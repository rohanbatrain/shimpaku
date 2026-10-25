// Plain-language explanations behind every (i) button: written for someone who has never run a
// network. One entry per idea, so the same word is explained the same way on every page.

export type Explainer = { title: string; body: string };

export const TERMS = {
  lab: { title: "The lab", body: "Seven real network devices (Juniper's Junos software, the same that runs on big switches and routers) running as virtual machines on one computer. Everything you see is read from them live." },
  vjunos: { title: "vJunos", body: "Juniper's own operating system for switches and routers, packaged to run as a virtual machine. Same commands, same behaviour as the physical boxes, just without the hardware." },
  hypervisor: { title: "Hypervisor", body: "The one physical computer that hosts all seven virtual devices, like one building holding seven offices. Each box on the drawing is one virtual device; its little lights are its real ports." },
  topology: { title: "Topology", body: "A map of which device is cabled to which. Lines are the intended connections; their colour is what the devices report right now (green up, amber struggling, red down)." },
  view3d: { title: "2D or 3D", body: "2D is the classic network diagram. 3D draws each device as the actual virtual machine, standing where it sits on the map, with its live CPU bar and port lights. Same live data, two ways to look at it." },
  sites: { title: "Campus and fabric", body: "Two small networks. Campus is like an office: two routers and a switch. Fabric is like a data centre: two 'spine' switches every 'leaf' switch connects to, so any one cable can fail without cutting anyone off." },
  sle: { title: "Service level", body: "A score for how well the network served people, minute by minute. Each minute is judged good or bad against a goal; the percentage is the share of good minutes. 'n' is how many minutes (or tests) the score is based on, so you can tell a solid number from a guess." },
  device_minutes: { title: "Device-minutes", body: "Every device gets a verdict every minute: good or bad (for example CPU too high, or it stopped answering). '57 good / n = 59' means 57 of the last 59 minutes were fine." },
  probes: { title: "Probes", body: "Small test messages the devices send to each other every few seconds, like a ping, to measure whether apps would get through and how fast. Lost or slow probes are the first sign users would notice." },
  incident: { title: "Incident", body: "Opened automatically when a service level stays below its goal. It names the likely cause and everything affected, so nobody has to dig through raw numbers." },
  blast: { title: "Blast radius", body: "Everything a problem touches: which devices, links and users were affected. Shown only when the numbers are strong enough to be meaningful (the 'significance gate')." },
  actions: { title: "Actions", body: "A proposed change to a device, like 'add VLAN 999 to this port'. Anyone (a person, Shim, an outside AI agent) can propose; only a signed-in human operator can approve, and nothing touches a device before that." },
  approval: { title: "Approval", body: "The human check. A proposal waits here until an operator reads what it will change and why, and clicks Approve. AI agents can propose but can never approve." },
  guardrails: { title: "Guardrails G0–G9", body: "Ten automatic safety checks that run on every proposal, before a human even sees it: is the request well formed, is it the right kind of port, has this port been bounced too often, is there evidence, and so on. One failed check blocks the change." },
  commit_confirmed: { title: "Commit confirmed", body: "A Junos safety net: the change is applied, but the device undoes it by itself when a short timer runs out (about 75 seconds here) unless we confirm it. We confirm only after checking the device is still healthy, so a bad change rolls itself back even if this system crashes." },
  validation: { title: "Validation window", body: "After a change is confirmed, we keep watching for a few minutes. If the problem stays fixed it is 'verified'; if it comes back it is marked 'recurred'." },
  audit: { title: "Audit chain", body: "A log of every call anyone made to this system. Each row carries a fingerprint (hash) of itself plus the row before, like links of a chain: change any old row and every link after it stops matching, so tampering is visible." },
  tamper: { title: "Tamper test (simulation)", body: "Shows what happens if someone edits the log. The server copies a few rows into memory, changes the copy and recomputes the fingerprints. The real log is never touched: nobody, including visitors, can edit it from here." },
  rehash: { title: "Edit and re-hash", body: "A smarter forger also recomputes the edited row's fingerprint. The chain still breaks at the next row, because that row's fingerprint was built on the original." },
  shim: { title: "Shim", body: "The built-in AI assistant. It answers by calling the same read-only tools as this dashboard and shows each call it made, so you can check its work. It can propose a fix, never approve one." },
  trace: { title: "Reasoning trace", body: "The steps behind one Shim answer: your question, every tool it called (with timings), and the answer. Useful to see why it said what it said." },
  mcp: { title: "MCP", body: "Model Context Protocol: a standard way for AI agents to use tools. Here it is the one and only door to the devices, for Shim and for outside agents alike, with the same guardrails and log." },
  gnmi: { title: "gNMI telemetry", body: "The devices stream their counters (traffic, CPU, port state) to us every 10 seconds on their own, instead of us asking each time." },
  netconf: { title: "NETCONF", body: "The structured way we read from, and (after approval) write to, a Junos device." },
  ospf: { title: "OSPF and BFD", body: "OSPF is how routers tell each other which paths exist. BFD is a fast heartbeat on each link. 'Full' and 'Up' mean the neighbours see each other." },
  vlan: { title: "VLAN and trunk", body: "A VLAN is a separate virtual network sharing the same cables. A trunk is a port that carries several VLANs. The demo removes VLAN 999 from one trunk; the fix puts it back." },
  timetravel: { title: "Time travel", body: "Drag the slider to redraw the page as it was at an earlier minute, from stored history. 'Live' returns to now." },
  live_feed: { title: "Live feed", body: "The latest things that happened: proposed or approved changes, incidents opening and closing, commits on devices. Newest first." },
  junos_events: { title: "Junos events", body: "Messages the devices themselves log (ports going up or down, probes, alarms), streamed as they happen." },
  traffic: { title: "Traffic", body: "Moving dashes show data flowing on a link, one lane per direction; thicker means more bits per second." },
  theatre: { title: "Theatre", body: "One screen that follows a full break-and-fix: a fault is injected, the system notices, a fix is proposed, guardrails check it, a human approves, the device is changed safely, and the result is verified. Every time shown is a recorded event." },
  virga: { title: "Virga fault lab", body: "Virga is the fault injector: a separate program that breaks the lab on purpose, from a catalogue of realistic faults, then checks whether Shimpaku noticed and fixed it. It is how the system is tested against faults it has not seen." },
  vg_impact: { title: "User impact", body: "Whether the fault would hurt people using the network (calls drop, pages fail) or stays latent: broken underneath but nobody notices yet. Latent faults test whether Shimpaku spots trouble before users do." },
  vg_selftest: { title: "Self-test", body: "Before a fault may run live, Virga injects and reverts it on its own several times to prove it can always put the lab back. '5/5' means five clean runs out of five." },
  vg_hold: { title: "Hold", body: "How long the fault stays in place before Virga reverts it. This is the window in which Shimpaku has to notice it, explain it and propose a fix." },
  vg_live: { title: "Runnable live", body: "Faults this lab can actually inject now. The others need extra lab parts (a Wi-Fi simulator, a RADIUS or DNS server, a second access NIC) that this home lab does not have yet; they stay in the catalogue so the full test plan is visible." },
  benchmark: { title: "Benchmark", body: "Scores from running Virga's fault catalogue against the system: how many faults were detected, correctly explained and safely fixed, and how fast." },
  replay: { title: "Replay", body: "When the lab is offline, the public site shows a recorded snapshot instead, clearly marked. Nothing can be changed in a replay." },
  readonly: { title: "Read-only view", body: "You are watching the real lab but cannot change anything. Request a live session to try the break-and-fix yourself, or sign in as the operator." },
} satisfies Record<string, Explainer>;

export type Term = keyof typeof TERMS;

// "About this page", by route prefix (longest match wins)
export const PAGES: [string, Explainer][] = [
  ["/topology", { title: "About Topology", body: "Both networks and every intended cable between devices, coloured by what the devices report right now. Click a device for its details, a line for its traffic. Use 2D / 3D above the map to switch drawings, and the slider to see an earlier minute." }],
  ["/hardware", { title: "About Hardware", body: "The machine behind the lab: one computer running seven virtual Junos devices, each drawn with its real ports. The lights are live: green link up, red down, blinking with traffic. Click a device to see its front panel." }],
  ["/devices/", { title: "About this device", body: "One device in depth: its role, CPU and memory, whether it is answering, its front panel, its traffic and each port's error counters since they were last cleared." }],
  ["/events", { title: "About Events", body: "Everything the devices log, as it happens: ports changing state, probe results, alarms. Filter by device or severity." }],
  ["/sle", { title: "About Service levels", body: "How well the network served people, minute by minute, per site. Each score shows its goal and 'n', how much data it rests on. Click a bad minute to see what caused it." }],
  ["/root-cause", { title: "About Root cause", body: "Each incident with its likely cause, the evidence for it, and its blast radius (everything it affected), drawn on the map." }],
  ["/actions", { title: "About Actions", body: "Every proposed change and where it stands: waiting for approval, blocked by a guardrail, applied, verified. Only a signed-in human can approve; every step is logged." }],
  ["/theatre", { title: "About Theatre", body: "Follows one break-and-fix from start to finish: the fault, the first sign of trouble, the proposed fix, the safety checks, the human approval, the change on the device and the verdict. Visitors see a replay of the last completed run with its real timings." }],
  ["/virga", { title: "About the Virga fault lab", body: "Virga breaks the lab on purpose with realistic faults, then watches whether Shimpaku detects, explains and fixes them. A drill is real: a real device is changed, and Virga puts it back." }],
  ["/guardrails", { title: "About Guardrails", body: "The ten safety checks every proposed change must pass, and how many proposals each one stopped. Click a check to see what it looks at." }],
  ["/audit", { title: "About the Audit chain", body: "Every call to this system, in a tamper-evident log. 'Chain verified' means every row's fingerprint still matches. Use the tamper test to see a forgery caught: it runs on a copy in memory, never on the real log." }],
  ["/assistant/trace", { title: "About Shim's reasoning", body: "Each answer Shim gave, broken into steps: the question, every tool call with its timing and result, and the final answer. Kept in this browser only." }],
  ["/shim", { title: "About Shim", body: "Chat with the built-in assistant. It answers from the lab's live data and shows every tool call it made; it can propose a fix but never approve it. Your conversations stay in this browser." }],
  ["/benchmark", { title: "About Benchmark", body: "How the system scored against Virga's catalogue of faults: detection, correct explanation, safe fix, and time taken." }],
  ["/architecture", { title: "About Architecture", body: "How the parts fit together. One program owns all the data; one door (the MCP server) is the only way to a device. Click any box for what it does and the code behind it." }],
  ["/probes", { title: "About the Probe heatmap", body: "Each cell is one minute of test messages between two devices: darker means more lost or slower. A dark row or column points at the device or path in trouble." }],
  ["/", { title: "About this dashboard", body: "Shimpaku watches a small real network, explains problems in plain terms, and fixes them safely: AI may propose a change, a human approves it, and the device itself rolls back a bad change. Start here for the overall health; every (i) explains what you are looking at." }],
];

export function pageExplainer(path: string): Explainer | null {
  const hit = PAGES.filter(([p]) => (p === "/" ? path === "/" : path.startsWith(p))).sort((a, b) => b[0].length - a[0].length)[0];
  return hit ? hit[1] : null;
}
