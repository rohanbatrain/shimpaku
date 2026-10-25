"""Lab host health (D1): a no-root sampler for the machine that runs the vJunos VMs.

Why: on 2026-10-25 the lab host (a laptop running 7 vJunos VMs under libvirt) crashed
from overheating: CPU package 94-98 C against a 100 C critical trip, ~150k thermal-throttle events.
This makes that visible before it happens again.

Every reading is an unprivileged file read or a ``virsh`` call on the ``qemu:///system`` socket
(needs the daemon user in the ``libvirt`` group; otherwise the VM list carries the error):

- CPU package temperature: hwmon ``coretemp`` ``temp*_label == "Package id 0"`` (millidegrees), else
  thermal zone type ``x86_pkg_temp``, else the hottest thermal zone (marked ``source``).
- Throttle counters: ``/sys/devices/system/cpu/cpu*/thermal_throttle/{core,package}_throttle_count``
  (package counters are per CPU but count the same package events, so the max is kept, cores summed).
- ``/proc/loadavg``, ``/proc/meminfo``, hwmon ``fan*_input`` (RPM; absent on many laptops).

Samples land in a small SQLite table pruned to 24 h. The optional thermal guard
(``SHIMPAKU_THERMAL_GUARD=1``, off by default) logs a warning and records an event when the package
stays at or above 97 C for 60 s. It never shuts VMs down: which VMs to stop (and whether losing a
lab mid-exercise is worse than throttling) is the owner's choice, and a daemon that kills VMs on a
noisy sensor reading would be its own outage. The event is the hook for that choice.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

log = logging.getLogger("shimpaku.hostmon")

RETENTION_S = 24 * 3600
WARN_C = 90.0
GUARD_C = 97.0
GUARD_HOLD_S = 60.0


def _read(p: Path) -> str | None:
    try:
        return p.read_text().strip()
    except OSError:
        return None


def _num(p: Path) -> float | None:
    v = _read(p)
    try:
        return float(v) if v is not None else None
    except ValueError:
        return None


def package_temp(sys_root: Path) -> tuple[float | None, str | None, float | None]:
    """(degrees C, source, critical C) for the CPU package."""
    for h in sorted((sys_root / "class/hwmon").glob("hwmon*")):
        if _read(h / "name") != "coretemp":
            continue
        for lab in sorted(h.glob("temp*_label")):
            if (_read(lab) or "").startswith("Package id"):
                base = lab.name[: -len("_label")]
                t = _num(h / f"{base}_input")
                if t is not None:
                    crit = _num(h / f"{base}_crit")
                    return round(t / 1000, 1), "coretemp", round(crit / 1000, 1) if crit else None
    zones = []
    for z in sorted((sys_root / "class/thermal").glob("thermal_zone*")):
        t = _num(z / "temp")
        if t is not None:
            zones.append((_read(z / "type") or z.name, t / 1000))
    for typ, t in zones:
        if typ == "x86_pkg_temp":
            return round(t, 1), "x86_pkg_temp", None
    if zones:
        typ, t = max(zones, key=lambda z: z[1])
        return round(t, 1), f"zone:{typ}", None
    return None, None, None


def throttle_counts(sys_root: Path) -> dict[str, int] | None:
    core, pkg, seen = 0, 0, False
    for d in (sys_root / "devices/system/cpu").glob("cpu[0-9]*/thermal_throttle"):
        c, p = _num(d / "core_throttle_count"), _num(d / "package_throttle_count")
        if c is not None:
            core += int(c); seen = True
        if p is not None:
            pkg = max(pkg, int(p)); seen = True
    return {"core": core, "package": pkg} if seen else None


def fans(sys_root: Path) -> list[dict]:
    out = []
    for h in sorted((sys_root / "class/hwmon").glob("hwmon*")):
        for f in sorted(h.glob("fan*_input")):
            rpm = _num(f)
            if rpm is not None:
                label = _read(h / f.name.replace("_input", "_label")) or f"{_read(h / 'name') or h.name} {f.name[:-6]}"
                out.append({"label": label, "rpm": int(rpm)})
    return out


def load_mem(proc_root: Path) -> dict[str, Any]:
    out: dict[str, Any] = {"load": None, "mem_total_mb": None, "mem_avail_mb": None}
    la = (_read(proc_root / "loadavg") or "").split()
    if len(la) >= 3:
        try:
            out["load"] = [float(x) for x in la[:3]]
        except ValueError:
            pass
    mi: dict[str, int] = {}
    for line in (_read(proc_root / "meminfo") or "").splitlines():
        k, _, v = line.partition(":")
        if v.strip().split() and v.strip().split()[0].isdigit():
            mi[k] = int(v.split()[0])
    if "MemTotal" in mi:
        out["mem_total_mb"] = mi["MemTotal"] // 1024
        out["mem_avail_mb"] = mi.get("MemAvailable", mi.get("MemFree", 0)) // 1024
    return out


def read_sample(sys_root: Path = Path("/sys"), proc_root: Path = Path("/proc")) -> dict[str, Any]:
    t, src, crit = package_temp(sys_root)
    ncpu = len(list((sys_root / "devices/system/cpu").glob("cpu[0-9]*")))
    return {"t": time.time(), "temp_c": t, "temp_source": src, "temp_crit_c": crit,
            "throttle": throttle_counts(sys_root), "fans": fans(sys_root), "ncpu": ncpu or None,
            **load_mem(proc_root)}


def parse_virsh_list(text: str) -> list[dict]:
    """``virsh list --all`` table: `` Id   Name   State`` then a dashed line, then rows."""
    out = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("Id") or set(s) <= {"-"}:
            continue
        parts = s.split(None, 2)
        if len(parts) == 3:
            out.append({"id": None if parts[0] == "-" else parts[0], "name": parts[1], "state": parts[2]})
    return out


async def virsh_domains(uri: str = "qemu:///system", timeout_s: float = 8.0) -> dict[str, Any]:
    try:
        proc = await asyncio.create_subprocess_exec(
            "virsh", "-c", uri, "list", "--all", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, err = await asyncio.wait_for(proc.communicate(), timeout_s)
    except FileNotFoundError:
        return {"vms": [], "error": "virsh is not installed"}
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        return {"vms": [], "error": "virsh timed out"}
    if proc.returncode != 0:
        msg = (err.decode(errors="replace").strip().splitlines() or ["virsh failed"])[-1][:200]
        return {"vms": [], "error": msg}
    return {"vms": parse_virsh_list(out.decode(errors="replace")), "error": None}


class HostStore:
    """Rolling 24 h of samples and guard events in ``<data>/host.db`` (or memory for tests)."""

    def __init__(self, path: Path | str | None = None):
        self._db = sqlite3.connect(str(path) if path else ":memory:", check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._db.execute("CREATE TABLE IF NOT EXISTS host_sample (t REAL PRIMARY KEY, body TEXT NOT NULL)")
            self._db.execute("CREATE TABLE IF NOT EXISTS host_event (t REAL NOT NULL, kind TEXT NOT NULL, body TEXT NOT NULL)")
            self._db.commit()
        self.vms: dict[str, Any] = {"vms": [], "error": "not sampled yet", "t": None}

    def add(self, sample: dict) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO host_sample VALUES (?, ?)", (sample["t"], json.dumps(sample)))
            cut = time.time() - RETENTION_S
            self._db.execute("DELETE FROM host_sample WHERE t < ?", (cut,))
            self._db.execute("DELETE FROM host_event WHERE t < ?", (cut,))
            self._db.commit()

    def event(self, kind: str, body: dict) -> None:
        with self._lock:
            self._db.execute("INSERT INTO host_event VALUES (?, ?, ?)", (time.time(), kind, json.dumps(body)))
            self._db.commit()

    def events(self, limit: int = 20) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT t, kind, body FROM host_event ORDER BY t DESC LIMIT ?", (limit,)).fetchall()
        return [{"t": t, "kind": k, **json.loads(b)} for t, k, b in rows]

    def samples(self, since_s: float = RETENTION_S) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT body FROM host_sample WHERE t >= ? ORDER BY t",
                                    (time.time() - since_s,)).fetchall()
        return [json.loads(r[0]) for r in rows]


def downsample(samples: list[dict], points: int = 288) -> list[dict]:
    """Chart series: temperature (max per bucket), load1, memory used %, throttle events per bucket."""
    if not samples:
        return []
    t0, t1 = samples[0]["t"], samples[-1]["t"]
    width = max(1.0, (t1 - t0) / points)
    buckets: dict[int, list[dict]] = {}
    for s in samples:
        buckets.setdefault(int((s["t"] - t0) // width), []).append(s)
    out, prev = [], None
    for k in sorted(buckets):
        b = buckets[k]
        temps = [s["temp_c"] for s in b if s.get("temp_c") is not None]
        loads = [s["load"][0] for s in b if s.get("load")]
        mems = [100 * (1 - s["mem_avail_mb"] / s["mem_total_mb"]) for s in b if s.get("mem_total_mb")]
        thr = [(s["throttle"] or {}).get("core", 0) + (s["throttle"] or {}).get("package", 0) for s in b if s.get("throttle")]
        last = thr[-1] if thr else None
        delta = None
        if last is not None and prev is not None:
            delta = max(0, last - prev)  # a reboot resets the counters: never negative
        if last is not None:
            prev = last
        out.append({"t": round(b[-1]["t"]), "temp_c": max(temps) if temps else None,
                    "load1": round(sum(loads) / len(loads), 2) if loads else None,
                    "mem_pct": round(sum(mems) / len(mems), 1) if mems else None, "throttle": delta})
    return out


class ThermalGuard:
    """Warn once per episode when the package is >= ``threshold`` for ``hold_s``. Never acts."""

    def __init__(self, store: HostStore, *, enabled: bool, threshold: float = GUARD_C, hold_s: float = GUARD_HOLD_S):
        self.store, self.enabled, self.threshold, self.hold_s = store, enabled, threshold, hold_s
        self._since: float | None = None
        self._fired = False

    def observe(self, t: float, temp: float | None) -> bool:
        if not self.enabled:
            return False
        if temp is None or temp < self.threshold:
            self._since, self._fired = None, False
            return False
        if self._since is None:
            self._since = t
        if not self._fired and t - self._since >= self.hold_s:
            self._fired = True
            log.warning("thermal guard: CPU package %.1f C >= %.0f C for %.0f s; VMs NOT stopped "
                        "(that is the owner's choice)", temp, self.threshold, t - self._since)
            self.store.event("thermal_guard", {"temp_c": temp, "threshold_c": self.threshold,
                                               "held_s": round(t - self._since)})
            return True
        return False


class HostSampler:
    def __init__(self, store: HostStore, *, interval_s: float = 15.0, guard: bool = False,
                 sys_root: Path = Path("/sys"), proc_root: Path = Path("/proc"), virsh_uri: str = "qemu:///system"):
        self.store, self.interval_s = store, interval_s
        self.guard = ThermalGuard(store, enabled=guard)
        self.sys_root, self.proc_root, self.virsh_uri = sys_root, proc_root, virsh_uri

    async def tick(self) -> dict:
        s = await asyncio.to_thread(read_sample, self.sys_root, self.proc_root)
        vms = await virsh_domains(self.virsh_uri)
        s["vms_running"] = sum(1 for v in vms["vms"] if v["state"] == "running")
        self.store.vms = {**vms, "t": s["t"]}
        await asyncio.to_thread(self.store.add, s)
        self.guard.observe(s["t"], s.get("temp_c"))
        return s

    async def run(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("host sample failed")
            await asyncio.sleep(self.interval_s)


def snapshot(store: HostStore, *, guard_enabled: bool, running: bool = True) -> dict:
    samples = store.samples()
    latest = samples[-1] if samples else None
    return {"demo": False, "sampler": running, "latest": latest, "series": downsample(samples),
            "vms": store.vms, "warn_c": WARN_C,
            "guard": {"enabled": guard_enabled, "threshold_c": GUARD_C, "hold_s": GUARD_HOLD_S,
                      "events": store.events()}}
