#!/usr/bin/env python3
"""Merge-load Junos `set` files onto lab devices over NETCONF, then check the campus works.

Runs on the lab host in Shimpaku's venv (junos-eznc comes with the `[junos]` extra):

    apply_sets.py --node r1=10.99.0.11 configs/campus/r1.set configs/services/r1.set \
                  [--node r2=... files...] [--wait 1800] [--assert-campus]

Per node, all files are loaded in ONE commit (r1's routing-engine filter and the gNMI term that
lets the collector through must land together), lock, merge, diff, `commit check`, commit. An
unchanged node gives an empty diff and no commit: re-running is a no-op. `#` lines are comments.

Credentials: LAB_USER / LAB_PASSWORD (the lab's published defaults, ops/lab-gen.py's boot
config: admin / admin@123). They are read from the environment, never from argv.
"""
from __future__ import annotations

import argparse
import os
import socket
import sys
import time
from pathlib import Path

USER = os.environ.get("LAB_USER", "admin")
PASSWORD = os.environ.get("LAB_PASSWORD", "admin@123")


def statements(files: list[Path]) -> str:
    out = []
    for f in files:
        for line in f.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                out.append(line)
    return "\n".join(out)


def wait_netconf(name: str, host: str, deadline: float) -> None:
    """vJunos takes 5-15 minutes to boot; NETCONF over SSH (830) answering is the signal."""
    last = 0.0
    while True:
        try:
            with socket.create_connection((host, 830), timeout=5) as s:
                s.settimeout(10)
                if s.recv(64).startswith(b"SSH-"):
                    return
        except OSError:
            pass
        now = time.time()
        if now > deadline:
            raise SystemExit(f"{name}: NETCONF ({host}:830) did not answer in time")
        if now - last > 60:
            print(f"  waiting for {name} ({host}:830) ...", flush=True)
            last = now
        time.sleep(10)


def device(host: str):
    from jnpr.junos import Device

    dev = Device(host=host, user=USER, password=PASSWORD, port=830, gather_facts=False)
    for attempt in range(1, 31):     # sshd answers before mgd accepts NETCONF sessions
        try:
            dev.open()
            dev.timeout = 120
            return dev
        except Exception as e:  # noqa: BLE001
            if attempt == 30:
                raise SystemExit(f"{host}: NETCONF login failed: {type(e).__name__}")
            time.sleep(20)


def apply(name: str, host: str, files: list[Path]) -> None:
    from jnpr.junos.utils.config import Config

    text = statements(files)
    dev = device(host)
    try:
        with Config(dev, mode="exclusive") as cu:
            cu.load(text, format="set", merge=True)
            diff = cu.diff()
            if not diff:
                print(f"{name}: no change (already applied)")
                return
            added = sum(1 for line in diff.splitlines() if line.startswith("+"))
            cu.commit_check()
            cu.commit(comment="shimpaku deploy-your-own")
            print(f"{name}: committed ({added} lines added)")
    finally:
        dev.close()


def assert_campus(nodes: dict[str, str], tries: int = 8, gap_s: float = 20.0) -> None:
    """r1 sees r2 Full over OSPF; sw1's users host (instance USER) reaches the servers gateway."""
    for attempt in range(1, tries + 1):
        fails = []
        if "r1" in nodes:
            dev = device(nodes["r1"])
            try:
                r = dev.rpc.get_ospf_neighbor_information()
                states = [n.findtext("ospf-neighbor-state", "").strip() for n in r.findall(".//ospf-neighbor")]
                if "Full" not in states:
                    fails.append(f"r1 OSPF neighbours {states or 'none'}")
            finally:
                dev.close()
        if "sw1" in nodes:
            dev = device(nodes["sw1"])
            try:
                r = dev.rpc.ping(host="10.20.0.1", count="2", rapid=True, routing_instance="USER")
                if r.find(".//ping-success") is None:
                    fails.append("sw1 users -> servers gateway ping failed")
            finally:
                dev.close()
        if not fails:
            print("  PASS  campus: r1-r2 OSPF Full; users reach the servers gateway")
            return
        print(f"  attempt {attempt}/{tries}: {'; '.join(fails)}", flush=True)
        time.sleep(gap_s)
    raise SystemExit("campus assertions failed")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--node", action="append", default=[], help="name=address, followed by its set files")
    ap.add_argument("--wait", type=float, default=1800, help="seconds to wait for NETCONF on each node")
    ap.add_argument("--assert-campus", action="store_true")
    ap.add_argument("--wait-only", action="store_true", help="only wait for NETCONF")
    args, _ = ap.parse_known_args()

    # files follow the --node they belong to, in argv order
    plan: list[tuple[str, str, list[Path]]] = []
    cur = None
    argv = sys.argv[1:]
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--node":
            name, host = argv[i + 1].split("=", 1)
            cur = (name, host, [])
            plan.append(cur)
            i += 2
            continue
        if a in ("--wait",):
            i += 2
            continue
        if a.startswith("--"):
            i += 1
            continue
        if cur is None:
            ap.error(f"{a}: a set file before any --node")
        cur[2].append(Path(a))
        i += 1
    for _, _, fs in plan:
        for f in fs:
            if not f.is_file():
                ap.error(f"no such set file: {f}")

    deadline = time.time() + args.wait
    for name, host, _ in plan:
        wait_netconf(name, host, deadline)
    if args.wait_only:
        return 0
    for name, host, fs in plan:
        if fs:
            apply(name, host, fs)
    if args.assert_campus:
        assert_campus({name: host for name, host, _ in plan})
    return 0


if __name__ == "__main__":
    sys.exit(main())
