#!/usr/bin/env python3
"""Pure logic for the "Deploy your own" installer (docs/build/deploy-your-own.md).

Runs on the target host with the system python3 (stdlib only; kept to Python 3.6 syntax so the
preflight can still report a FAIL on an old host instead of crashing). Nothing here touches the
system except the manifest file it is pointed at.

    ownlib.py evaluate --profile full [--json] [--allow-vm] [--enable-nested] [--manifest P]
                       < facts                     preflight verdict; exit 0 pass, 1 warn, 2 fail
    ownlib.py plan --profile full                  the resource lines of the plan
    ownlib.py topologies --profile full            topology stems, one per line
    ownlib.py nodes --profile full                 "<node> <mgmt ip> <topology> <kind>" lines
    ownlib.py bridges --profile P | --topology T   the lab bridges, one per line
    ownlib.py manifest-add P KIND NAME [k=v ...]   record one created thing (idempotent, atomic)
    ownlib.py manifest-has P KIND NAME             exit 0 when recorded
    ownlib.py manifest-list P [--reverse]          "KIND<TAB>NAME<TAB>{json extra}" lines

Facts are "key=value" lines written by remote/preflight.sh (and, for local image files, by
install.sh); unknown keys are ignored and a missing key reads as "unknown".
"""
from __future__ import print_function

import ipaddress
import json
import os
import re
import sys
import tempfile
import time

GIB = 1024 ** 3
NODE_MEM_MIB = 5120          # every node in both topologies: 4 vCPU / 5120 MiB
NODE_VCPU = 4
DISK_PER_NODE_GIB = 2        # measured: 0.75-1.1 GiB per overlay after weeks of drills
DISK_SLACK_GIB = 5
MEM_RESERVE_FAIL_GIB = 4     # lab-gen.py's own reserve_mb default is 4096
MEM_RESERVE_WARN_GIB = 8
MIN_THREADS = 8
TESTED_VERSION = "26.2R1.7"
MIN_IMAGE_BYTES = 1 * GIB    # the 26.2R1.7 images are 3.8 and 4.3 GiB

TOPOLOGIES = {
    "day-02-switching": {
        "site": "campus",
        "nodes": [("r1", "router", "10.99.0.11"), ("r2", "router", "10.99.0.12"),
                  ("sw1", "switch", "10.99.0.21")],
        "bridges": ["lab-mgmt", "lab-l1", "lab-l2"],
    },
    "day-06-datacenter": {
        "site": "fabric",
        "nodes": [("spine1", "switch", "10.99.0.31"), ("spine2", "switch", "10.99.0.32"),
                  ("leaf1", "switch", "10.99.0.41"), ("leaf2", "switch", "10.99.0.42")],
        "bridges": ["lab-mgmt", "lab-dc1", "lab-dc2", "lab-dc3", "lab-dc4"],
    },
}
PROFILES = {
    "full": ["day-02-switching", "day-06-datacenter"],
    "fabric": ["day-06-datacenter"],
    "campus": ["day-02-switching"],
}
# The lab's own address plan. Only the management subnet is ever configured on the host
# (10.99.0.1 on lab-mgmt); the rest live inside the devices and their point-to-point bridges.
MGMT_SUBNET = "10.99.0.0/24"
DEVICE_SUBNETS = ["10.0.0.0/16", "10.10.0.0/24", "10.20.0.0/24", "10.100.0.0/24", "10.255.0.0/24"]
LAB_PORT = 8780
NFT_TABLES = ["inet lab_iso"]
OS_TESTED = {"debian": ("11", "12", "13"), "ubuntu": ("20.04", "22.04", "24.04")}
OS_ALLOWED_WARN = ("arch", "fedora")
PACKAGE_MANAGERS = ("apt", "pacman", "dnf")


# ---- profiles and resources ----------------------------------------------------------------

def profile_nodes(profile):
    """[(node, kind, mgmt, topology)] for a profile, in bring-up order."""
    if profile not in PROFILES:
        raise ValueError("unknown profile %r (full, fabric, campus)" % profile)
    out = []
    for topo in PROFILES[profile]:
        for name, kind, mgmt in TOPOLOGIES[topo]["nodes"]:
            out.append((name, kind, mgmt, topo))
    return out


def images_needed(profile):
    kinds = sorted({k for _, k, _, _ in profile_nodes(profile)})
    return kinds  # ["router", "switch"] or ["switch"]


def bridges(profile):
    seen = []
    for topo in PROFILES[profile]:
        for b in TOPOLOGIES[topo]["bridges"]:
            if b not in seen:
                seen.append(b)
    return seen


def resources(profile, running_owned=0, image_bytes=0):
    """What a profile needs. `running_owned` nodes of ours already run (a resumed deploy), so
    their memory is already spent and not available again; `image_bytes` still to be copied."""
    n = len(profile_nodes(profile))
    to_start = max(n - running_owned, 0)
    mem_fail = to_start * NODE_MEM_MIB * 1024 ** 2 + (MEM_RESERVE_FAIL_GIB * GIB if to_start else 0)
    mem_warn = to_start * NODE_MEM_MIB * 1024 ** 2 + (MEM_RESERVE_WARN_GIB * GIB if to_start else 0)
    disk_fail = image_bytes + DISK_PER_NODE_GIB * GIB * n + DISK_SLACK_GIB * GIB
    return {
        "nodes": n, "vcpu": n * NODE_VCPU, "memory_mib": n * NODE_MEM_MIB,
        "mem_fail_bytes": mem_fail, "mem_warn_bytes": mem_warn,
        "disk_fail_bytes": disk_fail, "disk_warn_bytes": 2 * disk_fail,
    }


def gib(b):
    return "%.1f GiB" % (float(b) / GIB)


# ---- subnets -----------------------------------------------------------------------------------

def overlaps(routes, subnets):
    """Host routes (CIDR strings; "default" ignored) that overlap any of `subnets`.
    Returns [(route, subnet)]."""
    hits = []
    nets = [ipaddress.ip_network(s) for s in subnets]
    for r in routes:
        r = r.strip()
        if not r or r == "default":
            continue
        try:
            rn = ipaddress.ip_network(r, strict=False)
        except ValueError:
            continue
        if rn.version != 4 or rn.prefixlen == 0:
            continue
        for n in nets:
            if rn.overlaps(n):
                hits.append((str(rn), str(n)))
    return hits


# ---- facts ---------------------------------------------------------------------------------------

def parse_facts(text):
    facts = {}
    for line in text.splitlines():
        if "=" not in line or line.startswith("#"):
            continue
        k, v = line.split("=", 1)
        facts[k.strip()] = v.strip()
    return facts


def _list(v):
    return [x for x in (v or "").replace(";", ",").split(",") if x]


def _int(v, default=None):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _ver(v):
    return tuple(_int(x, 0) for x in re.findall(r"\d+", v or "")[:3])


# ---- the preflight table ---------------------------------------------------------------------------

class Report(object):
    def __init__(self):
        self.rows = []

    def add(self, check, status, why, fix=""):
        self.rows.append({"check": check, "status": status, "why": why, "fix": fix})

    def code(self):
        st = {r["status"] for r in self.rows}
        return 2 if "FAIL" in st else 1 if "WARN" in st else 0


def evaluate(facts, profile, allow_vm=False, enable_nested=False, owned=None):
    """The §2 preflight table. `owned` = {(kind, name)} from this installer's manifest: those
    are a resumed deploy, not conflicts."""
    owned = owned or set()
    f = facts.get
    rep = Report()
    nodes = profile_nodes(profile)
    want_domains = ["lab-" + n for n, _, _, _ in nodes]

    # SSH + root
    if f("uid") == "0":
        rep.add("SSH + root", "PASS", "%s as root" % ("local" if f("transport") == "local" else "logged in"))
    elif f("sudo") == "yes":
        rep.add("SSH + root", "PASS", "logged in as %s, passwordless sudo works" % f("whoami", "?"))
    else:
        rep.add("SSH + root", "FAIL", "logged in as %s without root or passwordless sudo; the deploy needs root"
                % f("whoami", "?"), "log in as root, or --user a sudo-capable user (the installer offers to authorize its key for root)")

    # OS
    kernel, os_id, ver = f("kernel", "?"), f("os_id", "?"), f("os_version", "")
    pretty = f("os_pretty") or ("%s %s" % (os_id, ver)).strip()
    if kernel != "Linux":
        rep.add("OS", "FAIL", "%s is not Linux" % kernel, "vJunos runs on a Linux KVM host")
    elif os_id in OS_TESTED and ver in OS_TESTED[os_id]:
        rep.add("OS", "PASS", pretty)
    elif os_id in OS_TESTED or os_id in OS_ALLOWED_WARN:
        rep.add("OS", "WARN", "%s: outside Juniper's list (Ubuntu 18.04-22.04, Debian 11) and this installer's "
                "tested set; expect to fix small things by hand" % pretty)
    else:
        rep.add("OS", "WARN", "%s: not a distribution this installer knows" % pretty,
                "Debian 12/13 or Ubuntu 24.04 is the smooth path")

    # CPU arch
    arch = f("arch", "?")
    rep.add("CPU arch", "PASS" if arch == "x86_64" else "FAIL", arch,
            "" if arch == "x86_64" else "vJunos images are x86_64 only")

    # Virtualisation
    virt_flag, kvm = f("cpu_virt", "none"), f("dev_kvm", "no")
    if virt_flag in ("vmx", "svm") and kvm == "yes":
        rep.add("Virtualisation", "PASS", "%s flag, /dev/kvm present" % virt_flag)
    else:
        rep.add("Virtualisation", "FAIL", "cpu flag %s, /dev/kvm %s" % (virt_flag, "present" if kvm == "yes" else "missing"),
                "enable VT-x/AMD-V in the firmware and load kvm_intel/kvm_amd")

    # Nested KVM
    nested, mod = f("nested", ""), f("nested_module", "") or "kvm_intel/kvm_amd"
    if nested in ("Y", "y", "1"):
        rep.add("Nested KVM", "PASS", "%s nested=%s" % (mod, nested))
    elif enable_nested and virt_flag in ("vmx", "svm"):
        rep.add("Nested KVM", "WARN", "%s nested=%s; --enable-nested will turn it on (module reload, or a reboot if VMs run)"
                % (mod, nested or "?"))
    else:
        rep.add("Nested KVM", "FAIL", "%s nested=%s: the vJunos images boot a VM inside the VM" % (mod, nested or "unknown"),
                "re-run with --enable-nested (writes /etc/modprobe.d/kvm-nested.conf)")

    # Bare metal (D4)
    virt = f("virt", "unknown")
    vendor = "%s %s" % (f("dmi_vendor", ""), f("dmi_product", ""))
    vm_dmi = re.search(r"QEMU|KVM|VMware|VirtualBox|Xen|Microsoft Corporation Virtual|Bochs|Parallels|Google Compute|Amazon EC2|OpenStack",
                       vendor, re.I)
    if virt in ("none", "") and not vm_dmi:
        rep.add("Bare metal", "PASS", "systemd-detect-virt: none")
    elif virt == "unknown" and not vm_dmi:
        rep.add("Bare metal", "WARN", "could not tell (no systemd-detect-virt, DMI '%s')" % vendor.strip())
    else:
        why = "this host is a VM (%s%s): Juniper does not support vJunos launched from within a VM" % (
            virt if virt not in ("none", "unknown") else "", (" / " + vendor.strip()) if vm_dmi else "")
        if allow_vm:
            rep.add("Bare metal", "WARN", why + "; continuing because of --i-know-this-is-a-vm")
        else:
            rep.add("Bare metal", "FAIL", why, "use a bare-metal host, or --i-know-this-is-a-vm to try anyway")

    # Memory
    running_owned = len([d for d in _list(f("lab_domains_running")) if ("domain", d) in owned])
    res = resources(profile, running_owned, _int(f("image_copy_bytes"), 0))
    avail = _int(f("mem_available_kib"))
    if avail is None:
        rep.add("Memory", "FAIL", "MemAvailable unreadable")
    else:
        a = avail * 1024
        need = "%d nodes x 5 GiB + %d GiB" % (res["nodes"] - running_owned, MEM_RESERVE_FAIL_GIB)
        if a < res["mem_fail_bytes"]:
            rep.add("Memory", "FAIL", "%s available, need %s = %s" % (gib(a), need, gib(res["mem_fail_bytes"])),
                    "free memory, or a smaller --profile (fabric 4 nodes, campus 3)")
        elif a < res["mem_warn_bytes"]:
            rep.add("Memory", "WARN", "%s available; %s needed, less than 8 GiB to spare" % (gib(a), gib(res["mem_fail_bytes"])))
        else:
            rep.add("Memory", "PASS", "%s available, %s needed" % (gib(a), gib(res["mem_fail_bytes"])))

    # CPU
    threads = _int(f("threads"), 0)
    if threads < MIN_THREADS:
        rep.add("CPU", "FAIL", "%d threads, need >= %d" % (threads, MIN_THREADS))
    elif res["vcpu"] > 2 * threads:
        rep.add("CPU", "WARN", "%d vCPU on %d threads: more than 2x overcommit, boots will be slow" % (res["vcpu"], threads))
    else:
        rep.add("CPU", "PASS", "%d threads for %d vCPU" % (threads, res["vcpu"]))

    # Disk
    free = _int(f("disk_free_kib"))
    if free is None:
        rep.add("Disk", "FAIL", "free space at %s unreadable" % f("disk_path", "?"))
    else:
        fb = free * 1024
        if fb < res["disk_fail_bytes"]:
            rep.add("Disk", "FAIL", "%s free at %s, need %s" % (gib(fb), f("disk_path", "?"), gib(res["disk_fail_bytes"])))
        elif fb < res["disk_warn_bytes"]:
            rep.add("Disk", "WARN", "%s free at %s, need %s (less than 2x headroom)" % (gib(fb), f("disk_path", "?"), gib(res["disk_fail_bytes"])))
        else:
            rep.add("Disk", "PASS", "%s free at %s, need %s" % (gib(fb), f("disk_path", "?"), gib(res["disk_fail_bytes"])))

    # Conflicts
    fails, warns = [], []
    doms = [d for d in _list(f("lab_domains")) if ("domain", d) not in owned]
    if doms:
        fails.append("libvirt domains %s exist" % ",".join(doms))
    nets = _list(f("lab_networks"))
    if nets:
        fails.append("libvirt networks %s exist" % ",".join(nets))
    links = [b for b in _list(f("lab_links")) if ("bridge", b) not in owned]
    if links:
        fails.append("interfaces %s exist" % ",".join(links))
    tables = [t for t in _list(f("nft_tables")) if t in NFT_TABLES and ("nft-table", t) not in owned]
    if tables:
        fails.append("nft table %s exists" % ",".join(tables))
    elif f("nft_readable") == "no":
        warns.append("nft tables unreadable without root")
    routes = _list(f("routes"))
    if ("bridge", "lab-mgmt") in owned:      # our own lab-mgmt carries 10.99.0.0/24 on a resumed deploy
        routes = [r for r in routes if r != MGMT_SUBNET]
    mg = overlaps(routes, [MGMT_SUBNET])
    if mg:
        fails.append("host route %s overlaps the lab management subnet %s" % (mg[0][0], MGMT_SUBNET))
    dv = overlaps(routes, DEVICE_SUBNETS)
    if dv:
        warns.append("host route %s overlaps lab device subnet %s (inside the devices only; the lab cannot reach it)"
                     % (dv[0][0], dv[0][1]))
    if f("port_%d" % LAB_PORT) == "yes" and ("user-unit", "shimpakud") not in owned:
        fails.append("port %d is in use" % LAB_PORT)
    data = f("data_dir", "/home/lab")
    if f("data_dir_exists") == "yes" and ("dir", data) not in owned:
        fails.append("%s exists (the lab toolchain would take it over)" % data)
    op = f("operator", "shimpaku")
    if f("operator_exists") == "yes" and ("user", op) not in owned:
        fails.append("user %s exists (pick another with --operator)" % op)
    elif f("operator_group_exists") == "yes" and ("user", op) not in owned:
        fails.append("group %s exists (pick another with --operator)" % op)
    others = _int(f("other_vms_running"), 0)
    stacks = [s for s in _list(f("hv_stacks"))]
    if stacks:
        warns.append("another hypervisor stack is present (%s)" % ",".join(stacks))
    if others:
        warns.append("%d other VM(s) running" % others)
    if f("virsh_access") == "no":
        warns.append("libvirt not readable by this user: domain check incomplete")
    if fails:
        rep.add("Conflicts", "FAIL", "; ".join(fails), "this installer never touches what it did not create: remove them, or use another host")
    elif warns:
        rep.add("Conflicts", "WARN", "; ".join(warns))
    else:
        rep.add("Conflicts", "PASS", "no lab-* domains, bridges, nft table, route or port %d in use" % LAB_PORT)

    # Packages
    pm = f("pkg_mgr", "none")
    missing = _list(f("missing_tools"))
    if pm not in PACKAGE_MANAGERS:
        rep.add("Packages", "FAIL", "no apt, pacman or dnf", "install qemu, libvirt, dosfstools, mtools, python3, git by hand")
    elif missing:
        rep.add("Packages", "WARN", "%s will install: %s" % (pm, ",".join(missing)))
    else:
        rep.add("Packages", "PASS", "%s; every tool present" % pm)

    # Python (the lab toolchain runs the system python3 and needs tomllib, 3.11+)
    pyv = f("python3_version", "")
    if not pyv and pm in PACKAGE_MANAGERS:
        rep.add("Python", "WARN", "python3 missing; %s will install it (needs >= 3.11)" % pm)
    elif not pyv:
        rep.add("Python", "FAIL", "python3 missing")
    elif _ver(pyv) < (3, 11):
        rep.add("Python", "FAIL", "system python3 is %s; the lab toolchain (ops/lab.sh, lab-gen.py) needs >= 3.11 (tomllib)" % pyv,
                "Debian 12+/Ubuntu 24.04 ship 3.11+; Shimpaku itself gets its own 3.12 via uv")
    else:
        rep.add("Python", "PASS", "python3 %s" % pyv)

    # Images
    for kind in images_needed(profile):
        p = f("image_%s_path" % kind, "")
        state = f("image_%s_state" % kind, "missing")
        size = _int(f("image_%s_size" % kind), 0)
        fmt = f("image_%s_format" % kind, "")
        label = "Image (%s)" % kind
        if not p:
            rep.add(label, "FAIL", "no --%s-image given" % kind,
                    "download vJunos-%s %s (free, Juniper account) and pass --%s-image FILE" % (kind, TESTED_VERSION, kind))
        elif state != "present":
            rep.add(label, "FAIL", "%s: not found (%s)" % (p, f("image_%s_where" % kind, "?")))
        elif fmt != "qcow2":
            rep.add(label, "FAIL", "%s is %s, not qcow2" % (p, fmt or "unknown"))
        elif size < MIN_IMAGE_BYTES:
            rep.add(label, "FAIL", "%s is only %s: truncated download?" % (p, gib(size)))
        else:
            m = re.search(r"(\d+\.\d+R\d+(?:-S\d+)?\.\d+)", os.path.basename(p))
            v = m.group(1) if m else "unknown"
            if v != TESTED_VERSION:
                rep.add(label, "WARN", "%s (%s, version %s; tested %s)" % (os.path.basename(p), gib(size), v, TESTED_VERSION))
            else:
                rep.add(label, "PASS", "%s (%s, qcow2, %s)" % (os.path.basename(p), gib(size), f("image_%s_where" % kind, "")))
    other = _list(f("other_images"))
    if other:
        rep.add("Images dir", "WARN", "other vJunos images in the lab image dir (%s): lab-gen.py picks the last by name"
                % ",".join(other))

    # Time
    ntp = f("ntp_sync", "unknown")
    if ntp == "yes":
        rep.add("Time", "PASS", "NTP synchronised")
    else:
        rep.add("Time", "WARN", "NTP %s: telemetry timestamps may drift" % ("not synchronised" if ntp == "no" else "state unknown"),
                "timedatectl set-ntp true")

    # Outbound
    down = [h for h, k in (("github.com", "github"), ("pypi.org", "pypi")) if f(k, "no") != "yes"]
    if down:
        rep.add("Outbound", "FAIL", "%s:443 unreachable" % " and ".join(down),
                "the repos are cloned and Shimpaku's Python packages installed on the host: allow outbound HTTPS")
    else:
        rep.add("Outbound", "PASS", "github.com and pypi.org reachable on 443")

    # Pins (checked from the visitor's machine)
    pins = f("pins_published", "")
    if pins == "yes":
        rep.add("Pins", "PASS", "pinned commits are published")
    elif pins.startswith("no"):
        rep.add("Pins", "FAIL", "pinned commit not on GitHub: %s" % pins[3:], "update deploy/own/PINS or wait for the push")
    elif pins:
        rep.add("Pins", "WARN", "could not verify the pinned commits (%s)" % pins)
    return rep


# ---- rendering --------------------------------------------------------------------------------------

COLOURS = {"PASS": "\033[32m", "WARN": "\033[33m", "FAIL": "\033[31m"}


def render(rep, colour=False):
    w = max(len(r["check"]) for r in rep.rows) if rep.rows else 10
    out = []
    for r in rep.rows:
        st = r["status"]
        tag = (COLOURS[st] + st + "\033[0m") if colour else st
        out.append("  %s  %-*s  %s" % (tag, w, r["check"], r["why"]))
        if r["fix"] and st != "PASS":
            out.append("  %s  %-*s  fix: %s" % ("    ", w, "", r["fix"]))
    n = {s: sum(1 for r in rep.rows if r["status"] == s) for s in ("PASS", "WARN", "FAIL")}
    out.append("")
    out.append("  %d pass, %d warn, %d fail" % (n["PASS"], n["WARN"], n["FAIL"]))
    return "\n".join(out)


def plan_lines(profile):
    res = resources(profile)
    topo = PROFILES[profile]
    lines = [
        "profile %s: %d vJunos devices (%s), %d vCPU, %d GiB RAM" % (
            profile, res["nodes"], " ".join(n for n, _, _, _ in profile_nodes(profile)), res["vcpu"],
            res["memory_mib"] // 1024),
        "topologies: %s" % ", ".join(topo),
        "domains: %s" % " ".join("lab-" + n for n, _, _, _ in profile_nodes(profile)),
        "bridges: %s; nft table inet lab_iso" % " ".join(bridges(profile)),
    ]
    return lines


# ---- manifest (D6) --------------------------------------------------------------------------------

def manifest_load(path):
    try:
        with open(path) as fh:
            m = json.load(fh)
    except (IOError, OSError):
        return {"schema": "shimpaku-own/manifest/v1", "created": []}
    if not isinstance(m.get("created"), list):
        raise ValueError("%s: not a shimpaku-own manifest" % path)
    return m


def manifest_save(path, m):
    d = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(prefix=".manifest-", dir=d)
    with os.fdopen(fd, "w") as fh:
        json.dump(m, fh, indent=1, sort_keys=True)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.chmod(tmp, 0o600)
    os.rename(tmp, path)


def manifest_add(path, kind, name, extra=None):
    m = manifest_load(path)
    for e in m["created"]:
        if e["kind"] == kind and e["name"] == name:
            return False
    e = {"kind": kind, "name": name, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    if extra:
        e["extra"] = extra
    m["created"].append(e)
    manifest_save(path, m)
    return True


def manifest_entries(path, reverse=False):
    m = manifest_load(path)
    es = list(m["created"])
    return list(reversed(es)) if reverse else es


def owned_set(path):
    if not path:
        return set()
    try:
        return {(e["kind"], e["name"]) for e in manifest_entries(path)}
    except ValueError:
        return set()


# ---- CLI ------------------------------------------------------------------------------------------

def _opt(argv, name, default=None):
    if name in argv:
        i = argv.index(name)
        return argv[i + 1] if i + 1 < len(argv) else default
    return default


def main(argv):
    if not argv:
        print(__doc__)
        return 64
    cmd, rest = argv[0], argv[1:]
    if cmd == "evaluate":
        profile = _opt(rest, "--profile", "full")
        facts = parse_facts(sys.stdin.read())
        rep = evaluate(facts, profile, allow_vm="--allow-vm" in rest, enable_nested="--enable-nested" in rest,
                       owned=owned_set(_opt(rest, "--manifest")))
        if "--json" in rest:
            print(json.dumps({"profile": profile, "exit": rep.code(), "checks": rep.rows}, indent=1))
        else:
            print(render(rep, colour="--colour" in rest))
        return rep.code()
    if cmd == "plan":
        print("\n".join(plan_lines(_opt(rest, "--profile", "full"))))
        return 0
    if cmd == "topologies":
        print("\n".join(PROFILES[_opt(rest, "--profile", "full")]))
        return 0
    if cmd == "nodes":
        for n, kind, mgmt, topo in profile_nodes(_opt(rest, "--profile", "full")):
            print("%s %s %s %s" % (n, mgmt, topo, kind))
        return 0
    if cmd == "bridges":
        t = _opt(rest, "--topology")
        print("\n".join(TOPOLOGIES[t]["bridges"] if t else bridges(_opt(rest, "--profile", "full"))))
        return 0
    if cmd == "manifest-add":
        path, kind, name = rest[0], rest[1], rest[2]
        extra = dict(kv.split("=", 1) for kv in rest[3:] if "=" in kv)
        manifest_add(path, kind, name, extra or None)
        return 0
    if cmd == "manifest-has":
        return 0 if (rest[1], rest[2]) in owned_set(rest[0]) else 1
    if cmd == "manifest-list":
        for e in manifest_entries(rest[0], reverse="--reverse" in rest):
            print("%s\t%s\t%s" % (e["kind"], e["name"], json.dumps(e.get("extra") or {}, sort_keys=True)))
        return 0
    print("unknown command %r" % cmd, file=sys.stderr)
    return 64


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
