#!/usr/bin/env node
// Record the landing-page clip of theatre mode: a 30–60 s MP4 (or WebM) + a poster PNG.
//
//   cd scripts && npm install                       # puppeteer-core only; uses your installed Chrome
//   node record_demo.mjs --base http://127.0.0.1:8793                       # demo backend: drives Break → Propose → Approve
//   node record_demo.mjs --base http://127.0.0.1:8780 --replay --action act-…   # a real run, read-only replay
//
// Demo mode (default) needs the DEMO backend (SHIMPAKU_DEMO=1, auth off) and presses the theatre's
// own Break / Propose via Shim / Approve buttons, which in demo mode touch no device. It refuses to
// drive a backend whose /api/whoami does not say demo: true. --replay never presses anything: it
// opens /theatre?action=<id>&replay=1 and films the replay of that completed run, which plays the
// run's recorded timings (gaps over 12 s fast-forwarded, as the page labels them).
//
// Frames come from CDP Page.startScreencast with their capture timestamps, so the clip keeps real
// time. With ffmpeg on PATH they become an H.264 MP4 (yuv420p, +faststart); without it the frames
// are replayed onto a canvas in a blank page and captured with MediaRecorder as WebM.
import { execFileSync, spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, statSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import puppeteer from "puppeteer-core";

const here = dirname(fileURLToPath(import.meta.url));
const args = process.argv.slice(2);
const opt = (k, d) => { const i = args.indexOf(`--${k}`); return i >= 0 ? args[i + 1] : d; };
const flag = (k) => args.includes(`--${k}`);
const BASE = opt("base", "http://127.0.0.1:8793").replace(/\/$/, "");
const OUT = resolve(opt("out", join(here, "..", "deploy", "vps", "site")));
const NAME = opt("name", "theatre-demo");
const W = Number(opt("width", 1280)), H = Number(opt("height", 800));
const REPLAY = flag("replay");
const ACTION = opt("action", null);
const MAX_S = Number(opt("max-seconds", 60));
const MAX_BYTES = 8 * 1024 * 1024;
const CHROME = opt("chrome", process.env.CHROME_PATH || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const log = (...a) => console.log("[record]", ...a);

const who = await (await fetch(`${BASE}/api/whoami`)).json();
if (!REPLAY && !who.demo) {
  console.error("refusing: this backend is not the demo backend (whoami.demo is false). Use --replay --action <id> for a real run.");
  process.exit(2);
}
if (REPLAY && !ACTION) {
  console.error("--replay needs --action <id> (a completed demo-fixture action)");
  process.exit(2);
}

const browser = await puppeteer.launch({ executablePath: CHROME, headless: true, args: [`--window-size=${W},${H}`] });
const page = await browser.newPage();
await page.setViewport({ width: W, height: H, deviceScaleFactor: 1 });
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));

const url = REPLAY ? `${BASE}/theatre?kiosk=1&replay=1&action=${encodeURIComponent(ACTION)}` : `${BASE}/theatre?kiosk=1`;
await page.goto(url, { waitUntil: "networkidle2", timeout: 60000 });
await page.waitForSelector(".th-line", { timeout: 30000 });
await sleep(800);

// ---- screencast: every frame with its capture time ------------------------------------------
const cdp = await page.createCDPSession();
const frames = [];
const dir = mkdtempSync(join(tmpdir(), "shimpaku-rec-"));
cdp.on("Page.screencastFrame", async (f) => {
  const file = join(dir, `f${String(frames.length).padStart(5, "0")}.jpg`);
  writeFileSync(file, Buffer.from(f.data, "base64"));
  frames.push({ file, t: f.metadata.timestamp });
  await cdp.send("Page.screencastFrameAck", { sessionId: f.sessionId }).catch(() => {});
});
await cdp.send("Page.startScreencast", { format: "jpeg", quality: 88, maxWidth: W, maxHeight: H, everyNthFrame: 1 });
const t0 = Date.now();
const elapsed = () => (Date.now() - t0) / 1000;

async function click(text) {
  const ok = await page.evaluate((t) => {
    const b = [...document.querySelectorAll(".th-ctl button")].find((x) => x.textContent.includes(t) && !x.disabled);
    if (b) b.click();
    return !!b;
  }, text);
  if (!ok) throw new Error(`no enabled button "${text}" (is the fixture already broken or a run in flight? restart the demo backend)`);
  log(`${elapsed().toFixed(1)} s  clicked ${text}`);
}
async function until(fn, ms, what) {
  const end = Date.now() + ms;
  while (Date.now() < end) { if (await page.evaluate(fn)) return; await sleep(200); }
  throw new Error(`timed out waiting for ${what}`);
}
const finalShown = () => !!document.querySelector(".th-final");

let poster = null;
if (REPLAY) {
  await until(finalShown, MAX_S * 1000, "the replay to end");
} else {
  await sleep(2200);
  await click("Break VLAN 999");
  await until(() => document.querySelector(".th-mode")?.textContent.includes("fault"), 15000, "the fault to show");
  await sleep(2500);
  await click("Propose via Shim");
  await until(() => [...document.querySelectorAll(".th-ctl button")].some((b) => b.textContent.includes("Approve")), 20000, "the proposal");
  await sleep(2200);
  await click("Approve…");
  await sleep(900);
  await click("Approve and commit");
  await until(finalShown, (MAX_S - elapsed() - 2) * 1000, "Verified");
}
await sleep(2500);
poster = join(OUT, `${NAME}.png`);
await page.screenshot({ path: poster });
await cdp.send("Page.stopScreencast");
await sleep(300);
log(`captured ${frames.length} frames over ${elapsed().toFixed(1)} s; page errors: ${errors.length}`);
if (errors.length) console.error(errors);

// ---- encode ------------------------------------------------------------------------------------
const hasFfmpeg = spawnSync("ffmpeg", ["-version"]).status === 0 && !flag("webm");
let out;
if (hasFfmpeg) {
  // concat demuxer: each frame lasts until the next one was captured (real time)
  const list = frames.map((f, i) => `file '${f.file}'\nduration ${(((frames[i + 1]?.t ?? f.t + 0.04) - f.t)).toFixed(4)}`).join("\n")
    + `\nfile '${frames[frames.length - 1].file}'\n`;
  writeFileSync(join(dir, "list.txt"), list);
  out = join(OUT, `${NAME}.mp4`);
  for (const crf of [30, 34, 38]) {
    execFileSync("ffmpeg", ["-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", join(dir, "list.txt"),
      "-vf", `fps=24,scale=${W}:-2:flags=lanczos,format=yuv420p`, "-c:v", "libx264", "-preset", "slow", "-crf", String(crf),
      "-tune", "animation", "-movflags", "+faststart", "-an", out]);
    if (statSync(out).size <= MAX_BYTES) break;
    log(`crf ${crf} gave ${(statSync(out).size / 1e6).toFixed(1)} MB; retrying smaller`);
  }
} else {
  // no ffmpeg: replay the frames onto a canvas in a blank page and record it with MediaRecorder
  const p2 = await browser.newPage();
  await p2.goto("about:blank");
  const data = frames.map((f, i) => ({ src: `data:image/jpeg;base64,${readFileSync(f.file).toString("base64")}`,
    ms: Math.max(1, ((frames[i + 1]?.t ?? f.t + 0.04) - f.t) * 1000) }));
  const b64 = await p2.evaluate(async (data, W, H) => {
    const c = document.createElement("canvas"); c.width = W; c.height = H; document.body.append(c);
    const g = c.getContext("2d");
    const imgs = await Promise.all(data.map((d) => new Promise((r) => { const im = new Image(); im.onload = () => r(im); im.src = d.src; })));
    const rec = new MediaRecorder(c.captureStream(30), { mimeType: "video/webm;codecs=vp9", videoBitsPerSecond: 900_000 });
    const chunks = []; rec.ondataavailable = (e) => chunks.push(e.data);
    const done = new Promise((r) => (rec.onstop = r));
    rec.start(250);
    for (let i = 0; i < imgs.length; i++) { g.drawImage(imgs[i], 0, 0, W, H); await new Promise((r) => setTimeout(r, data[i].ms)); }
    rec.stop(); await done;
    const buf = new Uint8Array(await new Blob(chunks, { type: "video/webm" }).arrayBuffer());
    let s = ""; for (let i = 0; i < buf.length; i += 0x8000) s += String.fromCharCode(...buf.subarray(i, i + 0x8000));
    return btoa(s);
  }, data, W, H);
  out = join(OUT, `${NAME}.webm`);
  writeFileSync(out, Buffer.from(b64, "base64"));
}
await browser.close();
rmSync(dir, { recursive: true, force: true });
const size = statSync(out).size;
log(`wrote ${out} (${(size / 1e6).toFixed(2)} MB) and ${poster}`);
if (size > MAX_BYTES) { console.error(`clip is over 8 MB (${size} bytes)`); process.exit(1); }
