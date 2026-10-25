# Hardware twin: anatomy, and the real hardware behind the lab's software — research

Retrieved: 2026-10-25 (every source below was retrieved this date). Quotes are verbatim from the
source; anything marked *(inference)* is ours, not the source's. No personal name or e-mail went
into any request, header or URL; GitHub was read with `gh api` (no extra headers).

## 1. `github.com/wheresryan22/anatomy`

Source: the repository at commit `1482e8469b` (2026-10-18, "Printable sheets: A4 and Letter PDFs
from any figure (#126)"), read through `gh api repos/wheresryan22/anatomy` and
`contents/{README.md,LICENSE,SKILL.md,kit/iso-kit.ts,kit/iso.css,kit/react/draw.tsx,kit/react/live.tsx}`.
— https://github.com/wheresryan22/anatomy

- **What it is.** README: "A Claude skill that explains an idea by building it as a real, working
  object." "Crafted, interactive isometric figures with many small precise parts, in SVG, WebGL and
  3D." It is an *Agent Skill* (a `SKILL.md` plus a drawing kit), not an npm package.
- **Licence.** MIT, "Copyright (c) 2026 Ryan" (`LICENSE`; GitHub reports `spdx_id: MIT`).
- **What it renders.** Three kinds: "SVG ... Solid isometric parts with four-tone shading, painted
  back to front, in a card with a live readout and eased motion"; "SVG + WebGL" for fire, water,
  glow; "3D (beta)". Examples: a dial indicator, a desk computer, an arcade cabinet, a Raptor engine.
- **Stack and size.** The core is `kit/iso-kit.ts` (38,631 bytes; the same as plain ESM in
  `iso-kit.mjs`), "Apart from React for those components, it has no dependencies." README badge:
  "dependencies 0"; Node 20.10+ only for its build scripts and headless-Chrome checks. The whole
  repository is ~12.8 MB (GitHub `size` 12817 KB), almost all of it built example pages and PDFs.
- **How it is used.** `npx skills add wheresryan22/anatomy` installs the skill; an agent then writes
  a build script that imports the kit and emits SVG/HTML, or React components (`kit/react/draw.tsx`:
  `Solid`, `Line`, `Wire`).
- **Its own limits (README).** "It is not a chart library. There are no axes, series or data plots.
  Numbers appear as a live readout driven by a model"; "under reduced motion every figure goes still."
- **Craft rules we adopt** (`SKILL.md`): "Let the kit shade. `extrude` returns four side tones by
  facing plus a lighter top"; "Truth first ... The readout, motion and caption must all be true";
  motion "goes still under reduced motion".

**Decision.** Installing the skill would add an agent workflow, not a runtime dependency, and the
full kit (WebGL, 3D, audits, PDF) is far more than a dashboard needs. The licence (MIT) allows
reuse with the notice kept, and the core is dependency-free TypeScript, so we **vendor a small
part**: the isometric camera, `roundedPlan`, `extrude` (four-tone side shading) and its helpers,
`slabOf`, `fitProjection`, `pathOf`, `sideMatrix`, plus the `Solid` React shape and the shading
CSS rules, into `web/src/vendor/anatomy/` with the MIT notice and the source commit. Everything
drawn with it (the hypervisor and its seven guests) is our own figure, and every LED, colour and
number on it comes from the live API. The flat front panels are our own SVG.

## 2. The real hardware that runs the same software

### vJunos-switch (sw1, spine1, spine2, leaf1, leaf2)

Source: "vJunos-switch Overview", Juniper Networks —
https://www.juniper.net/documentation/us/en/software/vjunos/vjunos-switch-kvm/topics/vjunos-switch-overview-understanding.html

- "The vJunos-switch is a virtual version of a Juniper switch that runs the Junos OS."
- "The vJunos-switch is built using EX9214 as a reference Juniper switch" and "supports a single
  Routing Engine and single Flexible PIC Concentrator (FPC)."
- "The vJunos-switch supports a bandwidth of up to 100 Mbps aggregated over all the interfaces."
- "The vJunos-switch is a single virtual machine (VM) that you can use only in labs and not in the
  production environment."
- Features: "Can simulate data center IP underlay and overlay topologies." "Supports EVPN-VXLAN leaf
  functionality", "Supports Edge-Routed Bridging (ERB)", "Supports EVPN LAG multihoming in
  EVPN-VXLAN (ESI-LAG)", "Supports up to 96 switch interfaces".
- Host: "an industry-standard x86 server" "running a Linux KVM hypervisor (Ubuntu 18.04, 20.04,
  22.04 or Debian 11 Bullseye)."

Source: "vJunos-switch Architecture" —
https://www.juniper.net/documentation/us/en/software/vjunos/vjunos-switch-kvm/topics/vjunos-switch-architecture-concept.html

- "The vJunos-switch is a single, nested VM solution in which the virtual forwarding plane (VFP)"
  "and the Packet Forwarding Engine (PFE) reside in the outer VM." "The term "nested" refers to the
  VCP VM being nested within the VFP VM".
- "The vJunos-switch can support up to 100 Mbps of throughput using 4 cores and 5GB of memory."
- "The vJunos-switch supports Gigabit Ethernet interfaces only."

Source: "Minimum Hardware and Software Requirements" (vJunos-switch) —
https://www.juniper.net/documentation/us/en/software/vjunos/vjunos-switch-kvm/topics/vjunos-switch-kvm-hw-requirements.html

- "A minimum of four cores are required." "The software allocates three cores to the VFP and one
  core to the VCP". "A minimum of 5GB memory is required." "Approximately 3GB memory will be
  allocated to VFP and 2 GB to the VCP."

### vJunos-router (r1, r2)

Source: "vJunos-router Overview", Juniper Networks —
https://www.juniper.net/documentation/us/en/software/vjunos-router/vjunos-router-kvm/topics/vjunos-router-overview-understanding.html

- "The vJunos-router is a virtual version of a Juniper router that runs the Junos OS."
- "The vJunos-router is built using vMX as a reference Juniper router" and "supports a single
  Routing Engine and single Flexible PIC Concentrator (FPC)."
- "Feature consistency with the physical MX Series platforms."
- "The vJunos-router supports a bandwidth of up to 100 Mbps aggregated over all the interfaces."
- Lab only: "can use only in labs and not in the production environment." "You don't have to
  purchase a bandwidth license for using the vJunos-router."

Source: "Minimum Hardware and Software Requirements for vJunos-router on KVM" —
https://www.juniper.net/documentation/us/en/software/vjunos-router/vjunos-router-kvm/topics/vjunos-router-kvm-hw-requirements.html

- "A minimum of four cores are required." "The software allocates three cores to the VFP and one
  core to the VCP, which is sufficient for most use cases." "A minimum of 5GB memory is required."

### What this means for the twin *(inference, stated on the page as such)*

- The switches run the Junos of an **EX9214-class** chassis switch (Juniper's own reference); the
  routers run the Junos of the **MX Series** (reference: vMX, "feature consistency with the
  physical MX Series"). Same control plane and CLI; **not** the same forwarding hardware: the
  forwarding plane is software in an x86 VM (VFP), so there is no ASIC fidelity, no line-rate
  behaviour, no optics, and throughput is capped at 100 Mbps per VM.
- One RE, one FPC, Gigabit Ethernet only: the front panels show `ge-0/0/N` ports on one FPC.
- The lab declares 4 vCPU / 5120 MiB per VM (`lab/topologies/*.toml`), exactly Juniper's minimum.
  Seven of them is 28 vCPUs and 35 GiB declared on one host.

### Trademark use

No Juniper logo, product photo, colour scheme or page layout is used. The front panels are an
original, generic drawing labelled "runs the same Junos as an EX9214-class switch (per Juniper
docs)", never presented as a Juniper image; the README already commits to not imitating Juniper's
sites ("the look, design, or overall commercial impression").

## 3. What the live lab reports (read-only look, 2026-10-25 ~10:46 UTC, via the SSH tunnel)

Read once from the running daemon (`/api/viz/topology`, `/api/live/traffic`, `/api/viz/devices/{sw1,r1}`,
`/api/ops/host`), then the tunnel was closed:

- All 7 devices reachable; `version` is `null` for every device (the daemon does not collect it).
- gNMI oper state (`device_get_health.ports`) lists logical interfaces too (`lo0`, `fxp0`, `irb`,
  `vtep`), so the twin keeps only physical `ge-/xe-/et-` names. r1's stored port map was empty.
- `iface_get_errors` (NETCONF) returns only ports that are up or have errors, so unwired ports are
  absent: the panels draw unreported positions as blanks and say so.
- Link rates: r1–r2 had "no counter samples for either end in the last 90 s"; r1–sw1 ~340 / 125
  bit/s. Rates are shown exactly as returned, `null` as "no sample".
- The host ran 7 `lab-<device>` domains plus other, unrelated VMs; the twin exposes only the lab
  guests' states (mapped to device names) to non-operators, never the other domain names.
</content>
</invoke>
