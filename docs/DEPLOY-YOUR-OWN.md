# Deploy your own

Run the whole Shimpaku lab on your own hypervisor: the seven Junos devices (campus `r1`, `r2`, `sw1`; fabric
`spine1`, `spine2`, `leaf1`, `leaf2`), the Shimpaku daemon and dashboard, and the Virga fault lab, the same as the
public demo. An installer on **your computer** checks your machine, shows a plan, and builds the lab over SSH after
you say yes. `--uninstall` removes exactly what it made.

The dashboard has a page that writes the command for you: **Deploy your own** (`/deploy`, under Evaluate). It only
fills in command text in your browser. **It never takes a password and never sends the address you type anywhere.**
The design and its decisions are in [`docs/build/deploy-your-own.md`](build/deploy-your-own.md); the installer's own
reference (flags, manifest) is [`deploy/own/README.md`](../deploy/own/README.md).

> You type the root password into the installer on your own computer, never into a web page.

## 1. What you need

| | |
|---|---|
| **A bare-metal machine** | x86_64 Linux with VT-x or AMD-V (turned on in the BIOS/UEFI) and nested KVM. Debian 11 to 13 or Ubuntu 20.04 to 24.04 are the safe choices; Arch and Fedora are allowed with a warning. |
| **Root SSH** | From the computer you run the installer on. A sudo-capable user works with `--user`. |
| **The vJunos images** | Two qcow2 files you download yourself from Juniper (section 2). |
| **A computer to run the installer on** | Linux or macOS with `bash`, `ssh` and `git`. This is not the hypervisor. |

### How much machine

Each device is one vJunos VM with 4 vCPUs and 5 GiB of RAM, which is Juniper's minimum for both images. The
installer's budget is that, multiplied out, plus headroom for the host:

| Profile | Devices | Free RAM | CPU threads | Free disk | Images needed |
|---|---|---|---|---|---|
| `full` (default) | 7: r1, r2, sw1, spine1, spine2, leaf1, leaf2 | about 40 GiB | 8 or more | about 28 GiB | router + switch |
| `fabric` | 4: spine1, spine2, leaf1, leaf2 | about 24 GiB | 8 or more | about 22 GiB | switch |
| `campus` | 3: r1, r2, sw1 | about 19 GiB | 8 or more | about 20 GiB | router + switch |

How these are derived:

- **RAM** = 5 GiB x devices + 4 GiB reserve for the host: 7 x 5 + 4 = 39 (about 40), 4 x 5 + 4 = 24, 3 x 5 + 4 = 19.
  Below that the check fails; below 5 GiB x devices + 8 GiB it warns.
- **Disk** = about 9 GiB of images + 2 GiB per device + 5 GiB: 9 + 14 + 5 = 28, 9 + 8 + 5 = 22, 9 + 6 + 5 = 20.
  Below twice that it warns.
- **CPU**: at least 8 threads. The full lab declares 28 vCPUs; below 14 threads (over 2x overcommit) it warns, and
  boots are slower.

These are floors. A machine at the floor works but is tight; the check warns before that.

### Why bare metal

Each vJunos device is itself a nested virtual machine: the forwarding plane and the control plane are two VMs, one
inside the other. Juniper's vJunos-router deployment guide says vJunos cannot be used in any deployment that launches
the instances from within a VM. A cloud server, or a guest of another hypervisor, would therefore fail late and
confusingly, so the installer stops at the start with a clear message (it detects this with `systemd-detect-virt` and
the machine's DMI data). If you want to try anyway, `--i-know-this-is-a-vm` turns that failure into a warning; you are
then outside what Juniper supports.

## 2. Get the images

The lab runs Juniper's **vJunos-router** (r1, r2) and **vJunos-switch** (the other five). They are free for lab use
but are Juniper's: you need a Juniper account, you accept Juniper's terms, and this project neither hosts nor
redistributes them.

1. Open Juniper's [Free Virtual Junos OS Download for Labs](https://www.juniper.net/us/en/dm/vjunos-labs.html) page.
2. Download the **vJunos-router** and **vJunos-switch** qcow2 images. The lab is tested with Junos **26.2R1.7**; the
   check warns on any other version but does not stop.
3. Keep the files where you can reach them from the computer running the installer (a local path) or already on the
   hypervisor (the installer copies a local file, or uses a path that is already on the host).

The `fabric` profile needs only the switch image.

Juniper's requirements, which the table above follows:

- [vJunos-router, minimum hardware and software requirements for KVM](https://www.juniper.net/documentation/us/en/software/vjunos-router/vjunos-router-kvm/topics/vjunos-router-kvm-hw-requirements.html)
  (at least four cores, 5 GB of memory; not for use inside a VM).
- [vJunos-switch, minimum hardware and software requirements](https://www.juniper.net/documentation/us/en/software/vjunos/vjunos-switch-kvm/topics/vjunos-switch-kvm-hw-requirements.html)
  (four cores, 5 GB; three cores to the forwarding VM and one to the control VM).
- [vJunos-router overview](https://www.juniper.net/documentation/us/en/software/vjunos-router/vjunos-router-kvm/topics/vjunos-router-overview-understanding.html)
  and [vJunos-switch overview](https://www.juniper.net/documentation/us/en/software/vjunos/vjunos-switch-kvm/topics/vjunos-switch-overview-understanding.html):
  lab use only, up to 100 Mbps aggregated over all interfaces.

## 3. Get the installer

On your computer (not the hypervisor):

```bash
git clone <this repository> shimpaku
cd shimpaku
```

**The one-liner.** From your computer: `curl -fsSL https://<landing-domain>/install.sh | bash`, or on the hypervisor
itself, signed in as root or a sudo user: `curl -fsSL https://<landing-domain>/install.sh | sudo bash -s -- --local`.
Options go after `bash -s --` (for example `| bash -s -- --host 192.0.2.10 --check`). The script it fetches is
`deploy/own/get.sh`: it downloads Shimpaku at a pinned commit from GitHub and starts `deploy/own/install.sh` with your
options; every prompt reads from your terminal. Read it first with `curl -fsSL https://<landing-domain>/install.sh | less`.
The `./deploy/own/install.sh` commands below are the same thing from a clone.

The installer is `deploy/own/install.sh`. It clones the lab toolchain
([`juniper-7-day-sprint`](https://github.com/rohanbatrain/juniper-7-day-sprint), `ops/`) and Shimpaku onto the
hypervisor at the commits pinned in `deploy/own/PINS`, not at `main`, so what you get is what was tested.

## 4. Check first

`--check` runs the preflight alone. It is read-only: it logs in, reads, and prints PASS, WARN or FAIL for each item
with one line of why and the fix. Nothing on the hypervisor is changed.

```bash
./deploy/own/install.sh --host 192.0.2.10 --check \
  --router-image ~/Downloads/vjunos-router.qcow2 \
  --switch-image ~/Downloads/vjunos-switch.qcow2
```

It asks for the root password once, in your terminal (`read -s`; it never goes on a command line, into a log or to
disk) and uses it only to install a fresh ed25519 key for this deploy, `~/.ssh/shimpaku-own-<host>`. Everything after
that uses the key. If you already have a key the host accepts, pass `--key <file>` and no password is asked.

| Check | Fails when | Warns when |
|---|---|---|
| SSH and root | it cannot log in as root (or the `--user`) | |
| Operating system | not Linux | not Debian 11 to 13 / Ubuntu 20.04 to 24.04 (Arch and Fedora allowed) |
| CPU type | not x86_64 | |
| Virtualisation | no `vmx`/`svm` flag, or no `/dev/kvm` | |
| Nested KVM | the `kvm_intel`/`kvm_amd` `nested` parameter is off (fix: `--enable-nested`) | |
| Bare metal | the host is itself a VM | (only with `--i-know-this-is-a-vm`) |
| Memory | `MemAvailable` below 5 GiB x devices + 4 GiB | below 5 GiB x devices + 8 GiB |
| CPU | fewer than 8 threads | more than 2 vCPUs per thread |
| Disk | below images + 2 GiB x devices + 5 GiB | below twice that |
| Conflicts | a libvirt domain or network named `lab-*`; a bridge or nft table the lab uses; a route overlapping a lab subnet (`10.99.0.0/24` management, `10.0.0.0/16` fabric, `10.20.0.0/16` campus); port 8780 in use | another hypervisor stack (Proxmox, VirtualBox) is running VMs |
| Packages | no package manager it knows (apt, pacman, dnf) | packages are missing (they will be installed: libvirt, qemu, dosfstools, mtools, Python 3.11 or newer, git) |
| Images | an image is missing, not qcow2, or implausibly small (checked with `qemu-img info`) | version differs from 26.2R1.7 |
| Clock | | NTP is not synchronised (telemetry timestamps drift) |
| Outbound network | the host cannot reach github.com | |

Any FAIL stops the installer before its first change.

## 5. Deploy

Run the same command without `--check`. Add `--profile fabric` or `--profile campus` for a smaller lab,
`--enable-nested` if the check said nested KVM is off, and `--selftest` to finish by running one Virga scenario
(FAB-01) end to end.

```bash
./deploy/own/install.sh --host 192.0.2.10 \
  --router-image ~/Downloads/vjunos-router.qcow2 \
  --switch-image ~/Downloads/vjunos-switch.qcow2 \
  --selftest
```

It runs the preflight, prints a **plan** (what it will install, create and use, in GiB), and waits for you to type
yes. `--yes` skips the question for unattended runs. Then it:

1. installs packages, enables `libvirtd`, creates the lab's data directory, and adds the operator user to the
   `libvirt` group;
2. clones the lab and Shimpaku at the pins and copies the images into the lab's image directory;
3. sets up the lab host, creates the profile's topologies (campus `day-02-switching`, fabric `day-06-datacenter`),
   boots every device, waits until each answers NETCONF (**vJunos takes 5 to 10 minutes to boot**), applies the day
   configurations, and validates them;
4. installs Shimpaku (a virtualenv, the user service, an environment file with the device credentials the lab set,
   `SHIMPAKU_AUTH_MODE=local`, and `SHIMPAKU_VIRGA_LIVE=1`), and generates an operator password;
5. waits until `/api/public/health` reads healthy for both sites;
6. prints the tunnel line, the dashboard address, the operator password (once) and the manifest location.

Every step is idempotent, so a run that was interrupted resumes where it stopped when you run it again.

## 6. Open your dashboard

The dashboard listens on the hypervisor's loopback (`127.0.0.1:8780`) only. A fresh lab is not hardened for the
internet. From your computer:

```bash
ssh -L 8780:127.0.0.1:8780 root@192.0.2.10
```

Leave that running, open <http://127.0.0.1:8780> and sign in as the operator with the password the installer printed.
If you do want it reachable on a network, `--bind ADDR` exposes it and prints a warning; put it behind your own
authentication and TLS first.

## 7. What it changes, and uninstall

Everything the installer creates is recorded in `/var/lib/shimpaku-own/manifest.json` on the hypervisor.

```bash
./deploy/own/install.sh --host 192.0.2.10 --uninstall
```

`--uninstall` removes exactly what the manifest lists, and nothing else. It never touches a domain, network, bridge,
nft table or user it did not create. Packages that were already installed stay. Add `--remove-key` to also take the
deploy's SSH key off the host at the end.

## 8. Troubleshooting

**"Nested KVM is off."** Run again with `--enable-nested`. It writes `/etc/modprobe.d/kvm-nested.conf`
(`options kvm_intel nested=1` or `kvm_amd nested=1`) and reloads the module only when no VM is running; otherwise it
asks you to reboot the host and run the installer again. Check by hand with
`cat /sys/module/kvm_intel/parameters/nested` (or `kvm_amd`): it must read `Y` or `1`.

**"This host is a virtual machine."** Run on bare metal; see "Why bare metal". Common cases: a cloud instance, a
Proxmox/VMware/VirtualBox guest, WSL2. `--i-know-this-is-a-vm` lets you try anyway, unsupported by Juniper, and is
likely to fail late or run very slowly.

**"No vmx/svm flag" or no `/dev/kvm`.** Enable VT-x / AMD-V (sometimes called SVM) in the BIOS/UEFI. Check with
`grep -E -c 'vmx|svm' /proc/cpuinfo`, which must not print 0.

**"Not enough RAM."** Use a smaller profile (`--profile fabric` needs about 24 GiB, `--profile campus` about 19 GiB) or
free memory on the host. Stop other VMs; `MemAvailable` in `/proc/meminfo` is what the check reads. A warning (not a
failure) means it will probably work but leaves the host under 8 GiB of headroom.

**"Conflict: lab-* already exists" or "port 8780 is in use".** The message names the item. Another Shimpaku or lab
install, or something of yours using the same names, subnets or port. The installer never removes something it did not
create; rename or remove yours, or use a different host. A previous install by this installer is removed with
`--uninstall`.

**"Image is not qcow2" or "implausibly small."** The download was probably interrupted, or you passed the compressed
archive. Check with `qemu-img info <file>` (it should say `file format: qcow2`), download again if not.

**"Cannot log in as root."** Check the address, that `sshd` is running and that root login is permitted (or use
`--user` with a sudo-capable user, or `--key` with a key the host accepts).

**It has been 10 minutes and a device is not up.** vJunos boots in 5 to 10 minutes, longer on a loaded or low-core
host. The installer keeps waiting and says which device it is waiting for. If one never answers, run the installer
again (it resumes), and read that VM's console: `virsh console <domain>` on the hypervisor.

**The dashboard shows devices as unreachable.** Give it a minute after the install: the first gNMI samples arrive
every 10 seconds. If it persists, check the daemon, which runs as a user service named `shimpakud`, on the hypervisor.

## Licence and trademarks

The vJunos images are Juniper's, used under Juniper's terms for lab use. Shimpaku is an independent project, not
affiliated with or endorsed by Juniper Networks or HPE, and uses none of their marks.
