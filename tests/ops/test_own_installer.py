"""deploy/own ("Deploy your own"): the pure logic of the installer, its set files and its scripts.

The installer's remote side (remote/ownlib.py) is stdlib-only and loaded here by path.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

OWN = Path(__file__).resolve().parents[2] / "deploy" / "own"
spec = importlib.util.spec_from_file_location("ownlib", OWN / "remote" / "ownlib.py")
ownlib = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ownlib)
GIB = 1024 ** 3


def fresh_facts(**over):
    """A bare-metal Debian 12 host with room for the full profile and nothing of the lab on it."""
    f = {
        "transport": "ssh", "uid": "0", "whoami": "root", "kernel": "Linux", "arch": "x86_64",
        "os_id": "debian", "os_version": "12", "os_pretty": "Debian GNU/Linux 12 (bookworm)",
        "cpu_virt": "vmx", "dev_kvm": "yes", "nested_module": "kvm_intel", "nested": "Y", "virt": "none",
        "dmi_vendor": "Dell Inc.", "dmi_product": "PowerEdge R640",
        "mem_available_kib": str(60 * 1024 * 1024), "threads": "32",
        "disk_path": "/home", "disk_free_kib": str(500 * 1024 * 1024), "data_dir": "/home/lab", "data_dir_exists": "no",
        "virsh_access": "yes", "lab_domains": "", "lab_domains_running": "", "other_vms_running": "0", "lab_networks": "",
        "lab_links": "", "nft_readable": "yes", "nft_tables": "inet filter", "routes": "192.168.1.0/24",
        "port_8780": "no", "hv_stacks": "", "pkg_mgr": "apt", "missing_tools": "", "python3_version": "3.11.2",
        "ntp_sync": "yes", "github": "yes", "pypi": "yes", "operator": "shimpaku", "operator_exists": "no",
        "operator_group_exists": "no", "pins_published": "yes",
    }
    for kind, size in (("router", 4050714624), ("switch", 4645126144)):
        f.update({f"image_{kind}_path": f"/root/vJunos-{kind}-26.2R1.7.qcow2", f"image_{kind}_state": "present",
                  f"image_{kind}_size": str(size), f"image_{kind}_format": "qcow2", f"image_{kind}_where": "on the host"})
    f.update({k: str(v) for k, v in over.items()})
    return f


def status(rep, check):
    return next(r["status"] for r in rep.rows if r["check"] == check)


# ---- profiles and resource maths ----------------------------------------------------------------

def test_profiles_match_the_running_lab():
    full = ownlib.profile_nodes("full")
    assert [n for n, *_ in full] == ["r1", "r2", "sw1", "spine1", "spine2", "leaf1", "leaf2"]
    assert {n: m for n, _, m, _ in full}["leaf2"] == "10.99.0.42"
    assert ownlib.images_needed("fabric") == ["switch"]
    assert ownlib.images_needed("campus") == ["router", "switch"]
    assert ownlib.bridges("full") == ["lab-mgmt", "lab-l1", "lab-l2", "lab-dc1", "lab-dc2", "lab-dc3", "lab-dc4"]
    with pytest.raises(ValueError):
        ownlib.profile_nodes("everything")


def test_catalogue_mgmt_map_agrees():
    text = (OWN.parents[1] / "bench" / "catalogue.yaml").read_text()
    lab = text.split("---", 1)[0]
    for n, _, mgmt, _ in ownlib.profile_nodes("full"):
        assert re.search(rf"\b{n}: {{mgmt: {re.escape(mgmt)}}}", lab), n


@pytest.mark.parametrize("profile,nodes,mem_gib,vcpu", [("full", 7, 39, 28), ("fabric", 4, 24, 16), ("campus", 3, 19, 12)])
def test_resource_maths(profile, nodes, mem_gib, vcpu):
    r = ownlib.resources(profile)
    assert r["nodes"] == nodes and r["vcpu"] == vcpu
    assert r["mem_fail_bytes"] == mem_gib * GIB                  # profile x 5 GiB + 4 GiB
    assert r["mem_warn_bytes"] == (mem_gib + 4) * GIB            # profile x 5 GiB + 8 GiB
    assert r["disk_fail_bytes"] == (2 * nodes + 5) * GIB          # 2 GiB per device + 5 GiB
    assert r["disk_warn_bytes"] == 2 * r["disk_fail_bytes"]


def test_resources_count_images_and_running_nodes():
    r = ownlib.resources("full", running_owned=3, image_bytes=8 * GIB)
    assert r["disk_fail_bytes"] == (8 + 14 + 5) * GIB
    assert r["mem_fail_bytes"] == (4 * 5 + 4) * GIB                 # 3 of ours already run
    assert ownlib.resources("full", running_owned=7)["mem_fail_bytes"] == 0


# ---- subnet overlap ----------------------------------------------------------------------------------

def test_overlap():
    mg = [ownlib.MGMT_SUBNET]
    assert ownlib.overlaps(["10.99.0.0/24"], mg) == [("10.99.0.0/24", "10.99.0.0/24")]
    assert ownlib.overlaps(["10.0.0.0/8"], mg)                      # a wide VPN route covers it
    assert ownlib.overlaps(["10.99.0.128/25"], mg)
    assert not ownlib.overlaps(["10.98.0.0/16", "192.168.1.0/24", "192.168.1.1", "default", "", "fe80::/64", "junk"], mg)
    assert ownlib.overlaps(["10.0.5.0/24"], ownlib.DEVICE_SUBNETS) == [("10.0.5.0/24", "10.0.0.0/16")]


# ---- the preflight table -------------------------------------------------------------------------------

def test_fresh_host_passes():
    rep = ownlib.evaluate(fresh_facts(), "full")
    assert rep.code() == 0, [r for r in rep.rows if r["status"] != "PASS"]


def test_vm_fails_unless_overridden():
    f = fresh_facts(virt="kvm", dmi_vendor="QEMU", dmi_product="Standard PC (Q35 + ICH9, 2009)")
    assert status(ownlib.evaluate(f, "full"), "Bare metal") == "FAIL"
    assert status(ownlib.evaluate(f, "full", allow_vm=True), "Bare metal") == "WARN"
    # DMI alone is enough (no systemd-detect-virt)
    assert status(ownlib.evaluate(fresh_facts(virt="unknown", dmi_vendor="VMware, Inc."), "full"), "Bare metal") == "FAIL"


def test_nested():
    assert status(ownlib.evaluate(fresh_facts(nested="N"), "full"), "Nested KVM") == "FAIL"
    assert status(ownlib.evaluate(fresh_facts(nested="N"), "full", enable_nested=True), "Nested KVM") == "WARN"


@pytest.mark.parametrize("avail_gib,want", [(38, "FAIL"), (39, "WARN"), (42, "WARN"), (43, "PASS")])
def test_memory_thresholds(avail_gib, want):
    assert status(ownlib.evaluate(fresh_facts(mem_available_kib=avail_gib * 1024 * 1024), "full"), "Memory") == want


def test_cpu_and_disk():
    assert status(ownlib.evaluate(fresh_facts(threads=6), "full"), "CPU") == "FAIL"
    assert status(ownlib.evaluate(fresh_facts(threads=12), "full"), "CPU") == "WARN"     # 28 vCPU > 24
    assert status(ownlib.evaluate(fresh_facts(threads=12), "campus"), "CPU") == "PASS"
    assert status(ownlib.evaluate(fresh_facts(disk_free_kib=18 * 1024 * 1024), "full"), "Disk") == "FAIL"
    assert status(ownlib.evaluate(fresh_facts(disk_free_kib=30 * 1024 * 1024), "full"), "Disk") == "WARN"


def test_conflicts_fail_and_owned_things_do_not():
    f = fresh_facts(lab_domains="lab-r1", lab_domains_running="lab-r1", lab_links="lab-mgmt", nft_tables="inet lab_iso",
                    routes="10.99.0.0/24", port_8780="yes", data_dir_exists="yes")
    rep = ownlib.evaluate(f, "full")
    assert status(rep, "Conflicts") == "FAIL"
    why = next(r["why"] for r in rep.rows if r["check"] == "Conflicts")
    for word in ("lab-r1", "lab-mgmt", "lab_iso", "10.99.0.0/24", "8780", "/home/lab"):
        assert word in why
    owned = {("domain", "lab-r1"), ("bridge", "lab-mgmt"), ("nft-table", "inet lab_iso"), ("user-unit", "shimpakud"),
             ("dir", "/home/lab")}
    assert status(ownlib.evaluate(f, "full", owned=owned), "Conflicts") == "PASS"     # a resumed deploy


def test_device_subnet_overlap_and_other_stacks_only_warn():
    assert status(ownlib.evaluate(fresh_facts(routes="10.0.0.0/24"), "full"), "Conflicts") == "WARN"
    assert status(ownlib.evaluate(fresh_facts(hv_stacks="proxmox"), "full"), "Conflicts") == "WARN"


def test_os_python_packages():
    assert status(ownlib.evaluate(fresh_facts(kernel="Darwin"), "full"), "OS") == "FAIL"
    assert status(ownlib.evaluate(fresh_facts(os_id="arch", os_version=""), "full"), "OS") == "WARN"
    assert status(ownlib.evaluate(fresh_facts(os_id="ubuntu", os_version="22.04", python3_version="3.10.12"), "full"),
                  "Python") == "FAIL"
    assert status(ownlib.evaluate(fresh_facts(pkg_mgr="none"), "full"), "Packages") == "FAIL"
    assert status(ownlib.evaluate(fresh_facts(missing_tools="virsh,mcopy"), "full"), "Packages") == "WARN"


def test_images():
    assert status(ownlib.evaluate(fresh_facts(image_router_path=""), "full"), "Image (router)") == "FAIL"
    assert status(ownlib.evaluate(fresh_facts(image_router_format="not-qcow2"), "full"), "Image (router)") == "FAIL"
    assert status(ownlib.evaluate(fresh_facts(image_router_size=10 ** 6), "full"), "Image (router)") == "FAIL"
    assert status(ownlib.evaluate(fresh_facts(image_router_path="/x/vJunos-router-25.4R1.12.qcow2"), "full"),
                  "Image (router)") == "WARN"
    rep = ownlib.evaluate(fresh_facts(image_router_path=""), "fabric")             # fabric needs no router
    assert not [r for r in rep.rows if r["check"] == "Image (router)"]
    other = "/home/lab/images/vJunos-switch-26.2R1.7.qcow2,/home/lab/images/vJunos-switch-24.2R1.qcow2"
    rep = ownlib.evaluate(fresh_facts(other_images=other), "full")
    assert "24.2R1" in next(r["why"] for r in rep.rows if r["check"] == "Images dir")
    assert "26.2R1.7" not in next(r["why"] for r in rep.rows if r["check"] == "Images dir")


def test_root_time_outbound_pins():
    assert status(ownlib.evaluate(fresh_facts(uid=1000, sudo="no"), "full"), "SSH + root") == "FAIL"
    assert status(ownlib.evaluate(fresh_facts(uid=1000, sudo="yes"), "full"), "SSH + root") == "PASS"
    assert status(ownlib.evaluate(fresh_facts(ntp_sync="no"), "full"), "Time") == "WARN"
    assert status(ownlib.evaluate(fresh_facts(pypi="no"), "full"), "Outbound") == "FAIL"
    assert status(ownlib.evaluate(fresh_facts(pins_published="no shimpaku e3ccd272"), "full"), "Pins") == "FAIL"
    assert status(ownlib.evaluate(fresh_facts(pins_published="http 403"), "full"), "Pins") == "WARN"


def test_exit_codes_and_rendering():
    assert ownlib.evaluate(fresh_facts(ntp_sync="no"), "full").code() == 1
    assert ownlib.evaluate(fresh_facts(arch="aarch64"), "full").code() == 2
    text = ownlib.render(ownlib.evaluate(fresh_facts(ntp_sync="no"), "full"))
    assert "WARN  Time" in text and "fix: timedatectl set-ntp true" in text and "1 warn, 0 fail" in text


def test_cli_evaluate_json(tmp_path):
    facts = "\n".join(f"{k}={v}" for k, v in fresh_facts(nested="N").items())
    p = subprocess.run(["python3", str(OWN / "remote" / "ownlib.py"), "evaluate", "--profile", "full", "--json"],
                       input=facts, capture_output=True, text=True)
    assert p.returncode == 2
    out = json.loads(p.stdout)
    assert out["exit"] == 2 and {"check", "status", "why", "fix"} <= set(out["checks"][0])


# ---- manifest (D6) ---------------------------------------------------------------------------------------

def test_manifest_round_trip(tmp_path):
    m = tmp_path / "manifest.json"
    assert ownlib.manifest_entries(str(m)) == []
    assert ownlib.manifest_add(str(m), "user", "shimpaku")
    assert ownlib.manifest_add(str(m), "dir", "/home/lab")
    assert ownlib.manifest_add(str(m), "domain", "lab-r1", {"topology": "day-02-switching"})
    assert not ownlib.manifest_add(str(m), "dir", "/home/lab")          # idempotent
    assert [e["name"] for e in ownlib.manifest_entries(str(m), reverse=True)] == ["lab-r1", "/home/lab", "shimpaku"]
    assert ownlib.owned_set(str(m)) == {("user", "shimpaku"), ("dir", "/home/lab"), ("domain", "lab-r1")}
    assert stat.S_IMODE(m.stat().st_mode) == 0o600
    data = json.loads(m.read_text())
    assert data["schema"] == "shimpaku-own/manifest/v1"
    assert data["created"][2]["extra"] == {"topology": "day-02-switching"}
    assert not list(tmp_path.glob(".manifest-*"))                       # atomic: no temp left


def test_manifest_cli(tmp_path):
    m = str(tmp_path / "manifest.json")
    lib = ["python3", str(OWN / "remote" / "ownlib.py")]
    subprocess.run(lib + ["manifest-add", m, "package", "mtools", "manager=apt"], check=True)
    subprocess.run(lib + ["manifest-add", m, "nft-table", "inet lab_iso"], check=True)
    assert subprocess.run(lib + ["manifest-has", m, "nft-table", "inet lab_iso"]).returncode == 0
    assert subprocess.run(lib + ["manifest-has", m, "domain", "lab-r1"]).returncode == 1
    out = subprocess.run(lib + ["manifest-list", m, "--reverse"], capture_output=True, text=True, check=True).stdout
    assert out.splitlines() == ["nft-table\tinet lab_iso\t{}", 'package\tmtools\t{"manager": "apt"}']


def test_manifest_rejects_foreign_json(tmp_path):
    m = tmp_path / "manifest.json"
    m.write_text('{"created": "nope"}')
    with pytest.raises(ValueError):
        ownlib.manifest_load(str(m))


def test_canonical_image():
    assert ownlib.canonical_image("router", "/dl/vJunos-router-26.2R1.7.qcow2") == "vJunos-router-26.2R1.7.qcow2"
    assert ownlib.canonical_image("switch", "my switch.qcow2") == "vJunos-switch-own.qcow2"


# ---- device configs -----------------------------------------------------------------------------------------

def test_set_files():
    for n, _, _, topo in ownlib.profile_nodes("full"):
        files = [OWN / "configs" / "services" / f"{n}.set"]
        if topo == "day-02-switching":
            files.append(OWN / "configs" / "campus" / f"{n}.set")
        for f in files:
            lines = [ln for ln in f.read_text().splitlines() if ln and not ln.startswith("#")]
            assert lines and all(ln.startswith("set ") for ln in lines), f
            text = f.read_text()
            # the boot config owns users and credentials; nothing secret or host-specific ships here
            assert not re.search(r"encrypted-password|plain-text|ssh-(ed25519|rsa)|system login|host-name", text), f
            addrs = re.findall(r"\b\d+\.\d+\.\d+\.\d+\b", text)
            assert all(x.startswith("10.") or x == "0.0.0.0" for x in addrs), f       # lab addressing only
        assert "server shimpaku-gnmi grpc gnmi" in files[0].read_text()


# ---- scripts --------------------------------------------------------------------------------------------------

SCRIPTS = ["install.sh", "get.sh", "remote/preflight.sh", "remote/deploy.sh", "remote/uninstall.sh"]


@pytest.mark.parametrize("script", SCRIPTS)
def test_bash_syntax(script):
    subprocess.run(["bash", "-n", str(OWN / script)], check=True)


@pytest.mark.skipif(not shutil.which("shellcheck"), reason="shellcheck not installed")
def test_shellcheck():
    subprocess.run(["shellcheck", *[str(OWN / s) for s in SCRIPTS]], check=True)


def test_deploy_dry_run_records_before_it_creates():
    env = {**os.environ, "LC_ALL": "C"}
    out = ""
    for stage in ("base", "rest"):
        out += subprocess.run(["bash", str(OWN / "remote" / "deploy.sh"), "--stage", stage, "--dry-run", "--profile", "full",
                               "--upload", "switch=vJunos-switch-26.2R1.7.qcow2:0", "--selftest"],
                              capture_output=True, text=True, check=True, env=env).stdout
    lines = [ln for ln in out.splitlines() if ln.startswith("+ ")]      # commands and manifest records only

    def first(pat):
        return next(i for i, ln in enumerate(lines) if re.search(pat, ln))

    assert first(r"manifest \+= user shimpaku") < first(r"useradd ")
    assert first(r"manifest \+= dir /home/lab") < first(r"lab-host-setup\.sh")
    assert first(r"manifest \+= domain lab-r1") < first(r"lab\.sh up lab/topologies/day-02")
    assert first(r"manifest \+= domain lab-leaf2") < first(r"lab\.sh up lab/topologies/day-06")
    assert first(r"manifest \+= nft-table inet lab_iso") < first(r"lab-net-up\.sh")
    assert first(r"lab\.sh up lab/topologies/day-02") < first(r"apply_sets\.py .*--assert-campus")
    assert re.search(r"gen_fabric\.py lab/topologies/day-06-datacenter\.toml && \S+ day-07/automation/deploy\.py", out)
    assert first(r"manifest \+= user-unit shimpakud") < first(r"deploy/systemd/install\.sh")
    assert "virga --have day-06-fabric selftest --only FAB-01" in out
    assert "admin@123" not in out                  # env values are never printed, only their keys
    assert "git fetch -q --depth 1 origin 3e1cf694208942cce36b0c12f2efb3c65db96d1d" in out


def test_bootstrap_shape():
    text = (OWN / "get.sh").read_text()
    code = [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    assert code[-1] == 'main "$@"'                       # a cut-short download defines, never runs
    assert 'SHIMPAKU_REF="${SHIMPAKU_REF:-__SHIMPAKU_PIN__}"' in text
    assert "codeload.github.com/rohanbatrain/shimpaku/tar.gz" in text
    assert '</dev/null' in text.split("exec bash", 1)[1].splitlines()[0]   # prompts come from /dev/tty


def test_install_has_no_bash4_only_constructs():
    """install.sh and get.sh run on macOS's bash 3.2."""
    for name in ("install.sh", "get.sh"):
        text = (OWN / name).read_text()
        for bad in ("declare -A", "mapfile", "readarray", "${@,,}", ",,}", "^^}", "&>>", "|&", "coproc"):
            assert bad not in text, (name, bad)
