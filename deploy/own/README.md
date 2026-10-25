# deploy/own — the "Deploy your own" installer

Builds the whole Shimpaku lab on **your** hypervisor: seven vJunos devices (campus r1, r2, sw1;
fabric spine1, spine2, leaf1, leaf2), the Shimpaku daemon and dashboard, and Virga. The design and
its decisions D1–D9 are in [`docs/build/deploy-your-own.md`](../../docs/build/deploy-your-own.md);
the visitor guide is `docs/DEPLOY-YOUR-OWN.md`. This page is the installer's reference.

## One line

Read it first:

```bash
curl -fsSL https://<landing-domain>/install.sh | less
```

From your laptop (macOS or Linux), against the hypervisor:

```bash
curl -fsSL https://<landing-domain>/install.sh | bash                          # asks for what it needs
curl -fsSL https://<landing-domain>/install.sh | bash -s -- --host 192.0.2.10 --profile fabric \
     --switch-image ~/Downloads/vJunos-switch-26.2R1.7.qcow2
```

On the hypervisor itself, as root (no SSH, no password):

```bash
curl -fsSL https://<landing-domain>/install.sh | sudo bash -s -- --local \
     --router-image /root/vJunos-router-26.2R1.7.qcow2 --switch-image /root/vJunos-switch-26.2R1.7.qcow2
```

`install.sh` on the site is [`get.sh`](get.sh): it downloads this repository at one pinned commit
from `codeload.github.com` into `~/.local/share/shimpaku-own/src-<commit>` (reused when present) and
runs `deploy/own/install.sh` from it with your arguments. Its body is one `main()` called on the last
line, so a cut-short download runs nothing. The site build replaces `__SHIMPAKU_PIN__` with the
published commit; a copy that still has the placeholder warns and uses the `public` branch.

From a clone, the same thing is `deploy/own/install.sh --host <ip> ...`.

## What you need

- A **bare-metal** x86_64 Linux host with VT-x/AMD-V and nested KVM. Juniper does not support vJunos
  launched from inside a VM (double nesting): preflight FAILs on a VM unless `--i-know-this-is-a-vm`.
- RAM: devices × 5 GiB + 4 GiB available (full 39 GiB, fabric 24, campus 19); 8+ threads; disk for the
  images + 2 GiB per device + 5 GiB.
- Debian 12/13 or Ubuntu 24.04 is the smooth path (system `python3` ≥ 3.11, which the lab toolchain
  needs). Arch and Fedora run with a WARN. Ubuntu 20.04/22.04 and Debian 11 ship python3 3.8–3.10 and
  FAIL the Python check.
- **Your own vJunos images** (D5): download vJunos-router and vJunos-switch 26.2R1.7 from Juniper
  (free, lab use, Juniper account + EULA). Give each as a file on your computer (copied over SSH) or a
  path already on the host. `fabric` needs only the switch image.
- Outbound HTTPS from the host to github.com and pypi.org.
- Root on the host: root's password once (D2), or `--key FILE`, or `--user <sudo user>`.

## Flags

| Flag | Meaning |
|---|---|
| `--host ADDR` | the hypervisor (IP or name). Without it (and without `--local`), the installer asks, and offers `--local` when this machine has `/dev/kvm` |
| `--local` | deploy to this machine; needs root (re-runs itself under `sudo`) |
| `--check` | preflight only: read-only, prints the table, exits 0/1/2 |
| `--json` | with `--check`: the table as JSON on stdout (progress goes to stderr) |
| `--dry-run` | print every command the deploy would run, in order, for a fresh host; contacts nothing (`--dry-run-os apt\|pacman\|dnf`) |
| `--profile full\|fabric\|campus` | 7, 4 or 3 devices (default `full`) |
| `--router-image F` / `--switch-image F` | your vJunos qcow2 files: local path (copied) or host path |
| `--key F` | log in with this key (already authorized); no password asked |
| `--user U` | log in as `U` (default root). Without passwordless sudo, the installer runs one `ssh -t ... sudo` (sudo asks on the host) to authorize its key for root |
| `--port N` | SSH port (default 22) |
| `--ssh-alias A` | use an alias from your `~/.ssh/config` as is (no key management) |
| `--operator NAME` | the user the lab and Shimpaku run as (default `shimpaku`; created, must not exist) |
| `--enable-nested` | turn nested KVM on: writes `/etc/modprobe.d/kvm-nested.conf`, reloads the module if no VM runs, else exits 4 (reboot, re-run) |
| `--i-know-this-is-a-vm` | turn the bare-metal FAIL into a WARN |
| `--bind ADDR` | where the dashboard listens (default `127.0.0.1`; anything else is a WARN) |
| `--selftest` | after the deploy, run Virga scenario FAB-01 end to end (fabric profiles) |
| `--yes` | no typed confirmation (unattended); also required when there is no terminal |
| `--uninstall` | remove exactly what the manifest lists (`--keep-packages` leaves packages) |
| `--remove-key` | at the end, take the deploy key off the host (and delete it here) |

## Exit codes

| Code | Meaning |
|---|---|
| 0 | done; or `--check`: every check passed |
| 1 | `--check`: warnings only (a deploy continues after your confirmation) |
| 2 | a preflight check failed, or login failed: nothing was changed |
| 3 | a deploy step failed: fix the cause, re-run the same command (every step resumes) |
| 4 | nested KVM was enabled but VMs are running: reboot, re-run |
| 5 | aborted (confirmation not typed) |
| 64 | usage: a bad flag, or a prompt needed with no terminal |

## Preflight (read-only)

[`remote/preflight.sh`](remote/preflight.sh) reads facts (`/proc`, `/sys`, `/etc/os-release`, `ip`,
`ss`, `virsh list`, `nft list tables` as root) and opens one TCP connection each to github.com:443 and
pypi.org:443; [`remote/ownlib.py`](remote/ownlib.py) turns them into the table. It installs, writes and
starts nothing (a socket-activated libvirtd may wake up for `virsh list`).

| Check | FAIL | WARN |
|---|---|---|
| SSH + root | not root and no passwordless sudo | |
| OS | not Linux | outside Debian 11–13 / Ubuntu 20.04–24.04; Arch, Fedora |
| CPU arch | not x86_64 | |
| Virtualisation | no `vmx`/`svm`, no `/dev/kvm` | |
| Nested KVM | `nested` not Y/1 | `--enable-nested` given: it will be turned on |
| Bare metal | `systemd-detect-virt` or DMI says VM | with `--i-know-this-is-a-vm`; or cannot tell |
| Memory | MemAvailable < devices × 5 GiB + 4 GiB (devices already running from this deploy excluded) | < … + 8 GiB |
| CPU | < 8 threads | vCPU > 2 × threads |
| Disk | free < images to copy + 2 GiB × devices + 5 GiB | < 2 × that |
| Conflicts | a `lab-*` libvirt domain, network or interface; nft table `inet lab_iso`; a host route overlapping 10.99.0.0/24; port 8780 listening; `/home/lab` or the operator user/group existing — unless this installer's manifest owns it (a resumed deploy) | a route overlapping a device-internal subnet (10.0.0.0/16, 10.10/10.20/10.100/10.255.0.0/24: they never touch the host); Proxmox/VirtualBox/VMware present; other VMs running; nft unreadable without root |
| Packages | no apt, pacman or dnf | tools missing (they will be installed) |
| Python | system python3 < 3.11 | python3 missing (will be installed) |
| Image (router/switch) | not given, missing, not qcow2, < 1 GiB | version not 26.2R1.7 |
| Images dir | | other vJunos images of the same kind in `/home/lab/images` (lab-gen.py picks the last by name) |
| Time | | NTP not synchronised |
| Outbound | github.com or pypi.org unreachable | |
| Pins | a pinned commit is not on GitHub (checked from your computer) | could not verify |

## What the deploy does

After the plan and your typed confirmation (type the host address; `--yes` skips it), in two stages
of [`remote/deploy.sh`](remote/deploy.sh), each command printed as it runs:

1. **base** — missing packages (libvirt, qemu, dosfstools, mtools, python3, git, curl, nftables);
   nested KVM (with `--enable-nested`); libvirtd enabled; the operator user (groups libvirt, kvm and
   QEMU's group; linger on); the lab repo and Shimpaku cloned at [`PINS`](PINS); `ops/lab-host-setup.sh`
   for `/home/lab`; images already on the host copied into `/home/lab/images`.
2. your local image files are streamed into `/home/lab/images` (skipped when already there).
3. **rest** — uv and gnmic (sha256-checked downloads) and Shimpaku's venv (`uv`, Python 3.12, `.[junos]`);
   per topology: `lab-gen.py --check`, the lab's net script (bridges + `lab_iso`) as root, `ops/lab.sh up`;
   wait for NETCONF; device configs: campus = [`configs/campus/`](configs/campus) + [`configs/services/`](configs/services)
   merged in one commit per device, then OSPF and users→servers checked; fabric = the lab repo's
   `day-07/automation/gen_fabric.py` + `deploy.py` (commit + its five assertions), then the services set;
   Shimpaku's env file (`SHIMPAKU_AUTH_MODE=local`, `SHIMPAKU_VIRGA_LIVE=1`, the lab's published device
   credentials), a generated operator password (`shimpaku admin set-password --stdin`), the user unit
   (`deploy/systemd/install.sh`); then `/api/public/health` until the profile's sites read `healthy`.
4. the operator password is printed **once**, to your terminal only (never to the log), and deleted on
   the host; then the `ssh -L 8780:127.0.0.1:8780 …` line and the dashboard URL.

Re-running the same command resumes: every step checks before it changes anything. Nothing autostarts
after a reboot (the lab's bridges are runtime state): re-run the command to bring the lab back.

`configs/services/*.set` is what Shimpaku needs on each device: gNMI in clear text on 32767 in the
management instance, the RPM probes of [`docs/build/rpm-config.set`](../../docs/build/rpm-config.set),
and on r1 the routing-engine filter term that lets the collector in. `configs/campus/*.set` is the
campus baseline (VLANs, OSPF, router-on-a-stick, the users and routing-engine filters, sw1's users
host). Both were checked line by line against the running lab; lab addressing only.

## Password and keys (D2)

- The password is read once from `/dev/tty` with echo off and kept in one shell variable. It reaches
  `ssh` through `SSH_ASKPASS` (OpenSSH ≥ 8.4, `SSH_ASKPASS_REQUIRE=force`): a 0700 helper that `cat`s a
  FIFO in a 0700 temp dir, fed once by the shell's builtin `printf`. It is never in an argv, an
  environment variable, a file or the log. With an older OpenSSH, ssh prompts for it itself.
- It is used for one login, which appends a fresh `~/.ssh/shimpaku-own-<host>` ed25519 key to
  `authorized_keys`. Every later step uses the key. `--key` skips the password; `--remove-key` takes
  the key off the host at the end.
- Debian and Ubuntu refuse root password logins by default (`PermitRootLogin prohibit-password`):
  use `--user <sudo user>` (its password), `--key`, or `--local`.

## Manifest and uninstall (D6)

`/var/lib/shimpaku-own/manifest.json` on the host (mode 0600), written atomically, one entry per
created thing, **recorded before it is created**:

```json
{"schema": "shimpaku-own/manifest/v1",
 "created": [{"kind": "package", "name": "mtools", "at": "2026-10-25T18:02:11Z", "extra": {"manager": "apt"}},
             {"kind": "user", "name": "shimpaku", "at": "..."},
             {"kind": "dir", "name": "/home/lab", "at": "..."},
             {"kind": "bridge", "name": "lab-mgmt", "at": "..."},
             {"kind": "nft-table", "name": "inet lab_iso", "at": "..."},
             {"kind": "domain", "name": "lab-r1", "at": "..."}]}
```

Kinds: `package`, `file`, `service`, `user`, `group-member`, `linger`, `repo`, `dir`, `image`, `bridge`,
`nft-table`, `domain`, `user-unit`, `key`. `--uninstall` lists what it will remove, asks for the typed
confirmation, then removes the entries newest first ([`remote/uninstall.sh`](remote/uninstall.sh)):
domains destroyed and undefined, bridges and the nft table deleted, files and directories removed
(only under `/home/<user>/…`, `/home/lab`, `/var/lib/shimpaku-own`), linger off, the user deleted with its
home, packages removed (`--keep-packages` keeps them), then the manifest. Nothing outside the manifest
is touched. A directory the installer created goes with everything in it.

## Logs

`~/.local/state/shimpaku-own/<host>.log` on your computer: every command and its output, no secrets
(the root password never reaches stdout/stderr; the operator password goes to the terminal only).
`~/.local/state/shimpaku-own/known_hosts` holds the host key accepted on first contact.

## Known limits

- `ops/lab-bootstrap.py` (the console fallback when the vJunos boot config disk does not apply)
  ignores a topology's `ordinal_base`, so for `day-06-datacenter` it would type into the campus
  consoles. The installer uses it only for the campus; a fabric node that never answers NETCONF stops
  the deploy.
- The lab's net script recreates `inet lab_iso` per topology, so with both topologies the table
  holds the second topology's forward-drop rules only (the host-input rule is the same in both).
- The lab toolchain's repository grants no licence yet; it is cloned on your host at the pinned
  commit, never copied into this repository.
- Device credentials are the lab's published boot defaults (`admin` / `admin@123`); the lab is
  host-only by construction (no uplink, no NAT, `lab_iso`).

## Tests

`PYTHONPATH=src:bench .venv/bin/python -m pytest -q tests/ops/test_own_installer.py` (profiles and
resource maths, every preflight row, subnet overlap, manifest round trip, set files, `bash -n` +
shellcheck of every script, the dry-run recording each thing before creating it).
