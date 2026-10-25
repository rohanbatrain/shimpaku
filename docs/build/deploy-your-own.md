# Deploy your own — the whole lab on your hypervisor

**Status: design (2026-10-25), the contract the build follows.** Owner: the operator.

A visitor who likes the demo can run the same thing at home: seven vJunos devices (campus r1, r2, sw1;
fabric spine1, spine2, leaf1, leaf2), the Shimpaku daemon and dashboard, and Virga. They give the
installer their hypervisor's address and its root password; it checks the machine, says exactly what
it will change, and on a yes builds the lab with the same public toolchain this lab is built with
(`github.com/rohanbatrain/juniper-7-day-sprint`, `ops/`).

## 1. Decisions

| # | Decision | Why |
|---|---|---|
| D1 | The installer runs **on the visitor's own computer** (`deploy/own/install.sh`, from a clone of the public repo) and talks to their hypervisor over SSH. The public site never takes a password: its "Deploy your own" page only explains, checks requirements and writes the exact command with their address filled in, client-side. | A public form that takes root passwords would make our edge a credential sink and an SSH launcher against any address on the internet. Nobody should paste a root password into a website, ours included. |
| D2 | The root password is asked once (`read -s`, never argv, never logged, never written). It is used only to install a fresh ed25519 key for this deploy (`~/.ssh/shimpaku-own-<host>`); every later step uses the key. `--key <file>` skips the password entirely. `--remove-key` takes the key off the host at the end. | The password exists in memory for one step. |
| D3 | **Preflight first, read-only, always** (`--check` runs it alone). Any FAIL stops before the first change. Then a plan ("this will install X, create Y, use Z GiB") and a typed confirmation (`--yes` for unattended). | The host is someone else's machine. |
| D4 | **Bare metal only.** `systemd-detect-virt` (and DMI) saying "this is a VM" is a FAIL: Juniper does not support vJunos launched from within a VM (double nesting). `--i-know-this-is-a-vm` turns it into a WARN, for people who want to try anyway. | Juniper's vJunos deployment guides; it would fail late and confusingly otherwise. |
| D5 | **The vJunos images are the visitor's own download** from Juniper (free, lab use, account + EULA). The installer takes `--router-image` / `--switch-image` (a local file it copies, or a path already on the host) and checks each with `qemu-img info` (qcow2, sane size). Nothing downloads or redistributes Juniper images. | Juniper's terms do not grant redistribution. |
| D6 | Everything the installer creates is recorded in a manifest on the host (`/var/lib/shimpaku-own/manifest.json`); `--uninstall` removes exactly that and nothing else. It never touches a domain, network, bridge, nft table or user it did not create. | Reversible. |
| D7 | Profiles: `full` (7 devices, default), `fabric` (4), `campus` (3). Resource checks follow the profile. | Not everyone has 40 GiB to spare. |
| D8 | The dashboard binds to the host's loopback (`127.0.0.1:8780`); the installer prints the `ssh -L` line to open it. Exposing it is the visitor's choice (`--bind`), with a warning. Auth mode `local`, an operator password generated and printed once. | A fresh lab is not hardened for the internet; ours sits behind Cloudflare + Caddy + TOTP. |
| D9 | Pinned sources: the Shimpaku and lab repos are cloned at the commits this installer was tested with (`deploy/own/PINS`), not `main`. | Reproducible. |

## 2. Preflight (every check: PASS / WARN / FAIL with one line of why and the fix)

| Check | FAIL when | WARN when |
|---|---|---|
| SSH + root | cannot log in as root (or sudo-capable user with `--user`) | |
| OS | not Linux | not Debian 11–13 / Ubuntu 20.04–24.04 (Juniper's list + newer); Arch/Fedora allowed as WARN |
| CPU arch | not x86_64 | |
| Virtualisation | no `vmx`/`svm` flag, no `/dev/kvm` | |
| Nested KVM | `kvm_intel`/`kvm_amd` `nested` is not Y/1 (fix: `--enable-nested`, which writes `/etc/modprobe.d/kvm-nested.conf` and reloads the module only when no VM is running, else asks for a reboot) | |
| Bare metal | host is itself a VM (D4) | |
| Memory | `MemAvailable` < profile × 5 GiB + 4 GiB | < profile × 5 GiB + 8 GiB |
| CPU | < 8 threads | vCPU overcommit > 2× (7 × 4 = 28 vCPU on < 14 threads) |
| Disk | free space at the data dir < images + 2 GiB × devices + 5 GiB | < 2× that |
| Conflicts | an existing libvirt domain or network named `lab-*`, a bridge / nft table the lab uses, a host route overlapping a lab subnet (10.99.0.0/24 mgmt, 10.0.0.0/16 fabric p2p, 10.20.0.0/16 campus…), port 8780 in use | another hypervisor stack (Proxmox, VirtualBox) running VMs |
| Packages | no package manager the installer knows (apt, pacman, dnf) | packages missing (they will be installed: libvirt, qemu, dosfstools, mtools, python3 ≥ 3.11, git) |
| Images | an image missing, not qcow2, or implausibly small | version differs from the tested 26.2R1.7 |
| Time | | NTP not synchronised (telemetry timestamps) |
| Outbound | cannot reach github.com (clone) | |

## 3. Deploy (after the yes)

1. Packages; `libvirtd` enabled; `lab` data dir; the operator user in group `libvirt`.
2. Clone both repos at the pins; copy the images into the lab's image dir.
3. Lab: `ops/lab-host-setup.sh`, then `make up` for the profile's topologies (campus `day-02-switching`, fabric
   `day-06-datacenter`), wait for every device to answer NETCONF (vJunos boots in ~5–10 min), then the
   day configs the lab repo applies (bootstrap), then `deploy.py --validate-only` style assertions.
4. Shimpaku: venv, `deploy/systemd/install.sh` (user unit), env file with the device credentials the lab
   set, `SHIMPAKU_AUTH_MODE=local`, an operator password (printed once), `SHIMPAKU_VIRGA_LIVE=1`.
5. Proof: wait until `/api/public/health` reads healthy for both sites; optionally `--selftest` runs one
   Virga scenario (FAB-01) end to end.
6. Print: the `ssh -L 8780:127.0.0.1:8780 root@<host>` line, the dashboard URL, the operator password,
   `--uninstall` and where the manifest is.

Every step is idempotent; a re-run resumes.

## 4. Where it shows

| Place | What |
|---|---|
| Landing page | A "Deploy your own" section: what you need, the one command, link to the docs page and the dashboard page. |
| Dashboard `/deploy` (nav "Deploy your own", under Evaluate) | Requirements checklist, the bare-metal and image notes, a hypervisor-address field that fills in the command (client-side only, nothing sent), profiles, what preflight checks, uninstall. |
| `docs/DEPLOY-YOUR-OWN.md` | The full guide; README links to it. |
| `deploy/own/README.md` | The installer's reference (flags, manifest, exit codes). |

Sources: Juniper, *vJunos-router Deployment Guide for KVM* (hardware/host requirements; "cannot be used in
any deployments that launch the instances from within a VM"); *vJunos-switch Deployment Guide for KVM*;
*Free Virtual Junos OS Download for Labs* (free, lab use only). Read 2026-10-25.
