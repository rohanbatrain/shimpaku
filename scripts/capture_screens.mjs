#!/usr/bin/env node
// Recapture the documentation screenshots in docs/screens/ from the live public site, as a visitor.
//
//   node scripts/capture_screens.mjs                       # everything, into docs/screens/{desktop,mobile}
//   node scripts/capture_screens.mjs --only virga,deploy   # shots whose name contains one of these
//   node scripts/capture_screens.mjs --out /tmp/screens    # elsewhere (e.g. to look before replacing)
//   --gated    also take the pages a visitor cannot see (Junos console, Lab host, LLM gateway, the tester
//              invite page): they show "operator only" / "invite not valid" without an operator session or a
//              valid invite, so by default the committed operator-session captures of them are left alone
//
// Read-only: it only navigates and (on the phone) opens the navigation drawer. It never signs in,
// never types, never presses a button that changes anything. Lossless PNG, RGB, no palette reduction.
// Desktop is 1600x1000 @2x (viewport shot unless `full`), phone is 390x844 @2x (full page, cut at
// 2600 CSS px = 5200 px; `viewport` shots are one screen). The app keeps SSE streams open, so each
// page waits for domcontentloaded + a selector + a settle time, never networkidle.
//
// puppeteer-core: `npm install` in scripts/, or point PUPPETEER_MODULES at a directory that has it.
import { mkdirSync, readdirSync, rmSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const args = process.argv.slice(2);
const opt = (k, d) => { const i = args.indexOf(`--${k}`); return i >= 0 ? args[i + 1] : d; };
const OUT = resolve(opt("out", join(here, "..", "docs", "screens")));
const GATED = args.includes("--gated");
const ONLY = (opt("only", "") || "").split(",").filter(Boolean);
const CHROME = opt("chrome", process.env.CHROME_PATH || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

let puppeteer;
for (const dir of [here, process.env.PUPPETEER_MODULES].filter(Boolean)) {
  try { puppeteer = createRequire(dir.endsWith("/") ? dir : dir + "/")("puppeteer-core"); break; } catch { /* next */ }
}
if (!puppeteer) { console.error("puppeteer-core not found: run npm install in scripts/ or set PUPPETEER_MODULES"); process.exit(2); }

const APP = "https://shimpaku-app.rohanbatra.in";
const SITE = "https://shimpaku.rohanbatra.in";
const STATUS_A = "https://shimpaku-status.rohanbatra.in";
const STATUS_B = "https://shimpaku-status.shaktawat.in";
const INC = "INC-20261024-0839-fabric";
const ACT_VERIFIED = "act-20261025-838aed";
const ACT_RECURRED = "act-20261024-acdfc3";
const ACT_BLOCKED = "act-20261024-540b02";

// name: file stem; url; sel: element that proves the page rendered; settle: ms after it appears;
// d / m: how to take it on desktop / phone ("viewport" | "full"); nav: phone shot of the open drawer.
const SHOTS = [
  { name: "01-overview", url: `${APP}/`, sel: ".content h1, main h1", settle: 9000, d: "viewport", m: "full" },
  { name: "02-topology-live-traffic", url: `${APP}/topology`, settle: 9000 },
  { name: "03-service-levels-campus", url: `${APP}/sle?site=campus`, settle: 9000 },
  { name: "04-service-levels-fabric", url: `${APP}/sle?site=fabric`, settle: 9000 },
  { name: "05-probe-heatmap", url: `${APP}/probes`, settle: 7000, m: "viewport" },
  { name: "06-live-events", url: `${APP}/events`, settle: 8000 },
  { name: "07-root-cause-blast-radius", url: `${APP}/root-cause/${INC}`, settle: 9000 },
  { name: "08-actions", url: `${APP}/actions`, settle: 7000 },
  { name: "09-action-verified-real-run", url: `${APP}/actions/${ACT_VERIFIED}`, settle: 8000 },
  { name: "10-action-recurred", url: `${APP}/actions/${ACT_RECURRED}`, settle: 8000 },
  { name: "11-action-blocked-by-guardrail", url: `${APP}/actions/${ACT_BLOCKED}`, settle: 7000 },
  { name: "12-theatre-real-run", url: `${APP}/theatre?action=${ACT_VERIFIED}`, settle: 9000, click: "Skip to the end" },
  { name: "13-guardrail-funnel", url: `${APP}/guardrails`, settle: 8000 },
  { name: "14-audit-chain", url: `${APP}/audit`, settle: 8000 },
  { name: "15-benchmark", url: `${APP}/benchmark`, settle: 8000 },
  { name: "16-architecture", url: `${APP}/architecture`, settle: 7000 },
  { name: "17-junos-console", gated: true, url: `${APP}/console`, settle: 7000, m: "viewport" },
  { name: "18-device-sw1", url: `${APP}/devices/sw1`, settle: 9000 },
  { name: "19-ops-lab-host", gated: true, url: `${APP}/ops/host`, settle: 9000 },
  { name: "20-ops-llm-gateway", gated: true, url: `${APP}/ops/gateway`, settle: 8000, m: "viewport" },
  { name: "21-settings", url: `${APP}/settings`, settle: 7000 },
  { name: "22-tester-pass", gated: true, url: `${APP}/start`, settle: 5000, m: "viewport" },
  { name: "23-request-live-session", url: `${APP}/request`, settle: 5000, m: "viewport" },
  { name: "24-hardware-digital-twin", url: `${APP}/hardware`, settle: 10000 },
  { name: "25-fault-lab-virga", url: `${APP}/virga`, settle: 10000, d: "full" },
  { name: "26-deploy-your-own", url: `${APP}/deploy`, settle: 7000, d: "full" },
  { name: "27-landing-page", url: `${SITE}/`, landing: true, settle: 8000, d: "full", noDrawer: true },
  { name: "28-public-status-rohanbatra", url: `${STATUS_A}/`, settle: 8000, d: "full", noDrawer: true },
  { name: "29-public-status-shaktawat", url: `${STATUS_B}/`, settle: 8000, d: "full", noDrawer: true },
  // the sidebar, tall enough that the whole nav (Virga, Uptime, Deploy…) shows; desktop only
  { name: "31-overview-with-virga-tile", url: `${APP}/`, settle: 9000, d: "full", m: "none" },
  { name: "30-sidebar-with-uptime", url: `${APP}/`, settle: 6000, d: "nav", m: "none" },
];

const MAX_CSS = 2600;

// The live site falls back to "recorded replay" / "lab offline" for a while when its live reads are slow
// (for example while /api/hardware is being fetched). A screenshot taken then is not of the live lab, so
// look for the tell-tale text and take the page again later.
async function liveProblem(page, s) {
  return page.evaluate((isLanding) => {
    const t = document.body.innerText;
    if (/Recorded replay, read-only|The lab is offline right now/i.test(t)) return "page says the lab is offline (recorded replay)";
    if (/from 0 stored proposals/.test(t)) return "guardrail funnel computed from 0 proposals";
    if (isLanding) {
      const ok = document.querySelector('#live[data-state="live"] li.ok');
      if (!ok || !/online/.test(ok.innerText)) return "landing status strip is not showing the lab online";
    }
    return "";
  }, !!s.landing);
}

// A page is settled when no loading skeleton is left and its content height has stopped changing.
// (/api/hardware can take ~17 s on the live site, and the browser's per-host connection limit queues
// other requests behind the open SSE streams, so a fixed sleep is not enough.)
async function settled(page, name) {
  try { await page.waitForFunction(() => !document.querySelector(".skel"), { timeout: 70000, polling: 500 }); }
  catch { console.warn("WARN  skeleton still showing after 70 s:", name); }
  let last = -1, same = 0;
  for (let i = 0; i < 20 && same < 3; i++) {
    const h = await page.evaluate(() => { const m = document.querySelector("main.main"); return Math.max(document.documentElement.scrollHeight, m ? m.scrollHeight : 0); });
    same = h === last ? same + 1 : 0; last = h; await sleep(1000);
  }
}
const modes = [
  { dir: "desktop", w: 1600, h: 1000, mobile: false },
  { dir: "mobile", w: 390, h: 844, mobile: true },
];

const browser = await puppeteer.launch({ executablePath: CHROME, headless: true });
const mkdir = (d) => mkdirSync(d, { recursive: true });
let failed = 0;
try {
  for (const mode of modes) {
    mkdir(join(OUT, mode.dir));
    const page = await browser.newPage();
    await page.setViewport({ width: mode.w, height: mode.h, deviceScaleFactor: 2, isMobile: mode.mobile, hasTouch: mode.mobile });
    for (const s of SHOTS) {
      if (ONLY.length && !ONLY.some((o) => s.name.includes(o))) continue;
      if (s.gated && !GATED) continue;
      const how = (mode.mobile ? s.m : s.d) ?? (mode.mobile ? "full" : "viewport");
      if (how === "none") continue;
      const file = join(OUT, mode.dir, `${s.name}.png`);
      try {
        for (let attempt = 1; ; attempt++) {
          await page.goto(s.url, { waitUntil: "domcontentloaded", timeout: 45000 });
          await page.waitForSelector(s.sel || "h1", { timeout: 30000 });
          await sleep(s.settle ?? 6000);
          await settled(page, s.name);
          if (s.click) { // a playback control on the read-only replay, nothing that writes
            const hit = await page.evaluate((t) => { const b = [...document.querySelectorAll("button")].find((x) => x.textContent.trim() === t); if (b) b.click(); return !!b; }, s.click);
            console.log(hit ? `     clicked "${s.click}"` : `     (no "${s.click}" button)`);
            await sleep(3000);
          }
          // scroll the whole page once so lazy charts mount, then back to the top
          await page.evaluate(async () => {
            const el = document.querySelector("main.main") || document.scrollingElement;
            const h = Math.max(el.scrollHeight, document.documentElement.scrollHeight);
            for (let y = 0; y < Math.min(h, 5200); y += 600) { el.scrollTo(0, y); window.scrollTo(0, y); await new Promise((r) => setTimeout(r, 150)); }
            el.scrollTo(0, 0); window.scrollTo(0, 0);
          });
          await sleep(1500);
          await settled(page, s.name);
          if (how === "nav") {
            const need = await page.$eval("#main-nav", (n) => n.scrollHeight);
            const side = await page.$eval("#main-nav", (n) => Math.ceil(n.getBoundingClientRect().right));
            const vh = Math.max(mode.h, need + 140);
            await page.setViewport({ width: mode.w, height: vh, deviceScaleFactor: 2 });
            await sleep(1000);
            await page.screenshot({ path: file, type: "png", clip: { x: 0, y: 0, width: side, height: vh } });
            await page.setViewport({ width: mode.w, height: mode.h, deviceScaleFactor: 2, isMobile: mode.mobile, hasTouch: mode.mobile });
          } else if (how === "viewport") {
            await page.screenshot({ path: file, type: "png" });
          } else {
            const h = await page.evaluate(() => {
              const m = document.querySelector("main.main");
              return Math.max(document.documentElement.scrollHeight, m ? m.scrollHeight : 0);
            });
            const clipH = Math.min(h, MAX_CSS);
            if (!mode.mobile && (await page.$("main.main"))) {
              // desktop app pages scroll inside .main with the shell pinned: grow the window to the content
              await page.setViewport({ width: mode.w, height: Math.max(mode.h, clipH), deviceScaleFactor: 2 });
              await sleep(1500);
              await page.screenshot({ path: file, type: "png" });
              await page.setViewport({ width: mode.w, height: mode.h, deviceScaleFactor: 2 });
            } else {
              await page.screenshot({ path: file, type: "png", clip: { x: 0, y: 0, width: mode.w, height: clipH }, captureBeyondViewport: true });
            }
          }
          // the page may have flipped to its recorded-replay fallback while we waited: check at the end too
          const bad = await liveProblem(page, s);
          if (!bad) break;
          console.warn(`WARN  ${s.name}: ${bad} (attempt ${attempt})`);
          if (attempt >= 6) { console.warn("WARN  giving up, keeping the last capture"); break; }
          await sleep(30000);
        }
        console.log("ok  ", mode.dir, s.name, how);
      } catch (e) {
        failed++;
        console.error("FAIL", mode.dir, s.name, String(e).slice(0, 200));
      }
      // the phone navigation drawer: open the menu (a toggle, nothing else) on the overview
    }
    if (mode.mobile && (!ONLY.length || ONLY.some((o) => "00-navigation-drawer".includes(o)))) {
      const file = join(OUT, mode.dir, "00-navigation-drawer.png");
      try {
        await page.goto(`${APP}/`, { waitUntil: "domcontentloaded", timeout: 45000 });
        await page.waitForSelector("button.appbar-menu", { timeout: 30000 });
        await sleep(6000);
        await page.click("button.appbar-menu");
        await sleep(1200);
        await page.screenshot({ path: file, type: "png" });
        console.log("ok  ", mode.dir, "00-navigation-drawer");
      } catch (e) { failed++; console.error("FAIL drawer", String(e).slice(0, 200)); }
    }
    await page.close();
  }
} finally {
  await browser.close();
}
process.exit(failed ? 1 : 0);
