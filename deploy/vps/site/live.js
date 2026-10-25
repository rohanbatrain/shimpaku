// Live status strip on the landing page. Reads GET /api/public/summary on the app host (CORS
// allow-list, no credentials). If anything fails, the static fallback text in the page stays.
(function () {
  var box = document.getElementById("live");
  if (!box || !window.fetch) return;
  var src = box.getAttribute("data-src");
  function ago(t) {
    var s = Date.now() / 1000 - t;
    if (s < 60) return "just now";
    if (s < 3600) return Math.round(s / 60) + " min ago";
    if (s < 86400) return Math.round(s / 3600) + " h ago";
    return Math.round(s / 86400) + " d ago";
  }
  function item(label, value, tone, title) {
    var li = document.createElement("li");
    if (tone) li.className = tone;
    if (title) li.title = title;
    var b = document.createElement("b");
    b.textContent = value;
    var span = document.createElement("span");
    span.textContent = label;
    li.appendChild(b);
    li.appendChild(span);
    return li;
  }
  var ctl = window.AbortController ? new AbortController() : null;
  var timer = setTimeout(function () { if (ctl) ctl.abort(); }, 6000);
  fetch(src, { credentials: "omit", mode: "cors", headers: { Accept: "application/json" }, signal: ctl ? ctl.signal : undefined })
    .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
    .then(function (d) {
      clearTimeout(timer);
      var ul = document.createElement("ul");
      var lab = d.lab || {};
      ul.appendChild(lab.live
        ? item("lab", "online", "ok", "The dashboard is reading the live lab")
        : item("lab", "offline", "warn", "The dashboard is serving a recorded replay" +
            (lab.replay_taken_at ? " from " + new Date(lab.replay_taken_at * 1000).toISOString().slice(0, 16).replace("T", " ") + " UTC" : "")));
      var dv = d.devices || {};
      ul.appendChild(item("devices up", dv.up == null ? "— / " + (dv.total == null ? "—" : dv.total) : dv.up + " / " + dv.total,
        dv.up != null && dv.up < dv.total ? "warn" : "", dv.basis || ""));
      var la = d.last_action;
      ul.appendChild(item("last action", la && la.at ? ago(la.at) + " · " + String(la.status || "").replace(/_/g, " ") : "none", "",
        la && la.kind ? String(la.kind).replace(/_/g, " ") : ""));
      var ac = d.actions || {};
      ul.appendChild(item("unsafe actions executed", ac.unsafe_executed == null ? "—" : String(ac.unsafe_executed),
        ac.unsafe_executed === 0 ? "ok" : ac.unsafe_executed ? "bad" : "", ac.unsafe_definition ? "Counts " + ac.unsafe_definition +
        (ac.total != null ? " (" + ac.total + " actions, " + ac.blocked + " blocked by guardrails)" : "") : ""));
      var au = d.audit;
      ul.appendChild(item("audit chain", au ? (au.verified ? "verified" : "BROKEN") : "—", au ? (au.verified ? "ok" : "bad") : "",
        au && au.rows != null ? au.rows + " rows hash-chained" : ""));
      box.replaceChildren(ul);
      var note = document.createElement("p");
      note.className = "live-note";
      note.textContent = (lab.live ? "Live" : "Replay") + " · unsafe = an action that committed to a device without the guardrail policy allowing it. Hover a value for its basis.";
      box.appendChild(note);
      box.setAttribute("data-state", "live");
    })
    .catch(function () { clearTimeout(timer); box.setAttribute("data-state", "fallback"); });
})();
