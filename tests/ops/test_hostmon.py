"""Lab host sampler (shimpaku.hostmon) over a fake /sys and /proc: readings, retention, the guard."""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from shimpaku import hostmon


def w(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def fake_root(tmp: Path, *, coretemp=True) -> tuple[Path, Path]:
    sys_, proc = tmp / "sys", tmp / "proc"
    if coretemp:
        w(sys_ / "class/hwmon/hwmon3/name", "coretemp\n")
        w(sys_ / "class/hwmon/hwmon3/temp1_label", "Package id 0\n")
        w(sys_ / "class/hwmon/hwmon3/temp1_input", "95000\n")
        w(sys_ / "class/hwmon/hwmon3/temp1_crit", "100000\n")
        w(sys_ / "class/hwmon/hwmon3/temp2_label", "Core 0\n")
        w(sys_ / "class/hwmon/hwmon3/temp2_input", "93000\n")
    w(sys_ / "class/thermal/thermal_zone0/type", "acpitz\n")
    w(sys_ / "class/thermal/thermal_zone0/temp", "60000\n")
    w(sys_ / "class/thermal/thermal_zone1/type", "x86_pkg_temp\n")
    w(sys_ / "class/thermal/thermal_zone1/temp", "88000\n")
    w(sys_ / "class/hwmon/hwmon5/name", "dell_smm\n")
    w(sys_ / "class/hwmon/hwmon5/fan1_input", "4200\n")
    for i, (c, p) in enumerate([(10, 500), (20, 500)]):
        w(sys_ / f"devices/system/cpu/cpu{i}/thermal_throttle/core_throttle_count", f"{c}\n")
        w(sys_ / f"devices/system/cpu/cpu{i}/thermal_throttle/package_throttle_count", f"{p}\n")
    w(proc / "loadavg", "9.50 8.25 7.00 3/900 1234\n")
    w(proc / "meminfo", "MemTotal:       32000000 kB\nMemFree:  1000 kB\nMemAvailable:    8192000 kB\n")
    return sys_, proc


def test_sample_reads_package_throttle_load_mem_fans(tmp_path):
    s = hostmon.read_sample(*fake_root(tmp_path))
    assert (s["temp_c"], s["temp_source"], s["temp_crit_c"]) == (95.0, "coretemp", 100.0)
    assert s["throttle"] == {"core": 30, "package": 500}  # cores summed, package counted once
    assert s["load"] == [9.5, 8.25, 7.0] and s["ncpu"] == 2
    assert (s["mem_total_mb"], s["mem_avail_mb"]) == (31250, 8000)
    assert s["fans"] == [{"label": "dell_smm fan1", "rpm": 4200}]


def test_falls_back_to_x86_pkg_temp_zone_and_tolerates_missing_files(tmp_path):
    sys_, proc = fake_root(tmp_path, coretemp=False)
    assert hostmon.package_temp(sys_)[:2] == (88.0, "x86_pkg_temp")
    empty = hostmon.read_sample(tmp_path / "none", tmp_path / "none")
    assert empty["temp_c"] is None and empty["throttle"] is None and empty["load"] is None and empty["fans"] == []


def test_parse_virsh_list():
    text = (" Id   Name     State\n------------------------\n 1    r1       running\n 2    sw1      paused\n"
            " -    leaf2    shut off\n\n")
    assert hostmon.parse_virsh_list(text) == [
        {"id": "1", "name": "r1", "state": "running"}, {"id": "2", "name": "sw1", "state": "paused"},
        {"id": None, "name": "leaf2", "state": "shut off"}]


def test_store_prunes_to_24h_and_downsamples_throttle_deltas():
    st = hostmon.HostStore()
    now = time.time()
    st.add({"t": now - 25 * 3600, "temp_c": 50})
    for i, (temp, thr) in enumerate([(80, 100), (91, 160), (92, 40)]):  # 40 < 160: counters reset (reboot)
        st.add({"t": now - 30 + i * 10, "temp_c": temp, "load": [1, 1, 1], "mem_total_mb": 100, "mem_avail_mb": 25,
                "throttle": {"core": thr, "package": 0}})
    samples = st.samples()
    assert len(samples) == 3
    series = hostmon.downsample(samples)
    assert [p["throttle"] for p in series] == [None, 60, 0]
    assert series[-1]["mem_pct"] == 75.0


def test_guard_fires_once_after_hold_and_never_when_off(caplog):
    st = hostmon.HostStore()
    off = hostmon.ThermalGuard(st, enabled=False)
    assert not any(off.observe(t, 99) for t in range(0, 200, 15))
    g = hostmon.ThermalGuard(st, enabled=True)
    with caplog.at_level(logging.WARNING, "shimpaku.hostmon"):
        fired = [g.observe(t, 97.5) for t in range(0, 120, 15)]
    assert fired.count(True) == 1 and fired.index(True) == 4  # at t=60
    assert "NOT stopped" in caplog.text
    assert g.observe(130, 90) is False  # episode ends
    assert [g.observe(t, 98) for t in (140, 150)] == [False, False]  # a dip resets the 60 s hold
    ev = st.events()
    assert len(ev) == 1 and ev[0]["kind"] == "thermal_guard" and ev[0]["held_s"] == 60


def test_sampler_tick_records_vms_without_virsh(tmp_path, monkeypatch):
    sys_, proc = fake_root(tmp_path)

    async def fake_virsh(uri="qemu:///system", timeout_s=8.0):
        return {"vms": [{"id": "1", "name": "r1", "state": "running"},
                        {"id": None, "name": "r2", "state": "shut off"}], "error": None}
    monkeypatch.setattr(hostmon, "virsh_domains", fake_virsh)
    st = hostmon.HostStore()
    s = asyncio.run(hostmon.HostSampler(st, sys_root=sys_, proc_root=proc).tick())
    assert s["vms_running"] == 1 and st.vms["vms"][1]["state"] == "shut off"
    snap = hostmon.snapshot(st, guard_enabled=False)
    assert snap["latest"]["temp_c"] == 95.0 and snap["warn_c"] == 90.0 and snap["guard"]["enabled"] is False


def test_virsh_missing_is_an_error_not_a_crash(monkeypatch):
    monkeypatch.setenv("PATH", "/nonexistent")
    out = asyncio.run(hostmon.virsh_domains())
    assert out["vms"] == [] and "not installed" in out["error"]
