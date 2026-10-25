// Set up Uptime Kuma (v2) for the Shimpaku status page, over its own socket.io protocol (Kuma has no
// REST API for this). Idempotent: creates the admin only on a fresh instance, and for everything else
// creates what is missing and brings what exists to the desired settings (only when they differ).
//
//   KUMA_URL=http://127.0.0.1:3001 KUMA_USER=... KUMA_PASS=... \
//     node kuma_setup.mjs [--app <app-url>] [--group <name>] <url> [<url> ...] [--group <name> <url> ...]
//
//   <url> ...     the website monitors (HTTP, status 2xx): landing page, dashboard, status page.
//   --group <name>  the status-page group for the URLs after it (one per domain, e.g. "Example (host)").
//                 URLs before any --group go in the group "Website". A "Website" group left empty is removed.
//   --app <url>   the dashboard's base URL (or KUMA_APP_URL). Adds the group "Live lab": three keyword
//                 monitors on <url>/api/public/health (docs/build/virga-live.md §2) that turn red while
//                 a Virga fault drill is running, and green again after the revert:
//                   Campus network          "campus":"healthy"
//                   Fabric network          "fabric":"healthy"
//                   No fault drill running  "drill":"idle"
//                 Without --app the Live lab group is not created or changed (today's usage).
//
// Credentials come from the environment (never argv), and nothing secret is printed.
//
// The keyword is a plain substring of the response body. The daemon's JSON is compact (FastAPI's
// default: {"campus":"healthy",...}), so the keywords match as written. As far as Kuma's source goes it also
// stringifies a body it parsed as JSON (compactly) before matching, so spacing should not matter; a non-JSON body (an HTML error page
// from the edge) never contains the keyword, which is the red you want.
import { io } from "socket.io-client";

const URL = process.env.KUMA_URL || "http://127.0.0.1:3001";
const USER = process.env.KUMA_USER, PASS = process.env.KUMA_PASS;
const SLUG = process.env.KUMA_SLUG || "shimpaku";

const GROUP_WEBSITE = "Website", GROUP_LIVE = "Live lab";
const targets = [];   // { group, url }
let appUrl = process.env.KUMA_APP_URL || "", current = GROUP_WEBSITE;
const argv = process.argv.slice(2);
for (let i = 0; i < argv.length; i++) {
  if (argv[i] === "--app") appUrl = argv[++i] || "";
  else if (argv[i].startsWith("--app=")) appUrl = argv[i].slice(6);
  else if (argv[i] === "--group") current = argv[++i] || GROUP_WEBSITE;
  else targets.push({ group: current, url: argv[i] });
}
appUrl = appUrl.replace(/\/+$/, "");
if (!USER || !PASS || targets.length === 0) {
  console.error("need KUMA_USER, KUMA_PASS and at least one URL (optionally --app <dashboard url>)");
  process.exit(2);
}

const LIVE = [
  { name: "Campus network", keyword: '"campus":"healthy"' },
  { name: "Fabric network", keyword: '"fabric":"healthy"' },
  { name: "No fault drill running", keyword: '"drill":"idle"' },
];

const s = io(URL, { transports: ["websocket"], reconnection: false, timeout: 15000 });
const call = (ev, ...args) => new Promise((res, rej) => {
  const t = setTimeout(() => rej(new Error(`${ev}: no answer`)), 20000);
  s.emit(ev, ...args, (r) => { clearTimeout(t); res(r); });
});
let monitors = {};
s.on("monitorList", (m) => { monitors = m || {}; });
// Kuma 2 also sends incremental changes after an add or edit
s.on("updateMonitorIntoList", (m) => { Object.assign(monitors, m || {}); });
s.on("deleteMonitorFromList", (id) => { delete monitors[id]; });
const fail = (m) => { console.error("FAILED:", m); s.close(); process.exit(1); };

// What a new monitor of each kind starts from (the fields Kuma 2's `add` expects).
const base = (extra) => ({
  method: "GET", interval: 60, retryInterval: 60, resendInterval: 0, maxretries: 2, timeout: 48, maxredirects: 10,
  accepted_statuscodes: ["200-299"], ignoreTls: false, upsideDown: false, expiryNotification: true,
  notificationIDList: {}, active: true, conditions: [], kafkaProducerBrokers: [], kafkaProducerSaslOptions: {},
  ...extra,
});
const same = (a, b) => JSON.stringify(a ?? null) === JSON.stringify(b ?? null);

// Create `want` if no monitor has its name; else bring the fields in `want` to their values with
// `editMonitor`, sending the monitor as Kuma listed it plus the changes (what the admin UI does, so no
// field is reset). Nothing is sent when nothing differs. A refused edit is reported and skipped.
async function ensure(want, defaults) {
  const have = Object.values(monitors).find((m) => m.name === want.name);
  if (!have) {
    const r = await call("add", { ...defaults, ...want });
    if (!r?.ok) fail(`add ${want.name}: ${r?.msg}`);
    console.log(`monitor added: ${want.name}`);
    return r.monitorID;
  }
  const diff = Object.keys(want).filter((k) => !same(have[k], want[k]));
  if (diff.length === 0) { console.log(`monitor ok: ${want.name}`); return have.id; }
  const r = await call("editMonitor", { ...have, ...want });
  if (r?.ok) console.log(`monitor updated: ${want.name} (${diff.join(", ")})`);
  else console.log(`monitor skipped: ${want.name} differs in ${diff.join(", ")} but Kuma refused the edit (${r?.msg}); change it in the admin UI`);
  return have.id;
}

s.on("connect_error", (e) => fail(`cannot reach Kuma at ${URL}: ${e.message}`));
s.on("connect", async () => {
  try {
    const need = await call("needSetup");
    if (need) {
      const r = await call("setup", USER, PASS);
      if (!r?.ok) fail(`setup: ${r?.msg}`);
      console.log("admin account created");
    }
    const l = await call("login", { username: USER, password: PASS, token: "" });
    if (!l?.ok) fail(`login: ${l?.msg || "refused"} (an admin already exists with other credentials?)`);
    console.log("signed in");
    await new Promise((r) => setTimeout(r, 1500)); // monitorList arrives after login

    const group = async (name) => {
      const have = Object.values(monitors).find((m) => m.name === name && m.type === "group");
      if (have) { console.log(`group ok: ${name}`); return have.id; }
      const r = await call("add", base({ type: "group", name, maxretries: 0, expiryNotification: false }));
      if (!r?.ok) fail(`add group ${name}: ${r?.msg}`);
      console.log(`group added: ${name}`);
      return r.monitorID;
    };

    // Website groups (one per --group, in order): the HTTP monitors, named by their host.
    const groups = [];
    for (const g of [...new Set(targets.map((t) => t.group))]) {
      const gid = await group(g);
      const ids = [];
      for (const { url } of targets.filter((t) => t.group === g)) {
        const name = url.replace(/^https?:\/\//, "").replace(/\/$/, "");
        ids.push(await ensure({ name, parent: gid }, base({ type: "http", url })));
      }
      groups.push({ name: g, monitorList: ids.map((id) => ({ id })) });
    }
    // the default group from an earlier run, now that its monitors moved to per-domain groups
    const old = Object.values(monitors).find((m) => m.name === GROUP_WEBSITE && m.type === "group");
    if (old && !groups.some((g) => g.name === GROUP_WEBSITE)) {
      await new Promise((r) => setTimeout(r, 1500)); // monitorList refreshes after the edits
      if (!Object.values(monitors).some((m) => m.parent === old.id)) {
        const r = await call("deleteMonitor", old.id);
        console.log(r?.ok ? `group removed: ${GROUP_WEBSITE} (empty)` : `group ${GROUP_WEBSITE} left: ${r?.msg}`);
      }
    }

    // Live lab: keyword monitors on the daemon's anonymous health endpoint, via the public edge.
    if (appUrl) {
      const liveId = await group(GROUP_LIVE);
      const live = [];
      for (const m of LIVE) {
        live.push(await ensure({
          name: m.name, type: "keyword", url: `${appUrl}/api/public/health`, keyword: m.keyword, invertKeyword: false,
          interval: 20, retryInterval: 20, maxretries: 0, timeout: 10, parent: liveId,
        }, base({ expiryNotification: false })));
      }
      groups.push({ name: GROUP_LIVE, monitorList: live.map((id) => ({ id })) });
    } else {
      console.log("no --app given: the Live lab group is left as it is");
      const have = Object.values(monitors).filter((m) => LIVE.some((x) => x.name === m.name)).map((m) => ({ id: m.id }));
      if (have.length) groups.push({ name: GROUP_LIVE, monitorList: have });
    }

    const add = await call("addStatusPage", "Shimpaku", SLUG);
    if (add?.ok) console.log(`status page created: /status/${SLUG}`);
    else if (!/exist|taken|used|UNIQUE/i.test(add?.msg || "")) fail(`addStatusPage: ${add?.msg}`);
    else console.log(`status page exists: /status/${SLUG}`);

    const config = {
      slug: SLUG, title: "Shimpaku",
      description: "Live status of the Shimpaku site and its lab. The \"Live lab\" group goes red on purpose " +
        "while the operator injects a real fault into the lab with Virga, and green again once it is reverted and verified.",
      icon: "/icon.svg", theme: "auto", published: true, showTags: false, showPoweredBy: false,
      showCertificateExpiry: true, footerText: "Independent project. Not affiliated with HPE or Juniper Networks.",
      customCSS: "", analyticsType: null, analyticsId: null, analyticsScriptUrl: null, domainNameList: [], autoRefreshInterval: 300,
    };
    const save = await call("saveStatusPage", SLUG, config, "/icon.svg", groups);
    if (!save?.ok) fail(`saveStatusPage: ${save?.msg}`);
    console.log(`status page published: ${groups.map((g) => `${g.name} (${g.monitorList.length})`).join(", ")}`);
    s.close(); process.exit(0);
  } catch (e) { fail(e.message); }
});
