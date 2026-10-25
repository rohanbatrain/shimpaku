// "Ask Shim" on the landing page: Shim's public guide (POST /api/assistant/public on the app host,
// CORS allow-list, no credentials, text/plain body so the browser sends no preflight). Guide mode
// only explains Shimpaku from its documents and points visitors to the live dashboard; it never
// looks at the lab. Everything here is self-contained: no library, styles scoped to .shimw-*.
// Text from the model is escaped before any formatting is applied; only our own markup is added.
(function () {
  var live = document.getElementById("live");
  var src = live && live.getAttribute("data-src");
  if (!src || !window.fetch || !window.TextDecoder) return;
  var APP = new URL(src, location.href).origin;
  var DASH = APP; // the dashboard is the app host's root (data-lab points at a page inside it)
  var KEY = "shimw.v1";

  var css = "" +
    ".shimw-btn{position:fixed;right:22px;bottom:22px;z-index:9999;display:flex;align-items:center;gap:8px;padding:12px 18px;border:0;border-radius:28px;background:#01A982;color:#04261B;font:700 15px/1 Inter,system-ui,sans-serif;cursor:pointer;box-shadow:0 6px 20px rgba(4,38,27,.28)}" +
    ".shimw-btn:hover{transform:translateY(-1px)}.shimw-btn:focus-visible{outline:3px solid #00785C;outline-offset:3px}" +
    ".shimw-dot{width:8px;height:8px;border-radius:50%;background:#04261B;opacity:.6}" +
    ".shimw{position:fixed;right:22px;bottom:22px;z-index:10000;width:min(390px,calc(100vw - 24px));height:min(580px,calc(100vh - 40px));display:flex;flex-direction:column;background:#fff;color:#14211c;border:1px solid #cfdcd5;border-radius:16px;box-shadow:0 20px 60px rgba(8,26,19,.28);overflow:hidden;font:14px/1.5 Inter,system-ui,sans-serif}" +
    ".shimw[hidden]{display:none}" +
    ".shimw-h{display:flex;align-items:center;gap:10px;padding:12px 14px;background:#0f241c;color:#e7f1ec}" +
    ".shimw-h b{font-size:15px}.shimw-h small{display:block;color:#9fb3aa;font-size:11.5px}" +
    ".shimw-h button{margin-left:auto;background:none;border:0;color:#c9d8d1;font-size:22px;line-height:1;cursor:pointer;padding:2px 6px;border-radius:6px}" +
    ".shimw-h button:focus-visible{outline:2px solid #01A982}" +
    ".shimw-mode{margin:10px 12px 0;padding:8px 10px;border-radius:10px;background:#fff6e0;border:1px solid #f0d9a4;color:#5b4100;font-size:12.5px}" +
    ".shimw-b{flex:1;overflow:auto;padding:12px;display:flex;flex-direction:column;gap:10px}" +
    ".shimw-u{align-self:flex-end;max-width:85%;background:#01A982;color:#04261B;font-weight:500;padding:8px 12px;border-radius:12px 12px 2px 12px;white-space:pre-wrap}" +
    ".shimw-a{max-width:94%;background:#f4f8f6;border:1px solid #e1ebe6;padding:9px 12px;border-radius:2px 12px 12px 12px}" +
    ".shimw-a p{margin:0 0 6px}.shimw-a p:last-child{margin:0}.shimw-a ul{margin:4px 0 6px;padding-left:18px}.shimw-a code{font:12px ui-monospace,monospace;background:#e6efea;padding:1px 4px;border-radius:3px}" +
    ".shimw-a a{color:#00785C;font-weight:600}.shimw-unlock{display:block;margin-top:6px;padding:6px 8px;border-radius:8px;background:#e9f7f1;color:#0b4d38;font-size:13px}" +
    ".shimw-busy{color:#5f716a;font-size:12.5px}.shimw-err{color:#8a1c14;background:#fdecea;border-color:#f3c3bd}" +
    ".shimw-sugg{display:flex;flex-wrap:wrap;gap:6px}.shimw-sugg button{border:1px solid #bfe3d3;background:#f2faf6;color:#00785C;border-radius:14px;padding:5px 11px;font:13px Inter,system-ui,sans-serif;cursor:pointer}" +
    ".shimw-cta{display:flex;gap:8px;padding:8px 12px;border-top:1px solid #e1ebe6;background:#fafcfb}" +
    ".shimw-cta a{flex:1;text-align:center;padding:7px 8px;border-radius:8px;font:600 12.5px Inter,system-ui,sans-serif;text-decoration:none}" +
    ".shimw-cta .p{background:#01A982;color:#04261B}.shimw-cta .s{border:1px solid #cfdcd5;color:#14211c}" +
    ".shimw-f{display:flex;gap:8px;padding:10px 12px 12px;border-top:1px solid #e1ebe6}" +
    ".shimw-f textarea{flex:1;resize:none;font:inherit;padding:8px 10px;border:1px solid #cfdcd5;border-radius:10px;min-height:38px;max-height:110px}" +
    ".shimw-f button{border:0;border-radius:10px;background:#01A982;color:#04261B;font-weight:700;padding:0 14px;cursor:pointer}.shimw-f button:disabled{opacity:.5;cursor:default}" +
    "@media (max-width:520px){.shimw{right:0;bottom:0;width:100vw;height:100dvh;border-radius:0}.shimw-btn{right:14px;bottom:14px}}" +
    "@media (prefers-reduced-motion:no-preference){.shimw{animation:shimw-in .16s ease-out}}@keyframes shimw-in{from{opacity:0;transform:translateY(8px)}}";
  var st = document.createElement("style");
  st.textContent = css;
  document.head.appendChild(st);

  function esc(s) { return s.replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }
  function href(u) {
    if (/^\/[a-z]/i.test(u)) return DASH + u;          // dashboard paths
    if (/^https:\/\//.test(u)) return u;
    return null;
  }
  // Escape first, then add only our own tags: bold, code, links, lists, paragraphs.
  function md(text) {
    var out = [], list = null;
    esc(text).split(/\n/).forEach(function (raw) {
      var l = raw.trim();
      var inl = l.replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>").replace(/`([^`]+)`/g, "<code>$1</code>")
        .replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, function (m, t, u) { var h = href(u.replace(/&amp;/g, "&")); return h ? '<a href="' + esc(h) + '" target="_blank" rel="noopener">' + t + "</a>" : t; })
        .replace(/(^|[\s(])(\/(?:request|login|virga|topology|hardware|theatre|architecture|sle)\b)/g, function (m, p, u) { return p + '<a href="' + esc(DASH + u) + '" target="_blank" rel="noopener">' + u + "</a>"; });
      var b = l.match(/^[-*•]\s+/);
      if (b) { if (!list) { list = []; } list.push("<li>" + inl.replace(/^[-*•]\s+/, "") + "</li>"); return; }
      if (list) { out.push("<ul>" + list.join("") + "</ul>"); list = null; }
      if (!l) return;
      out.push(l.indexOf("🔓") === 0 ? '<span class="shimw-unlock">' + inl + "</span>" : "<p>" + inl + "</p>");
    });
    if (list) out.push("<ul>" + list.join("") + "</ul>");
    return out.join("");
  }

  var hist = [];
  try { hist = JSON.parse(sessionStorage.getItem(KEY) || "[]"); } catch (e) { hist = []; }
  var busy = false;

  var btn = document.createElement("button");
  btn.className = "shimw-btn"; btn.type = "button"; btn.setAttribute("aria-haspopup", "dialog");
  btn.innerHTML = '<span class="shimw-dot" aria-hidden="true"></span>Ask Shim';
  var box = document.createElement("section");
  box.className = "shimw"; box.hidden = true; box.setAttribute("role", "dialog"); box.setAttribute("aria-label", "Ask Shim, the guide");
  box.innerHTML =
    '<div class="shimw-h"><div><b>Shim</b><small>guide · explains Shimpaku, from its own docs</small></div><button type="button" aria-label="Close">×</button></div>' +
    '<div class="shimw-mode">Guide mode: Shim can explain how Shimpaku works and help you find your way. 🔓 To let it investigate the <b>live lab</b> and propose fixes, use the dashboard with a live session.</div>' +
    '<div class="shimw-b" aria-live="polite"></div>' +
    '<div class="shimw-cta"><a class="p" target="_blank" rel="noopener" href="' + esc(DASH + "/") + '">Open the live dashboard</a><a class="s" target="_blank" rel="noopener" href="' + esc(DASH + "/request") + '">Request a live session</a></div>' +
    '<form class="shimw-f"><label class="shimw-sr" style="position:absolute;left:-9999px" for="shimw-q">Ask Shim</label><textarea id="shimw-q" rows="1" maxlength="600" placeholder="Say hi, or ask how Shimpaku works…"></textarea><button type="submit">Send</button></form>';
  document.body.appendChild(btn);
  document.body.appendChild(box);
  var body = box.querySelector(".shimw-b"), form = box.querySelector("form"), ta = box.querySelector("textarea"), send = form.querySelector("button");

  function bubble(cls, html) { var d = document.createElement("div"); d.className = cls; d.innerHTML = html; body.appendChild(d); body.scrollTop = body.scrollHeight; return d; }
  function welcome() {
    bubble("shimw-a", md("Hi! I'm **Shim**, the assistant built into Shimpaku. Here I can explain what it is and how it works: the seven real Junos devices, the guardrails, the audit chain, the Virga fault drills.\n🔓 For questions about the live lab, open the dashboard and request a live session."));
    var s = document.createElement("div"); s.className = "shimw-sugg";
    ["Hi! What is Shimpaku?", "How can an AI fix a network safely?", "What is Virga?", "How do I try it myself?"].forEach(function (q) {
      var b = document.createElement("button"); b.type = "button"; b.textContent = q; b.onclick = function () { ask(q); }; s.appendChild(b);
    });
    body.appendChild(s);
  }
  hist.forEach(function (t) { bubble("shimw-u", esc(t.q)); bubble("shimw-a", md(t.a)); });
  if (!hist.length) welcome();

  function ask(q) {
    q = (q || "").trim();
    if (!q || busy) return;
    busy = true; send.disabled = true; ta.value = "";
    bubble("shimw-u", esc(q));
    var a = bubble("shimw-a", '<span class="shimw-busy">Shim is reading the docs…</span>');
    var text = "", t0 = Date.now();
    var tick = setInterval(function () { if (!text) a.innerHTML = '<span class="shimw-busy">Shim is reading the docs… ' + Math.round((Date.now() - t0) / 1000) + " s</span>"; }, 1000);
    function done(err) {
      clearInterval(tick); busy = false; send.disabled = false;
      if (err) { a.className = "shimw-a shimw-err"; a.innerHTML = md(err + "\n🔓 The full assistant is on the live dashboard."); return; }
      if (!text) { a.innerHTML = md("Shim finished without an answer. Try asking another way."); return; }
      hist = hist.concat([{ q: q.slice(0, 1500), a: text.slice(0, 1500) }]).slice(-6);
      try { sessionStorage.setItem(KEY, JSON.stringify(hist)); } catch (e) {}
    }
    fetch(APP + "/api/assistant/public", {
      method: "POST", mode: "cors", credentials: "omit",
      headers: { "Content-Type": "text/plain" },
      body: JSON.stringify({ question: q, history: hist.slice(-4) }),
    }).then(function (r) {
      if (!r.ok) return r.json().then(function (j) { done(j.detail || "Shim is not available right now."); }, function () { done(r.status === 503 ? "The live lab is offline right now, so Shim's guide is resting." : "Shim is not available right now."); });
      var rd = r.body.getReader(), dec = new TextDecoder(), buf = "";
      function pump() {
        return rd.read().then(function (x) {
          if (x.done) return done();
          buf += dec.decode(x.value, { stream: true }).replace(/\r\n/g, "\n");
          var i;
          while ((i = buf.indexOf("\n\n")) >= 0) {
            var chunk = buf.slice(0, i); buf = buf.slice(i + 2);
            var data = chunk.split("\n").filter(function (l) { return l.indexOf("data:") === 0; }).map(function (l) { return l.slice(5).trim(); }).join("");
            if (!data) continue;
            try {
              var ev = JSON.parse(data);
              if (ev.type === "token") { text += ev.text; a.innerHTML = md(text); body.scrollTop = body.scrollHeight; }
              else if (ev.type === "tool" && !text) a.innerHTML = '<span class="shimw-busy">Shim is reading the docs…</span>';
              else if (ev.type === "error") { return done("Shim could not answer: " + ev.message); }
            } catch (e) {}
          }
          return pump();
        });
      }
      return pump();
    }).catch(function () { done("Shim could not be reached. The live lab may be offline."); });
  }

  function open() { box.hidden = false; btn.hidden = true; ta.focus(); body.scrollTop = body.scrollHeight; }
  function close() { box.hidden = true; btn.hidden = false; btn.focus(); }
  btn.onclick = open;
  box.querySelector(".shimw-h button").onclick = close;
  box.addEventListener("keydown", function (e) { if (e.key === "Escape") close(); });
  form.onsubmit = function (e) { e.preventDefault(); ask(ta.value); };
  ta.addEventListener("keydown", function (e) { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); ask(ta.value); } });
})();
