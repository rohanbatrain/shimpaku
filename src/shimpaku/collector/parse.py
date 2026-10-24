"""Parse gnmic `--format json` output (concatenated, pretty-printed JSON notifications) into rows.

Shapes (recorded from vJunos 26.2 + gnmic 0.49.1, tests/collector/fixtures):
- SAMPLE: {"source": node, "subscription-name", "timestamp": ns, "prefix": "openconfig:/interfaces/interface[name=X]",
  "updates": [{"Path": "state/counters/in-octets", "values": {"state/counters/in-octets": 1}}]}
- /junos/events: one notification per event, prefix ".../event[facility=..][id=ID][priority=..][type=..]" with
  updates id/message/daemon/timestamp/seconds/microseconds..., followed by one notification per attribute,
  prefix ".../event[...]/attributes[key=K]" with updates key/value. Each is preceded by a bare delete.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# Event ids we keep. The stream is dominated by our own sessions (UI_*, SYSTEM, AGENTD_*), R2 §2.4.
EVENT_ALLOW_EXACT = {
    "UI_COMMIT", "UI_COMMIT_COMPLETED", "UI_COMMIT_CONFIRMED", "UI_COMMIT_CONFIRMED_REMINDER",
    "UI_COMMIT_CONFIRMED_TIMED", "UI_COMMIT_NOT_CONFIRMED", "UI_COMMIT_ROLLBACK", "UI_ROLLBACK", "UI_LOAD_EVENT",
    "UI_CFG_AUDIT_SET", "UI_CFG_AUDIT_OTHER", "KERNEL", "LICENSE_EXPIRED", "LICENSE_VIOLATION",
}
EVENT_ALLOW_PREFIX = (
    "SNMP_TRAP_LINK_", "RPD_OSPF_", "RPD_BGP_", "BGP_", "BFDD_", "RPD_IFD_", "RPD_IFL_", "RPD_IFA_", "RPD_EVPN",
    "RPD_RT_", "RPD_SCHED_", "RPD_TASK_", "CHASSISD_", "ALARMD_", "LACPD_", "LLDP_", "DOT1XD_", "JDHCPD_", "DHCPD_",
    "L2ALD_", "ESWD_", "L2CPD_", "MIB2D_", "PFE_", "FPC_", "RPM_", "PING_", "TWAMP_", "DDOS_", "STP_", "VCCPD_",
    "JTASK_SCHED", "SNMPD_TRAP_COLD", "SNMPD_TRAP_WARM", "KERNEL_", "EVPN_", "VXLAN_", "MAC_",
)
EVENT_DENY_PREFIX = ("RPD_SCHED_SLIP_KEVENT",)

# OpenConfig interface counter leaf -> metric name (subset; per-queue and pause counters are dropped)
IF_COUNTERS = {
    "in-octets": "if.in_octets", "out-octets": "if.out_octets", "in-pkts": "if.in_pkts", "out-pkts": "if.out_pkts",
    "in-errors": "if.in_errors", "out-errors": "if.out_errors", "in-discards": "if.in_discards",
    "out-discards": "if.out_discards", "in-fcs-errors": "if.in_fcs_errors",
    "carrier-transitions": "if.carrier_transitions", "in-broadcast-pkts": "if.in_broadcast_pkts",
    "in-multicast-pkts": "if.in_multicast_pkts",
}
# interfaces always kept; physical ports are kept only when up or named in the topology
IF_ALWAYS = re.compile(r"^(irb|lo0|fxp0|ae\d+|vtep)$")
IF_PHYSICAL = re.compile(r"^(ge|xe|et|mge)-\d+/\d+/\d+$")

# RFC 2328 neighbour states, ordered; ExStart (4) stuck = MTU mismatch signature (R2 §5 F2)
OSPF_STATE_CODE = {"DOWN": 0.0, "ATTEMPT": 1.0, "INIT": 2.0, "TWO_WAY": 3.0, "2WAY": 3.0, "EXSTART": 4.0,
                   "EXCHANGE": 5.0, "LOADING": 6.0, "FULL": 7.0}
_KEY = re.compile(r"\[([^=\]]+)=([^\]]*)\]")


def allowed_event(eid: str) -> bool:
    if eid in EVENT_ALLOW_EXACT:
        return True
    if eid.startswith(EVENT_DENY_PREFIX):
        return False
    return eid.startswith(EVENT_ALLOW_PREFIX)


def keys(prefix: str) -> dict[str, str]:
    return dict(_KEY.findall(prefix))


def ns_to_dt(ns: int | float) -> datetime:
    return datetime.fromtimestamp(ns / 1e9, tz=timezone.utc).replace(tzinfo=None)


def _num(v: Any) -> float | None:
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v)
        except ValueError:
            return None
    return None


class JSONStream:
    """Incremental decoder for a stream of concatenated JSON values."""

    def __init__(self) -> None:
        self.buf = ""
        self._dec = json.JSONDecoder()

    def feed(self, data: str) -> list[Any]:
        self.buf += data
        out, i, n = [], 0, len(self.buf)
        while True:
            while i < n and self.buf[i] in " \t\r\n,":
                i += 1
            if i >= n:
                break
            try:
                obj, j = self._dec.raw_decode(self.buf, i)
            except json.JSONDecodeError:
                break  # incomplete value: wait for more bytes
            out.append(obj)
            i = j
        self.buf = self.buf[i:]
        return out


@dataclass
class _PendingEvent:
    node: str
    eid: str
    base: str
    gnmi_ts: int
    fields: dict
    attrs: dict = field(default_factory=dict)
    t_rx: float = field(default_factory=time.time)


class NodeParser:
    """Stateful parser for one node's gnmic stream. Feed notifications; collect metric and event rows."""

    def __init__(self, node: str, topo_ifaces: set[str] | None = None):
        self.node = node
        self.topo_ifaces = topo_ifaces or set()
        self.up_ifaces: set[str] = set()
        self.metrics: list[tuple] = []
        self.events: list[dict] = []
        self.pending: _PendingEvent | None = None
        self.last_sample_ts: float = 0.0     # device time of last SAMPLE notification (s)
        self.last_rx: float = 0.0            # host time of last notification
        self.dropped_events = 0
        self.sync = False
        self._mem: dict[str, float] = {}
        self._bfd_peer: dict[tuple, str] = {}

    # public
    def handle(self, obj: dict, rx: float | None = None) -> None:
        rx = rx if rx is not None else time.time()
        self.last_rx = rx
        if obj.get("sync-response"):
            self.sync = True
            return
        prefix = obj.get("prefix", "") or ""
        updates = obj.get("updates") or []
        if "/junos/events/event" in prefix:
            self._event(prefix, obj.get("timestamp", 0), updates, rx)
            return
        if not updates:
            return
        ts_ns = obj.get("timestamp") or int(rx * 1e9)
        self.last_sample_ts = max(self.last_sample_ts, ts_ns / 1e9)
        ts = ns_to_dt(ts_ns)
        p = prefix.split(":", 1)[-1]
        for u in updates:
            path = u.get("Path", "")
            val = next(iter((u.get("values") or {}).values()), None)
            self._sample(ts, f"{p.rstrip('/')}/{path}" if p != "/" else "/" + path, val)

    def flush_events(self, older_than_s: float = 0.0, now: float | None = None) -> None:
        now = now if now is not None else time.time()
        if self.pending and now - self.pending.t_rx >= older_than_s:
            self._emit(self.pending)
            self.pending = None

    def take(self) -> tuple[list[tuple], list[dict]]:
        m, e = self.metrics, self.events
        self.metrics, self.events = [], []
        return m, e

    # samples
    def _keep_iface(self, name: str) -> bool:
        if IF_ALWAYS.match(name) or name in self.topo_ifaces:
            return True
        return bool(IF_PHYSICAL.match(name)) and name in self.up_ifaces

    def _sample(self, ts: datetime, path: str, val: Any) -> None:
        k = keys(path)
        bare = _KEY.sub("", path)
        n = self.node
        if bare.startswith("/interfaces/interface/"):
            name = k.get("name", "")
            leaf = bare[len("/interfaces/interface/"):]
            if leaf == "state/oper-status":
                up = val == "UP"
                (self.up_ifaces.add if up else self.up_ifaces.discard)(name)
                if self._keep_iface(name):
                    self.metrics.append((ts, f"{n}:{name}", "if.oper_up", 1.0 if up else 0.0))
                return
            if leaf.startswith("state/counters/"):
                m = IF_COUNTERS.get(leaf[len("state/counters/"):])
                v = _num(val)
                if m and v is not None and self._keep_iface(name):
                    self.metrics.append((ts, f"{n}:{name}", m, v))
            return
        if bare.startswith("/system/cpus/cpu/state/"):
            leaf = bare[len("/system/cpus/cpu/state/"):]
            if leaf in ("user/instant", "kernel/instant", "idle/instant", "total/instant"):
                v = _num(val)
                if v is not None:
                    idx = k.get("index", "0")
                    ent = n if idx in ("0", "ALL") else f"{n}:cpu{idx}"
                    self.metrics.append((ts, ent, "cpu." + leaf.split("/")[0], v))
            return
        if bare.startswith("/system/memory/state/"):
            leaf = bare.rsplit("/", 1)[-1]
            v = _num(val)
            if leaf in ("used", "physical") and v is not None:
                self.metrics.append((ts, n, f"mem.{leaf}", v))
                self._mem[leaf] = v
                if self._mem.get("physical") and leaf == "used":
                    self.metrics.append((ts, n, "mem.used_pct", 100.0 * v / self._mem["physical"]))
            return
        if "/ospfv2/" in bare and bare.endswith(("/neighbors/neighbor/state/adjacency-state",
                                                 "/neighbors/neighbor/state/state-changes")):
            ifn = k.get("id", "?")
            if bare.endswith("adjacency-state"):
                self.metrics.append((ts, f"{n}:ospf:{ifn}", "ospf.full", 1.0 if val == "FULL" else 0.0))
                code = OSPF_STATE_CODE.get(str(val).upper().replace("-", "_"))
                if code is not None:
                    self.metrics.append((ts, f"{n}:ospf:{ifn}", "ospf.state", code))
            elif (v := _num(val)) is not None:
                self.metrics.append((ts, f"{n}:ospf:{ifn}", "ospf.state_changes", v))
            return
        if "/bgp/neighbors/neighbor/state/" in bare:
            peer = k.get("neighbor-address", "?")
            leaf = bare.split("/bgp/neighbors/neighbor/state/", 1)[1]
            if leaf == "session-state":
                self.metrics.append((ts, f"{n}:bgp:{peer}", "bgp.established", 1.0 if val == "ESTABLISHED" else 0.0))
            elif leaf == "established-transitions" and (v := _num(val)) is not None:
                self.metrics.append((ts, f"{n}:bgp:{peer}", "bgp.established_transitions", v))
            return
        if bare.startswith("/bfd/interfaces/interface/peers/peer/state/"):
            leaf = bare.rsplit("/state/", 1)[1]
            key = (k.get("id", "?"), k.get("local-discriminator", "?"))
            if leaf == "remote-address":
                self._bfd_peer[key] = str(val)
                return
            peer = self._bfd_peer.get(key, f"{key[0]}#{key[1]}")
            if leaf == "session-state":
                self.metrics.append((ts, f"{n}:bfd:{peer}", "bfd.up", 1.0 if val == "UP" else 0.0))
            elif leaf == "failure-transitions" and (v := _num(val)) is not None:
                self.metrics.append((ts, f"{n}:bfd:{peer}", "bfd.failure_transitions", v))
            return

    # events
    def _event(self, prefix: str, gnmi_ts: int, updates: list, rx: float) -> None:
        if not updates:
            return  # the bare delete that precedes every event/attribute
        p = prefix.split(":", 1)[-1]
        if "/attributes[" in p:
            base, attr = p.split("/attributes[", 1)
            kv = {}
            for u in updates:
                kv.update(u.get("values") or {})
            key = kv.get("key") or keys("[" + attr).get("key")
            if self.pending and self.pending.base == base and key:
                self.pending.attrs[key] = kv.get("value")
            return
        if self.pending:
            self._emit(self.pending)
        fields: dict = {}
        for u in updates:
            fields.update(u.get("values") or {})
        eid = fields.get("id") or keys(p).get("id", "UNKNOWN")
        self.pending = _PendingEvent(self.node, eid, p, gnmi_ts, fields, t_rx=rx)

    def _emit(self, pe: _PendingEvent) -> None:
        if not allowed_event(pe.eid):
            self.dropped_events += 1
            return
        f = pe.fields
        sec, usec = f.get("timestamp/seconds"), f.get("timestamp/microseconds") or 0
        ts = ns_to_dt(int(sec) * 1e9 + int(usec) * 1e3) if sec else ns_to_dt(pe.gnmi_ts)
        attrs = dict(pe.attrs)
        for k in ("message", "daemon", "priority", "facility", "hostname", "pid"):
            if k in f:
                attrs[k] = f[k]
        ifn = attrs.get("interface-name")
        if not ifn:
            m = re.search(r"ifName ([\w\-/.:]+)", str(f.get("message", "")))
            ifn = m.group(1) if m else None
        self.events.append({
            "ts": ts, "ingest_ts": ns_to_dt(pe.t_rx * 1e9), "source": "gnmi", "node": pe.node,
            "entity_id": f"{pe.node}:{ifn}" if ifn else pe.node, "client_id": None, "type": pe.eid,
            "attrs": attrs, "raw": str(f.get("message", ""))[:2000],
        })

