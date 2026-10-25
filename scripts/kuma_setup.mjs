// Set up Uptime Kuma (v2) for the Shimpaku status page, over its own socket.io protocol (Kuma has no
// REST API for this). Idempotent: creates the admin only on a fresh instance, skips monitors and the
// status page that already exist.
//
//   KUMA_URL=http://127.0.0.1:3001 KUMA_USER=... KUMA_PASS=... node kuma_setup.mjs <url> [<url> ...]
//
// Credentials come from the environment (never argv), and nothing secret is printed.
import { io } from "socket.io-client";

const URL = process.env.KUMA_URL || "http://127.0.0.1:3001";
const USER = process.env.KUMA_USER, PASS = process.env.KUMA_PASS;
const SLUG = process.env.KUMA_SLUG || "shimpaku";
const targets = process.argv.slice(2);
if (!USER || !PASS || targets.length === 0) { console.error("need KUMA_USER, KUMA_PASS and at least one URL"); process.exit(2); }

const s = io(URL, { transports: ["websocket"], reconnection: false, timeout: 15000 });
const call = (ev, ...args) => new Promise((res, rej) => {
  const t = setTimeout(() => rej(new Error(`${ev}: no answer`)), 20000);
  s.emit(ev, ...args, (r) => { clearTimeout(t); res(r); });
});
let monitors = {};
s.on("monitorList", (m) => { monitors = m || {}; });
const fail = (m) => { console.error("FAILED:", m); s.close(); process.exit(1); };

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

    const ids = [];
    for (const url of targets) {
      const name = url.replace(/^https?:\/\//, "").replace(/\/$/, "");
      const have = Object.values(monitors).find((m) => m.name === name);
      if (have) { console.log(`monitor exists: ${name}`); ids.push(have.id); continue; }
      const r = await call("add", {
        type: "http", name, url, method: "GET", interval: 60, retryInterval: 60, resendInterval: 0,
        maxretries: 2, timeout: 48, maxredirects: 10, accepted_statuscodes: ["200-299"],
        ignoreTls: false, upsideDown: false, expiryNotification: true, notificationIDList: {},
        active: true, conditions: [], kafkaProducerBrokers: [], kafkaProducerSaslOptions: {},
      });
      if (!r?.ok) fail(`add ${name}: ${r?.msg}`);
      console.log(`monitor added: ${name}`); ids.push(r.monitorID);
    }

    const add = await call("addStatusPage", "Shimpaku", SLUG);
    if (add?.ok) console.log(`status page created: /status/${SLUG}`);
    else if (!/exist|taken|used|UNIQUE/i.test(add?.msg || "")) fail(`addStatusPage: ${add?.msg}`);
    else console.log(`status page exists: /status/${SLUG}`);

    const config = {
      slug: SLUG, title: "Shimpaku", description: "Live status of the Shimpaku site and its lab.",
      icon: "/icon.svg", theme: "auto", published: true, showTags: false, showPoweredBy: false,
      showCertificateExpiry: true, footerText: "Independent project. Not affiliated with HPE or Juniper Networks.",
      customCSS: "", analyticsType: null, analyticsId: null, analyticsScriptUrl: null, domainNameList: [], autoRefreshInterval: 300,
    };
    const save = await call("saveStatusPage", SLUG, config, "/icon.svg",
      [{ name: "Shimpaku", monitorList: ids.map((id) => ({ id })) }]);
    if (!save?.ok) fail(`saveStatusPage: ${save?.msg}`);
    console.log(`status page published with ${ids.length} monitors`);
    s.close(); process.exit(0);
  } catch (e) { fail(e.message); }
});
