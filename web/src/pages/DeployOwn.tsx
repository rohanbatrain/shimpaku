// "Deploy your own": run the whole lab (seven vJunos devices, Shimpaku, Virga) on your own hypervisor.
// Contract: docs/build/deploy-your-own.md. The one rule this page keeps (D1): it never takes a password and
// never sends anything anywhere. The address field below only fills in the command text, in this browser;
// there is no fetch, no form action and no storage in this file.
import { useRef, useState } from "react";
import { Link } from "react-router";
import TopBar from "../components/TopBar";
import "../public.css";
import "../deploy.css";

const REPO: string = ((import.meta as any).env?.VITE_REPO_URL ?? "").replace(/\/$/, "");
const JUNIPER_FREE = "https://www.juniper.net/us/en/dm/vjunos-labs.html";
const JUNIPER_ROUTER = "https://www.juniper.net/documentation/us/en/software/vjunos-router/vjunos-router-kvm/topics/vjunos-router-kvm-hw-requirements.html";
const JUNIPER_SWITCH = "https://www.juniper.net/documentation/us/en/software/vjunos/vjunos-switch-kvm/topics/vjunos-switch-kvm-hw-requirements.html";

type Profile = "full" | "fabric" | "campus";

const PROFILES: { id: Profile; name: string; devices: number; names: string; images: string; router: boolean; switch: boolean }[] = [
  { id: "full", name: "full", devices: 7, names: "r1, r2, sw1, spine1, spine2, leaf1, leaf2", images: "router + switch", router: true, switch: true },
  { id: "fabric", name: "fabric", devices: 4, names: "spine1, spine2, leaf1, leaf2", images: "switch", router: false, switch: true },
  { id: "campus", name: "campus", devices: 3, names: "r1, r2, sw1", images: "router + switch", router: true, switch: true },
];

// 5 GiB per device (Juniper's minimum) + 4 GiB reserve for the host; disk: ~9 GiB of images + 2 GiB per device + 5 GiB.
const ramGiB = (n: number) => n * 5 + 4;
const diskGiB = (n: number) => 9 + n * 2 + 5;

// ---- command text, built from the form. Everything here is a pure function of what was typed. ----

const IPV4 = /^(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(\.(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3}$/;
const IPV6 = /^[0-9a-f:.]+$/i;
const HOSTNAME = /^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*$/i;

export function hostKind(v: string): "ipv4" | "ipv6" | "hostname" | null {
  const s = v.trim();
  if (!s) return null;
  if (IPV4.test(s)) return "ipv4";
  if (s.includes(":") && IPV6.test(s) && (s.match(/:/g) ?? []).length >= 2 && !/:::|[0-9a-f]{5,}/i.test(s) && (s.match(/::/g) ?? []).length <= 1) return "ipv6";
  if (/^[\d.]+$/.test(s)) return null; // looks like an IPv4 address but is not one
  if (HOSTNAME.test(s)) return "hostname";
  return null;
}

// single-quote anything that is not plainly safe, so a pasted path can never change the command
const SAFE = /^[A-Za-z0-9_@%+=:,./-]+$/;
const sq = (v: string) => (SAFE.test(v) ? v : `'${v.replace(/'/g, `'\\''`)}'`);
function q(v: string): string {
  // keep a leading ~/ outside the quotes so the shell still expands it
  return v.startsWith("~/") ? `~/${sq(v.slice(2))}` : sq(v);
}

type Form = { host: string; profile: Profile; router: string; switchImg: string; user: string; key: string; nested: boolean; selftest: boolean };

// the one-liner's script lives on the landing page of the domain this dashboard is on:
// <label>-app.<parent> -> <label>.<parent>/install.sh (deploy/vps/site/install.sh, a pinned bootstrap)
export const INSTALL_URL = /^[^.]+-app\./.test(location.hostname)
  ? `https://${location.hostname.replace(/^([^.]+)-app\./, "$1.")}/install.sh` : "https://<landing-domain>/install.sh";
const PIPE = `curl -fsSL ${INSTALL_URL} | bash -s --`;

export function buildCommands(f: Form): { check: string; deploy: string; local: string } {
  const host = hostKind(f.host) ? q(f.host.trim()) : "<hypervisor-address>";
  const user = /^[a-z_][a-z0-9_-]{0,31}$/i.test(f.user.trim()) && f.user.trim() !== "root" ? f.user.trim() : "";
  const p = PROFILES.find((x) => x.id === f.profile)!;
  const base = [PIPE, "--host", host];
  if (user) base.push("--user", user);
  if (f.key.trim()) base.push("--key", q(f.key.trim()));
  const img: string[] = [];
  if (f.profile !== "full") img.push("--profile", f.profile);
  if (p.router) img.push("--router-image", f.router.trim() ? q(f.router.trim()) : "<path-to-vJunos-router.qcow2>");
  if (p.switch) img.push("--switch-image", f.switchImg.trim() ? q(f.switchImg.trim()) : "<path-to-vJunos-switch.qcow2>");
  const extra: string[] = [];
  if (f.nested) extra.push("--enable-nested");
  const wrap = (a: string[]) => a.slice(1).reduce<string[]>((rows, w) => {
    if (w.startsWith("--")) rows.push(w); else rows[rows.length - 1] += ` ${w}`;
    return rows;
  }, [a[0]]).join(" \\\n  ");
  const check = wrap([...base, "--check", ...img, ...extra]);
  const deploy = wrap([...base, ...img, ...extra, ...(f.selftest ? ["--selftest"] : [])]);
  // on the hypervisor itself: no SSH, no password; image paths are paths on that machine
  const local = wrap([`curl -fsSL ${INSTALL_URL} | sudo bash -s --`, "--local", ...img, ...extra, ...(f.selftest ? ["--selftest"] : [])]);
  return { check, deploy, local };
}

export function tunnel(host: string, user: string): string {
  const h = hostKind(host) ? host.trim() : "<hypervisor-address>";
  const u = /^[a-z_][a-z0-9_-]{0,31}$/i.test(user.trim()) && user.trim() ? user.trim() : "root";
  return `ssh -L 8780:127.0.0.1:8780 ${u}@${h}`;
}

// ---- small pieces ----

function CopyBlock({ label, text }: { label: string; text: string }) {
  const [state, setState] = useState<"idle" | "done" | "manual">("idle");
  const pre = useRef<HTMLPreElement>(null);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setState("done");
    } catch {
      // clipboard blocked (plain http, old browser): select the text so Ctrl/Cmd+C works
      const el = pre.current;
      if (el) { const r = document.createRange(); r.selectNodeContents(el); const s = window.getSelection(); s?.removeAllRanges(); s?.addRange(r); }
      setState("manual");
    }
    setTimeout(() => setState("idle"), 2500);
  };
  return (
    <div className="own-cmd">
      <div className="own-cmd-h">
        <span>{label}</span>
        <button type="button" className="btn" onClick={copy} aria-live="polite">
          {state === "done" ? "Copied" : state === "manual" ? "Selected: press Ctrl/Cmd+C" : "Copy"}
        </button>
      </div>
      <pre ref={pre} tabIndex={0} aria-label={label}>{text}</pre>
    </div>
  );
}

const PREFLIGHT: { check: string; fail: string; warn: string; fix: string }[] = [
  { check: "SSH and root", fail: "It cannot log in as root (or as the sudo-capable user you gave with --user).", warn: "", fix: "Check the address, that sshd runs, and that root login by password or key is allowed." },
  { check: "Operating system", fail: "Not Linux, or a system Python below 3.11 (Debian 11, Ubuntu 20.04 and 22.04).", warn: "Not Debian 12 or 13 or Ubuntu 24.04. Arch and Fedora are allowed with a warning.", fix: "Use Debian or Ubuntu if you can." },
  { check: "CPU type", fail: "Not x86_64.", warn: "", fix: "vJunos is an x86 image; ARM machines (including Apple silicon) cannot run it." },
  { check: "Hardware virtualisation", fail: "No vmx or svm CPU flag, or no /dev/kvm.", warn: "", fix: "Turn on VT-x (Intel) or AMD-V in the BIOS/UEFI." },
  { check: "Nested KVM", fail: "kvm_intel or kvm_amd reports nested as off.", warn: "", fix: "Re-run with --enable-nested. It writes /etc/modprobe.d/kvm-nested.conf and reloads the module only if no VM is running; otherwise it asks you to reboot." },
  { check: "Bare metal", fail: "The host is itself a virtual machine.", warn: "Only if you pass --i-know-this-is-a-vm, which turns the failure into a warning.", fix: "Run it on a physical machine. Juniper does not support vJunos started from inside a VM." },
  { check: "Memory", fail: "Available RAM is below 5 GiB per device plus 4 GiB.", warn: "Below 5 GiB per device plus 8 GiB.", fix: "Pick a smaller profile, or free memory." },
  { check: "CPU threads", fail: "Fewer than 8 threads.", warn: "More than 2 virtual CPUs per hardware thread (the full lab declares 28 vCPUs, so fewer than 14 threads).", fix: "A smaller profile, or accept slower boots." },
  { check: "Disk", fail: "Free space where the lab lives is below the images + 2 GiB per device + 5 GiB.", warn: "Below twice that.", fix: "Free some space or use a larger volume." },
  { check: "Conflicts", fail: "A libvirt domain or network named lab-*, a bridge or nft table the lab needs, a route that overlaps a lab subnet (10.99.0.0/24 management, 10.0.0.0/16 fabric, 10.20.0.0/16 campus), or port 8780 already in use.", warn: "Another hypervisor stack (Proxmox, VirtualBox) is running VMs.", fix: "The message names the exact item. The installer never edits or removes something it did not create." },
  { check: "Packages", fail: "No package manager the installer knows (apt, pacman, dnf).", warn: "Some packages are missing; the plan lists them and they are installed on your yes (libvirt, qemu, dosfstools, mtools, Python 3.11 or newer, git).", fix: "" },
  { check: "Images", fail: "A needed image is missing, is not qcow2, or is implausibly small.", warn: "The version differs from the tested 26.2R1.7.", fix: "Download again from Juniper and point --router-image and --switch-image at the files." },
  { check: "Clock", fail: "", warn: "NTP is not synchronised, so telemetry timestamps drift.", fix: "Enable chrony or systemd-timesyncd." },
  { check: "Outbound network", fail: "The host cannot reach github.com (it clones the code from there).", warn: "", fix: "Allow outbound HTTPS from the host." },
];

export default function DeployOwn() {
  const [f, setF] = useState<Form>({ host: "", profile: "full", router: "", switchImg: "", user: "", key: "", nested: false, selftest: false });
  const set = <K extends keyof Form>(k: K, v: Form[K]) => setF((s) => ({ ...s, [k]: v }));
  const kind = hostKind(f.host);
  const bad = f.host.trim() !== "" && !kind;
  const prof = PROFILES.find((p) => p.id === f.profile)!;
  const cmds = buildCommands(f);
  const clone = `git clone ${REPO || "<this-repository-url>"} shimpaku\ncd shimpaku\n./deploy/own/install.sh --help`;
  const docs = REPO ? `${REPO}/blob/main/docs/DEPLOY-YOUR-OWN.md` : null;
  const link = (path: string) => (REPO ? <a href={`${REPO}/blob/main/${path}`} target="_blank" rel="noreferrer"><code>{path}</code></a> : <code>{path}</code>);

  return (
    <>
      <TopBar title="Deploy your own" crumbs="Evaluate" />
      <div className="content own">
        <p className="weak own-lede">
          Like the demo? Run the whole lab on your own hypervisor: the same seven Junos devices, Shimpaku and the Virga fault lab.
          You bring a spare x86 Linux machine and Juniper's free lab images; an installer on your own computer checks the machine,
          tells you what it will change, and builds the lab only after you say yes.
        </p>

        <div className="notice info" role="note">
          <b>New, and not yet run end to end on a clean machine.</b>{" "}
          The checks, the plan and the password handling are tested; a full deploy on a fresh bare-metal host has not happened yet.
          Start with the check, which changes nothing.
        </div>

        <div className="notice ok" role="note">
          <b>You type the root password into the installer on your own computer, never into this page.</b>{" "}
          This page has no password field and sends nothing anywhere: the address below only fills in the command text in your browser,
          and it is not stored either.
        </div>

        <section className="card" aria-labelledby="own-get">
          <h2 id="own-get">What you get</h2>
          <div className="own-two">
            <ul className="own-list">
              <li><b>Seven real Junos devices</b> as virtual machines: campus <code>r1</code>, <code>r2</code> (vJunos-router) and <code>sw1</code>; fabric <code>spine1</code>, <code>spine2</code>, <code>leaf1</code>, <code>leaf2</code> (vJunos-switch, EVPN-VXLAN).</li>
              <li><b>Shimpaku</b>: the daemon and this same dashboard, reading those devices over gNMI and NETCONF.</li>
              <li><b>Virga</b>: the fault lab that breaks the network on purpose and checks the fix, optionally proven with one self-test.</li>
            </ul>
            <ul className="own-list">
              <li>The dashboard is private to your machine: it listens on the hypervisor's loopback and you reach it through an SSH tunnel.</li>
              <li>Smaller labs are fine: <code>--profile fabric</code> (4 devices) or <code>--profile campus</code> (3).</li>
              <li>Everything it creates is recorded, and <code>--uninstall</code> removes exactly that.</li>
            </ul>
          </div>
        </section>

        <section className="card" aria-labelledby="own-need">
          <h2 id="own-need">What you need</h2>
          <ul className="own-list">
            <li><b>A bare-metal x86_64 Linux machine</b> with VT-x or AMD-V and nested KVM. Debian 12 or 13, or Ubuntu 24.04, are the safe choices (older releases ship a Python below 3.11, which the lab toolchain needs).</li>
            <li><b>Root on it</b>: Debian and Ubuntu refuse root password logins over SSH by default, so either give a sudo user (<code>--user</code>), an SSH key (<code>--key</code>), or run the one-liner on the hypervisor itself with <code>--local</code>.</li>
            <li><b>Root SSH access</b> to it from the computer you will run the installer on (or a sudo-capable user, with <code>--user</code>).</li>
            <li><b>The two vJunos images, downloaded by you from Juniper.</b> They are free for lab use, need a Juniper account and acceptance of Juniper's terms, and are not redistributed by this project.{" "}
              <a href={JUNIPER_FREE} target="_blank" rel="noreferrer">Free Virtual Junos OS Download for Labs ↗</a></li>
            <li><b>Enough memory.</b> Each device needs 5 GiB (Juniper's minimum, 4 vCPUs) and the host keeps 4 GiB for itself.</li>
          </ul>
          <div className="own-scroll">
            <table className="own-table" aria-label="Resources per profile">
              <thead><tr><th>Profile</th><th>Devices</th><th>Free RAM</th><th>CPU threads</th><th>Free disk</th><th>Images</th></tr></thead>
              <tbody>
                {PROFILES.map((p) => (
                  <tr key={p.id} className={p.id === f.profile ? "on" : undefined}>
                    <td data-label="Profile"><div><b>{p.name}</b><div className="small weak">{p.names}</div></div></td>
                    <td data-label="Devices">{p.devices}</td>
                    <td data-label="Free RAM">≈ {ramGiB(p.devices)} GiB</td>
                    <td data-label="CPU threads">8 or more</td>
                    <td data-label="Free disk">≈ {diskGiB(p.devices)} GiB</td>
                    <td data-label="Images">{p.images}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="small weak own-note">
            RAM = 5 GiB per device + 4 GiB reserve for the host (the installer warns below 8 GiB of reserve). Disk = about 9 GiB of images +
            2 GiB per device + 5 GiB. These are Juniper's minimums for one device ({" "}
            <a href={JUNIPER_ROUTER} target="_blank" rel="noreferrer">vJunos-router ↗</a>,{" "}
            <a href={JUNIPER_SWITCH} target="_blank" rel="noreferrer">vJunos-switch ↗</a>) multiplied out; a fresh machine may need more.
            The images come to about 9 GiB, so the disk figure is a floor, not a promise.
          </p>
          <div className="notice warn" role="note">
            <b>Why bare metal?</b> Each vJunos device is itself a nested virtual machine. Juniper's deployment guide says vJunos cannot be used in
            deployments that launch the instances from within a VM, so running this inside a cloud server or another hypervisor's guest fails late
            and confusingly. The installer stops with a clear message instead. If you want to try anyway, <code>--i-know-this-is-a-vm</code> turns
            that failure into a warning.
          </div>
        </section>

        <section className="card" aria-labelledby="own-cmd">
          <h2 id="own-cmd">Build your command</h2>
          <p className="weak own-p">
            Fill in what you know; the commands below update as you type. Each is one line to paste into a terminal on <b>your</b> computer:
            it downloads a small, pinned installer and runs it there. Nothing you enter here is sent anywhere.
          </p>
          <form className="own-form" onSubmit={(e) => e.preventDefault()} autoComplete="off" noValidate>
            <div className="field">
              <label htmlFor="own-host">Hypervisor address</label>
              <input id="own-host" type="text" inputMode="url" autoComplete="off" autoCapitalize="none" autoCorrect="off" spellCheck={false}
                placeholder="192.0.2.10 or lab-host.example" value={f.host} onChange={(e) => set("host", e.target.value)}
                aria-invalid={bad} aria-describedby="own-host-h" />
              <span id="own-host-h" className={`small ${bad ? "own-bad" : "weak"}`} role={bad ? "alert" : undefined}>
                {bad ? "That is not an IPv4 address, IPv6 address or hostname. The command keeps a placeholder until it is."
                  : kind ? `Looks like ${kind === "ipv4" ? "an IPv4 address" : kind === "ipv6" ? "an IPv6 address" : "a hostname"}. Only used to write the command text.`
                  : "An IPv4, IPv6 address or hostname that you can SSH to as root."}
              </span>
            </div>
            <div className="field">
              <label htmlFor="own-profile">Profile</label>
              <select id="own-profile" value={f.profile} onChange={(e) => set("profile", e.target.value as Profile)}>
                {PROFILES.map((p) => <option key={p.id} value={p.id}>{p.name}: {p.devices} devices, about {ramGiB(p.devices)} GiB RAM</option>)}
              </select>
            </div>
            {prof.router && (
              <div className="field">
                <label htmlFor="own-router">vJunos-router image (qcow2)</label>
                <input id="own-router" type="text" autoComplete="off" autoCapitalize="none" spellCheck={false}
                  placeholder="~/Downloads/vjunos-router.qcow2" value={f.router} onChange={(e) => set("router", e.target.value)} />
              </div>
            )}
            {prof.switch && (
              <div className="field">
                <label htmlFor="own-switch">vJunos-switch image (qcow2)</label>
                <input id="own-switch" type="text" autoComplete="off" autoCapitalize="none" spellCheck={false}
                  placeholder="~/Downloads/vjunos-switch.qcow2" value={f.switchImg} onChange={(e) => set("switchImg", e.target.value)} />
              </div>
            )}
            <details className="own-adv">
              <summary>Other options</summary>
              <div className="field">
                <label htmlFor="own-user">SSH user (default root)</label>
                <input id="own-user" type="text" autoComplete="off" autoCapitalize="none" spellCheck={false} placeholder="root"
                  value={f.user} onChange={(e) => set("user", e.target.value)} />
              </div>
              <div className="field">
                <label htmlFor="own-key">Existing SSH key (skips the password)</label>
                <input id="own-key" type="text" autoComplete="off" autoCapitalize="none" spellCheck={false} placeholder="~/.ssh/id_ed25519"
                  value={f.key} onChange={(e) => set("key", e.target.value)} />
              </div>
            </details>
            <label className="own-check"><input type="checkbox" checked={f.nested} onChange={(e) => set("nested", e.target.checked)} />
              <span>Turn on nested KVM if it is off (<code>--enable-nested</code>)</span></label>
            <label className="own-check"><input type="checkbox" checked={f.selftest} onChange={(e) => set("selftest", e.target.checked)} />
              <span>Run one fault-lab scenario at the end to prove it works (<code>--selftest</code>)</span></label>
          </form>

          <ol className="own-steps">
            <li>
              <b>Check first.</b> Read-only: it changes nothing on the hypervisor.
              <CopyBlock label="Check (safe)" text={cmds.check} />
              <p className="small weak own-p">It asks for the root password once, in your terminal, and uses it only to install a fresh SSH key for this deploy. Give <code>--key</code> to skip the password.</p>
            </li>
            <li>
              <b>Deploy.</b> It prints a plan (what it will install, create and use) and waits for you to type the host address to agree. Boot takes 5 to 10 minutes.
              <CopyBlock label="Deploy" text={cmds.deploy} />
            </li>
            <li>
              <b>Or run it on the hypervisor itself</b> (signed in there as root or a sudo user): no SSH and no password; the image paths are paths on that machine.
              <CopyBlock label="On the hypervisor" text={cmds.local} />
            </li>
          </ol>
          <details className="own-adv">
            <summary>Read the script before you run it, or use a clone instead</summary>
            <CopyBlock label="Read it first" text={`curl -fsSL ${INSTALL_URL} | less`} />
            <CopyBlock label="From a clone" text={clone} />
            <p className="small weak own-p">The script only downloads Shimpaku at a pinned commit from GitHub and starts <code>deploy/own/install.sh</code> with your options.</p>
          </details>
        </section>

        <section className="card" aria-labelledby="own-pre">
          <h2 id="own-pre">What the check looks at</h2>
          <p className="weak own-p">
            Every check ends in PASS, WARN or FAIL with one line of why and the fix. Any FAIL stops the installer before its first change.
            The check always runs first, and <code>--check</code> runs it alone.
          </p>
          <div className="own-scroll">
            <table className="own-table own-pre" aria-label="Preflight checks">
              <thead><tr><th>Check</th><th>Stops (FAIL) when</th><th>Warns when</th><th>What to do</th></tr></thead>
              <tbody>
                {PREFLIGHT.map((r) => (
                  <tr key={r.check}>
                    <td data-label="Check"><b>{r.check}</b></td>
                    <td data-label="Stops when">{r.fail || "—"}</td>
                    <td data-label="Warns when">{r.warn || "—"}</td>
                    <td data-label="What to do">{r.fix || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <section className="card" aria-labelledby="own-chg">
          <h2 id="own-chg">What it changes, and how to undo it</h2>
          <div className="own-two">
            <div>
              <ul className="own-list">
                <li>Installs the packages it needs (libvirt, qemu and friends), enables <code>libvirtd</code>, and adds the operator user to the <code>libvirt</code> group.</li>
                <li>Clones the lab and Shimpaku at pinned, tested commits (not <code>main</code>), and copies your images into the lab's image folder.</li>
                <li>Creates the lab's libvirt network and domains, boots the devices, applies their starting configuration, and installs Shimpaku as a user service.</li>
                <li>Writes <code>/etc/modprobe.d/kvm-nested.conf</code> only if you pass <code>--enable-nested</code>.</li>
                <li>The dashboard listens on <code>127.0.0.1:8780</code> only. A fresh operator password is generated and printed once; <code>--bind</code> exposes it at your choice, with a warning.</li>
              </ul>
            </div>
            <div>
              <p className="own-p">Everything it creates is written to a manifest on the hypervisor, <code>/var/lib/shimpaku-own/manifest.json</code>. To remove it:</p>
              <CopyBlock label="Uninstall" text={`${PIPE} --host ${hostKind(f.host) ? q(f.host.trim()) : "<hypervisor-address>"}${/^[a-z_][a-z0-9_-]{0,31}$/i.test(f.user.trim()) && f.user.trim() !== "root" ? ` --user ${f.user.trim()}` : ""} --uninstall`} />
              <p className="small weak own-p">
                <code>--uninstall</code> removes exactly what the manifest lists and nothing else: it never touches a domain, network, bridge, firewall table or
                user it did not create. Add <code>--remove-key</code> to take the deploy's SSH key off the host as well. Packages that were already
                there stay.
              </p>
            </div>
          </div>
        </section>

        <section className="card" aria-labelledby="own-open">
          <h2 id="own-open">Open your dashboard</h2>
          <p className="weak own-p">When the installer finishes it prints this line, the dashboard address and your operator password. Run the tunnel on your computer and open it in a browser:</p>
          <CopyBlock label="On your computer" text={`${tunnel(f.host, f.user)}\n# then open http://127.0.0.1:8780`} />
          <p className="small weak own-p">Leave the tunnel running while you use the dashboard. It looks exactly like the one you are on now, except the devices are yours.</p>
        </section>

        <section className="card" aria-labelledby="own-more">
          <h2 id="own-more">More</h2>
          <ul className="own-list">
            <li>The full guide, with troubleshooting (nested KVM off, running in a VM, not enough RAM, conflicts, slow boots): {docs ? <a href={docs} target="_blank" rel="noreferrer">DEPLOY-YOUR-OWN.md ↗</a> : link("docs/DEPLOY-YOUR-OWN.md")}.</li>
            <li>What the hardware is: <Link to="/hardware">Hardware</Link> shows the seven devices and what they stand for. <Link to="/architecture">Architecture</Link> shows how the parts fit.</li>
            <li>Juniper's documentation: <a href={JUNIPER_ROUTER} target="_blank" rel="noreferrer">vJunos-router on KVM ↗</a>, <a href={JUNIPER_SWITCH} target="_blank" rel="noreferrer">vJunos-switch on KVM ↗</a>, <a href={JUNIPER_FREE} target="_blank" rel="noreferrer">free lab download ↗</a>.</li>
          </ul>
          <p className="small weak own-p">
            vJunos images are Juniper's, free for lab use under Juniper's terms; this project does not host or redistribute them. Independent project, not affiliated
            with or endorsed by Juniper Networks or HPE.
          </p>
        </section>
      </div>
    </>
  );
}
