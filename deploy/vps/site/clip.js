// Respect reduced motion: show the poster with controls instead of autoplaying the clip.
(function () {
  var v = document.querySelector(".clip video");
  if (!v || !window.matchMedia) return;
  var m = window.matchMedia("(prefers-reduced-motion: reduce)");
  function apply() {
    if (m.matches) { v.removeAttribute("autoplay"); v.removeAttribute("loop"); v.pause(); v.controls = true; }
  }
  apply();
  if (m.addEventListener) m.addEventListener("change", apply);
})();
