// Live status strip on the landing page. Reads GET /api/public/summary on the app host (CORS
// allow-list, no credentials). If anything fails, the static fallback text in the page stays.
// While the operator runs a Virga fault drill (summary.drill.active, docs/build/virga-live.md §2) a
// banner above the strip says so, with links to the dashboard's Fault lab and the status page.
// The strip refreshes every 30 s while the tab is visible, so a visitor can watch a drill start and end.
// Shim's public guide widget (shim.js) rides along with the live strip: one script tag on the page.
(function () { var s = document.createElement("script"); s.src = "shim.js?v=3"; s.defer = true; document.head.appendChild(s); })();
(function () {
  var box = document.getElementById("live");
  if (!box || !window.fetch) return;
  var src = box.getAttribute("data-src");
  var labUrl = box.getAttribute("data-lab"), statusUrl = box.getAttribute("data-status");
  var shown = false;
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
  function link(href, text) {
    var a = document.createElement("a");
    a.href = href;
    a.textContent = text;
    return a;
  }
  // "Fault drill running: FAB-01 — hold". Every value is set as text, never as HTML.
  function drillBanner(dr) {
    var p = document.createElement("p");
    p.className = "drill";
    p.setAttribute("role", "status");
    var b = document.createElement("b");
    b.textContent = "Fault drill running: " + String(dr.scenario || "a catalogued fault") +
      (dr.phase ? " \u2014 " + String(dr.phase).replace(/_/g, " ") : "");
    if (dr.title) b.title = String(dr.title);
    p.appendChild(b);
    var rest = document.createElement("span");
    rest.textContent = ". The operator is injecting a real fault into the lab with Virga, which then reverts it and verifies the revert. Watch it on ";
    p.appendChild(rest);
    if (labUrl) p.appendChild(link(labUrl, "the Fault lab"));
    if (labUrl && statusUrl) p.appendChild(document.createTextNode(" and "));
    if (statusUrl) p.appendChild(link(statusUrl, "the status page"));
    p.appendChild(document.createTextNode("."));
    return p;
  }
  function load() {
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
        var dr = d.drill;
        if (dr && dr.active === true) box.replaceChildren(drillBanner(dr), ul);
        else box.replaceChildren(ul);
        box.setAttribute("data-drill", dr && dr.active === true ? "active" : "idle");
        var note = document.createElement("p");
        note.className = "live-note";
        note.textContent = (lab.live ? "Live" : "Replay") + " · unsafe = an action that committed to a device without the guardrail policy allowing it. Hover a value for its basis.";
        box.appendChild(note);
        box.setAttribute("data-state", "live");
        shown = true;
      })
      // a failed refresh keeps what the visitor already sees; only the first failure shows the fallback text
      .catch(function () { clearTimeout(timer); if (!shown) box.setAttribute("data-state", "fallback"); });
  }
  load();
  setInterval(function () { if (!document.hidden) load(); }, 30000);
})();
