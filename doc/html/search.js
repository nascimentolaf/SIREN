// Client-side search over window.DOC_INDEX (search_index.js). Plain script
// tags rather than fetch(), so it also works on pages opened from disk.
(function () {
  var input = document.getElementById("search"), list = document.getElementById("results");
  var root = document.body.getAttribute("data-root") || "", sel = -1;
  if (!input || !window.DOC_INDEX) return;
  function esc(s) { return s.replace(/[&<>"]/g, function (c) {
    return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]; }); }
  function count(hay, needle) { var n = 0, i = hay.indexOf(needle);
    while (i !== -1 && n < 50) { n++; i = hay.indexOf(needle, i + needle.length); } return n; }
  function search(q) {
    var terms = q.toLowerCase().split(/\s+/).filter(Boolean), out = [];
    if (!terms.length) return out;
    window.DOC_INDEX.forEach(function (p) {
      var title = p.t.toLowerCase(), text = p.x.toLowerCase(), score = 0, best = null, ok = true;
      terms.forEach(function (t) {
        var s = (title.indexOf(t) !== -1 ? 20 : 0) + Math.min(count(text, t), 10);
        p.h.forEach(function (h) { if (h[0].toLowerCase().indexOf(t) !== -1) { s += 8; if (!best) best = h; } });
        if (!s) ok = false; score += s; });
      if (ok) out.push({p: p, s: score, h: best});
    });
    return out.sort(function (a, b) { return b.s - a.s; }).slice(0, 12);
  }
  function render() {
    var hits = search(input.value); sel = -1;
    if (!input.value.trim()) { list.hidden = true; return; }
    list.innerHTML = hits.length ? hits.map(function (r) {
      var url = root + r.p.u + (r.h ? "#" + r.h[1] : "");
      return '<li><a href="' + url + '">' + esc(r.p.t) + (r.h ? "<small>" + esc(r.h[0]) + "</small>" : "") + "</a></li>";
    }).join("") : '<li><a>No match</a></li>';
    list.hidden = false;
  }
  function move(d) { var a = list.querySelectorAll("a[href]"); if (!a.length) return;
    if (sel >= 0) a[sel].classList.remove("sel"); sel = (sel + d + a.length) % a.length; a[sel].classList.add("sel"); }
  input.addEventListener("input", render);
  input.addEventListener("keydown", function (e) {
    if (e.key === "ArrowDown") { move(1); e.preventDefault(); }
    else if (e.key === "ArrowUp") { move(-1); e.preventDefault(); }
    else if (e.key === "Enter") { var a = list.querySelectorAll("a[href]"); if (a.length) location.href = a[Math.max(sel, 0)].href; }
    else if (e.key === "Escape") { list.hidden = true; input.blur(); } });
  document.addEventListener("click", function (e) { if (!e.target.closest(".search")) list.hidden = true; });
  document.addEventListener("keydown", function (e) {
    if (e.key === "/" && document.activeElement !== input) { input.focus(); e.preventDefault(); } });
})();
