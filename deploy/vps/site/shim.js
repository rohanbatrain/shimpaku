// "Ask Shim" on the landing page: Shim's public guide (POST /api/assistant/public on the app host,
// CORS allow-list, no credentials, text/plain body so the browser sends no preflight). Guide mode
// only explains Shimpaku from its documents and points visitors to the live dashboard; it never
// looks at the lab. Self-contained: no library, styles scoped to .shimw-*, and the look follows the
// dashboard's own chat window (round launcher, white header, answer cards, one-line guide note).
// Text from the model is escaped before any formatting is applied; only our own markup is added.
(function () {
  var live = document.getElementById("live");
  var src = live && live.getAttribute("data-src");
  if (!src || !window.fetch || !window.TextDecoder) return;
  var APP = new URL(src, location.href).origin;
  var DASH = APP; // the dashboard is the app host's root (data-lab points at a page inside it)
  var KEY = "shimw.v1";
  var ICON_CHAT = '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M4 5.5A2.5 2.5 0 0 1 6.5 3h11A2.5 2.5 0 0 1 20 5.5v8a2.5 2.5 0 0 1-2.5 2.5H10l-4.5 4v-4h0A1.5 1.5 0 0 1 4 14.5z" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/><path d="M8 8.5h8M8 12h5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>';
  var ICON_BOT = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true"><rect x="4" y="7" width="16" height="12" rx="3" stroke="currentColor" stroke-width="2"/><path d="M12 3v4M9 12h.01M15 12h.01M9 16h6" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>';
  var ICON_SEND = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M4 12 20 4l-6 16-2.5-6.5z" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/></svg>';
  var ICON_X = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/></svg>';

  var css = "" +
    ".shimw-btn{position:fixed;right:24px;bottom:24px;z-index:9999;width:56px;height:56px;border:0;border-radius:50%;background:#01A982;color:#04261B;display:grid;place-items:center;cursor:pointer;box-shadow:0 4px 14px rgba(4,38,27,.28);transition:transform .15s,box-shadow .15s}" +
    ".shimw-btn:hover{transform:translateY(-2px);box-shadow:0 6px 18px rgba(4,38,27,.34)}.shimw-btn:focus-visible{outline:3px solid #00785C;outline-offset:3px}" +
    ".shimw-tip{position:absolute;right:66px;top:50%;transform:translateY(-50%);white-space:nowrap;background:#10231c;color:#fff;font:500 12px Inter,system-ui,sans-serif;padding:4px 9px;border-radius:5px;opacity:0;pointer-events:none;transition:opacity .12s}" +
    ".shimw-btn:hover .shimw-tip,.shimw-btn:focus-visible .shimw-tip{opacity:1}" +
    ".shimw{margin:0;padding:0;max-width:none;position:fixed;right:24px;bottom:24px;z-index:10000;width:min(400px,calc(100vw - 32px));height:min(600px,calc(100vh - 48px));display:flex;flex-direction:column;background:#fff;color:#14211c;border:1px solid #cfdcd5;border-radius:14px;box-shadow:0 18px 50px rgba(8,26,19,.24);overflow:hidden;font:14px/1.45 Inter,system-ui,sans-serif;text-align:left}" +
    ".shimw *{box-sizing:border-box}.shimw[hidden]{display:none}" +
    ".shimw-h{margin:0;display:flex;align-items:center;gap:8px;padding:10px 10px 10px 14px;border-bottom:1px solid #e1ebe6}" +
    ".shimw-h .shimw-logo{color:#00785C;display:grid}.shimw-h b{font-size:14px;color:#14211c}" +
    ".shimw-tag{font-size:10.5px;font-weight:700;letter-spacing:.05em;text-transform:uppercase;color:#7a5300;background:#fff6e0;border:1px solid #f0d9a4;border-radius:10px;padding:1px 7px}" +
    ".shimw-x{margin-left:auto;display:grid;place-items:center;width:30px;height:30px;border:0;background:none;color:#4f6159;border-radius:6px;cursor:pointer}.shimw-x:hover{background:#eef4f1}.shimw-x:focus-visible{outline:2px solid #00785C}" +
    ".shimw-note{display:flex;flex-wrap:wrap;align-items:center;gap:2px 10px;padding:7px 14px;border-bottom:1px solid #e1ebe6;background:#fbfaf3;color:#5b4a14;font-size:12px}" +
    ".shimw-note a{color:#00785C;font-weight:600;text-decoration:none}.shimw-note a:hover{text-decoration:underline}" +
    ".shimw-b{flex:1;overflow:auto;padding:14px;display:flex;flex-direction:column;gap:14px;min-height:0}" +
    ".shimw-u{align-self:flex-end;max-width:85%;background:#01A982;color:#04261B;font-weight:500;padding:8px 12px;border-radius:12px 12px 2px 12px;white-space:pre-wrap;overflow-wrap:anywhere}" +
    ".shimw-a{background:#fbfdfc;border:1px solid #e1ebe6;border-radius:4px 12px 12px 12px;padding:10px 12px;font-size:13.5px;overflow-wrap:anywhere}" +
    ".shimw-meta{display:flex;align-items:center;gap:7px;margin-bottom:6px;font-size:12px;color:#5f716a}" +
    ".shimw-av{display:grid;place-items:center;width:20px;height:20px;border-radius:50%;background:#00785C;color:#fff}" +
    ".shimw-meta b{color:#00785C;font-weight:700}" +
    ".shimw-a p{margin:0 0 6px}.shimw-a p:last-child{margin:0}.shimw-a ul{margin:2px 0 6px;padding-left:18px}.shimw-a li{margin:2px 0}" +
    ".shimw-a code{font:12px ui-monospace,SFMono-Regular,monospace;background:#eef3f0;padding:1px 4px;border-radius:3px}" +
    ".shimw-a a{color:#00785C;font-weight:600}" +
    ".shimw-unlock{display:block;margin-top:8px;padding:7px 10px;border-radius:8px;background:#e9f7f1;border:1px solid #bfe3d3;color:#0b4d38;font-size:12.5px}" +
    ".shimw-busy{display:flex;align-items:center;gap:8px;color:#5f716a;font-size:12.5px}" +
    ".shimw-spin{width:12px;height:12px;border-radius:50%;border:2px solid #bfe3d3;border-top-color:#01A982;animation:shimw-spin .8s linear infinite}" +
    "@keyframes shimw-spin{to{transform:rotate(360deg)}}" +
    ".shimw-err{border-color:#f3c3bd;background:#fdf3f2}" +
    ".shimw-hi b{display:block;margin-bottom:4px}.shimw-hi span{color:#5f716a;font-size:13px}" +
    ".shimw-sugg{display:flex;flex-direction:column;gap:6px}" +
    ".shimw-sugg button{text-align:left;border:1px solid #cfdcd5;background:#fff;color:#14211c;border-radius:8px;padding:8px 12px;font:13px Inter,system-ui,sans-serif;cursor:pointer}" +
    ".shimw-sugg button:hover{border-color:#9ccbb8;background:#f4faf7}.shimw-sugg button:focus-visible{outline:2px solid #00785C}" +
    ".shimw-f{display:flex;gap:8px;align-items:flex-end;padding:10px 12px 12px;border-top:1px solid #e1ebe6}" +
    ".shimw-f textarea{flex:1;resize:none;font:inherit;line-height:1.4;padding:8px 10px;border:1px solid #cfdcd5;border-radius:8px;min-height:38px;max-height:120px;color:#14211c;background:#fff}" +
    ".shimw-f textarea:focus{outline:2px solid #01A982;outline-offset:-1px}" +
    ".shimw-send{display:grid;place-items:center;width:40px;height:38px;border:0;border-radius:8px;background:#01A982;color:#04261B;cursor:pointer}.shimw-send:disabled{opacity:.45;cursor:default}" +
    ".shimw-sr{position:absolute;left:-9999px}" +
    "@media (max-width:520px){.shimw{inset:0;width:100vw;height:100dvh;border-radius:0;border:0}.shimw-btn{right:16px;bottom:16px}}" +
    "@media (prefers-reduced-motion:no-preference){.shimw{animation:shimw-in .16s ease-out;transform-origin:bottom right}}" +
    "@keyframes shimw-in{from{opacity:0;transform:translateY(8px) scale(.98)}to{opacity:1;transform:none}}";
  var st = document.createElement("style");
  st.textContent = css;
  document.head.appendChild(st);

  function esc(s) { return s.replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }
  function href(u) {
    if (/^\/[a-z]/i.test(u)) return DASH + u;          // dashboard paths
    if (/^https:\/\//.test(u)) return u;
    return null;
  }
  function link(u, label) { return '<a href="' + esc(DASH + u) + '" target="_blank" rel="noopener">' + label + "</a>"; }
  // Escape first, then add only our own tags: bold, code, links, lists, paragraphs.
  function md(text) {
    var out = [], list = null;
    esc(text).split(/\n/).forEach(function (raw) {
      var l = raw.trim();
      var inl = l
        .replace(/`(\/(?:request|login|virga|topology|hardware|theatre|architecture|sle)\b[^`]*)`/g, "$1") // a path in backticks is a link, not code
        .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>").replace(/`([^`]+)`/g, "<code>$1</code>")
        .replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, function (m, t, u) { var h = href(u.replace(/&amp;/g, "&")); return h ? '<a href="' + esc(h) + '" target="_blank" rel="noopener">' + t + "</a>" : t; })
        .replace(/(^|[\s(])(\/(?:request|login|virga|topology|hardware|theatre|architecture|sle)\b)/g, function (m, p, u) { return p + link(u, u); });
      var b = l.match(/^[-*•]\s+/);
      if (b) { if (!list) { list = []; } list.push("<li>" + inl.replace(/^[-*•]\s+/, "") + "</li>"); return; }
      if (list) { out.push("<ul>" + list.join("") + "</ul>"); list = null; }
      if (!l) return;
      out.push(l.indexOf("🔓") === 0 ? '<span class="shimw-unlock">' + inl + "</span>" : "<p>" + inl + "</p>");
    });
    if (list) out.push("<ul>" + list.join("") + "</ul>");
    return out.join("");
  }
  function hhmm(t) { return new Date(t).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false }); }

  var hist = [];
  try { hist = JSON.parse(sessionStorage.getItem(KEY) || "[]"); } catch (e) { hist = []; }
  var busy = false;

  var btn = document.createElement("button");
  btn.className = "shimw-btn"; btn.type = "button"; btn.setAttribute("aria-haspopup", "dialog"); btn.setAttribute("aria-label", "Ask Shim");
  btn.innerHTML = ICON_CHAT + '<span class="shimw-tip" aria-hidden="true">Ask Shim</span>';
  var box = document.createElement("div");
  box.className = "shimw"; box.hidden = true; box.setAttribute("role", "dialog"); box.setAttribute("aria-label", "Ask Shim, the guide");
  box.innerHTML =
    '<div class="shimw-h"><span class="shimw-logo">' + ICON_BOT + '</span><b>Ask Shim</b><span class="shimw-tag">guide</span>' +
      '<button type="button" class="shimw-x" aria-label="Close (Esc)">' + ICON_X + "</button></div>" +
    '<div class="shimw-note"><span>🔓 The live lab needs a session</span>' + link("/request", "Request one") + link("/", "Dashboard") + "</div>" +
    '<div class="shimw-b" aria-live="polite"></div>' +
    '<form class="shimw-f"><label class="shimw-sr" for="shimw-q">Ask Shim</label><textarea id="shimw-q" rows="1" maxlength="600" placeholder="Say hi, or ask how Shimpaku works…"></textarea>' +
      '<button type="submit" class="shimw-send" aria-label="Send">' + ICON_SEND + "</button></form>";
  document.body.appendChild(btn);
  document.body.appendChild(box);
  var body = box.querySelector(".shimw-b"), form = box.querySelector("form"), ta = box.querySelector("textarea"), send = form.querySelector("button");

  function user(q) { var d = document.createElement("div"); d.className = "shimw-u"; d.textContent = q; body.appendChild(d); }
  function answer(html, at) {
    var d = document.createElement("div"); d.className = "shimw-a";
    d.innerHTML = '<div class="shimw-meta"><span class="shimw-av">' + ICON_BOT + '</span><b>Shim</b><span>' + hhmm(at || Date.now()) + '</span></div><div class="shimw-t">' + html + "</div>";
    body.appendChild(d); body.scrollTop = body.scrollHeight;
    return d.querySelector(".shimw-t");
  }
  function welcome() {
    var w = document.createElement("div"); w.className = "shimw-hi";
    w.innerHTML = "<b>Hi! I'm Shim.</b><span>I explain what Shimpaku is and how it works, from its own documents. I can't look at the live lab from here.</span>";
    body.appendChild(w);
    var s = document.createElement("div"); s.className = "shimw-sugg";
    ["What is Shimpaku?", "How can an AI fix a network safely?", "What is Virga?", "How do I try it myself?"].forEach(function (q) {
      var b = document.createElement("button"); b.type = "button"; b.textContent = q;
      b.onclick = function () { w.remove(); s.remove(); ask(q); }; s.appendChild(b);
    });
    body.appendChild(s);
  }
  hist.forEach(function (t) { user(t.q); answer(md(t.a), t.at); });
  if (!hist.length) welcome();
  function grow() { ta.style.height = "auto"; ta.style.height = Math.min(120, ta.scrollHeight) + "px"; }

  function ask(q) {
    q = (q || "").trim();
    if (!q || busy) return;
    var hi = body.querySelector(".shimw-hi"), sg = body.querySelector(".shimw-sugg");
    if (hi) hi.remove(); if (sg) sg.remove();
    busy = true; send.disabled = true; ta.value = ""; grow();
    user(q);
    var a = answer('<span class="shimw-busy"><span class="shimw-spin"></span>Reading the docs…</span>');
    var text = "", t0 = Date.now();
    var tick = setInterval(function () { if (!text) a.innerHTML = '<span class="shimw-busy"><span class="shimw-spin"></span>Reading the docs… ' + Math.round((Date.now() - t0) / 1000) + " s</span>"; }, 1000);
    function done(err) {
      clearInterval(tick); busy = false; send.disabled = false;
      if (err) { a.parentNode.classList.add("shimw-err"); a.innerHTML = md(err + "\n🔓 The full assistant is on the live dashboard."); return; }
      if (!text) { a.innerHTML = md("Shim finished without an answer. Try asking another way."); return; }
      hist = hist.concat([{ q: q.slice(0, 1500), a: text.slice(0, 1500), at: t0 }]).slice(-6);
      try { sessionStorage.setItem(KEY, JSON.stringify(hist)); } catch (e) {}
    }
    fetch(APP + "/api/assistant/public", {
      method: "POST", mode: "cors", credentials: "omit",
      headers: { "Content-Type": "text/plain" },
      body: JSON.stringify({ question: q, history: hist.slice(-4).map(function (t) { return { q: t.q, a: t.a }; }) }),
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
  box.querySelector(".shimw-x").onclick = close;
  box.addEventListener("keydown", function (e) { if (e.key === "Escape") close(); });
  form.onsubmit = function (e) { e.preventDefault(); ask(ta.value); };
  ta.addEventListener("input", grow);
  ta.addEventListener("keydown", function (e) { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); ask(ta.value); } });
})();
