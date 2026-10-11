"use strict";
/* ---------- utilitaires ---------- */
const $ = (id) => document.getElementById(id);
const NS = "http://www.w3.org/2000/svg";
const tr = t; // alias : `t` est parfois masqué par une variable locale (ticker)

function h(tag, props, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v == null || v === false) continue;
    if (k === "class") e.className = v;
    else if (k === "text") e.textContent = t(v);
    else if (k === "style") e.style.cssText = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v === true ? "" : ["aria-label", "title", "placeholder", "alt"].includes(k) ? t(v) : v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid == null || kid === false) continue;
    e.append(kid.nodeType ? kid : document.createTextNode(t(String(kid))));
  }
  return e;
}
function s(tag, attrs, text) {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs || {})) e.setAttribute(k, v);
  if (text != null) e.textContent = text;
  return e;
}
/* Traduit une fois le HTML statique (onglets, boutons, placeholders, aria-label…) quand la langue est l'anglais. */
function translateStatic(root) {
  if (LANG !== "en") return;
  const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const nodes = []; while (w.nextNode()) nodes.push(w.currentNode);
  for (const n of nodes) if (n.nodeValue.trim()) n.nodeValue = t(n.nodeValue);
  for (const el of root.querySelectorAll("[placeholder],[aria-label],[title],[alt]"))
    for (const a of ["placeholder", "aria-label", "title", "alt"]) if (el.hasAttribute(a)) el.setAttribute(a, t(el.getAttribute(a)));
}
translateStatic(document.body);
const safeUrl = (u) => (/^https?:\/\//i.test(u || "") ? u : null);
const link = (url, text) => (safeUrl(url) ? h("a", { href: url, target: "_blank", rel: "noopener noreferrer", text }) : text);
function delta(v) {
  if (v == null) return h("span", { class: "mut", text: "–" });
  return h("span", { class: v >= 0 ? "up" : "down", text: fmtPct(v, 2, true) });
}
/* Logo d'une entreprise : plusieurs sources essayées dans l'ordre (la suivante seulement si la précédente échoue vraiment,
   pas juste si elle est floue), pour avoir un logo net le plus souvent possible plutôt que rien du tout.
   1) Parqet et 2) Financial Modeling Prep : bases de logos d'actions par symbole boursier (net, bien cadré, carré) — fonctionnent même
      sans domaine connu (utile pour les entreprises d'un secteur qu'on ne suit pas encore).
   3) Favicon du site officiel (Google) : dernier recours, seulement si on connaît le domaine — qualité plus inégale.
   Le cadrage (cercle, marge interne) est géré en CSS par .logo ; l'image disparaît si aucune source ne fonctionne. */
function logoImg(ticker, domain, size = 18) {
  if (!ticker) return null;
  const sources = [
    `https://assets.parqet.com/logos/symbol/${encodeURIComponent(ticker)}`,
    `https://images.financialmodelingprep.com/symbol/${encodeURIComponent(ticker)}.png`,
  ];
  if (domain) sources.push(`https://www.google.com/s2/favicons?sz=128&domain=${encodeURIComponent(domain)}`);
  let i = 0;
  const img = h("img", { class: "logo", width: size, height: size, alt: "", loading: "lazy", src: sources[i] });
  img.addEventListener("error", () => { i += 1; if (i < sources.length) img.src = sources[i]; else img.remove(); });
  return img;
}
async function api(path, opts) {
  const r = await fetch(path, opts);
  if (r.status === 401) { location.href = "/login"; throw new Error(T("Non connecté", "Not signed in")); }
  if (!r.ok) throw new Error(path + " → " + r.status);
  return r.json();
}

const LABEL_COLOR = { BUY: "var(--good)", HOLD: "var(--hold)", WATCH: "var(--warn)", "AVOID/REASSESS": "var(--crit)" };
const LEVEL_COLOR = { strong: "var(--good)", medium: "var(--hold)", weak: "var(--axis)", caution: "var(--crit)" };
const KIND_FR = { technique: T("Technique", "Technical"), analystes: T("Analystes", "Analysts"), news: T("Actualité", "News"), investisseurs: T("Investisseurs", "Investors"), calendrier: T("Calendrier", "Calendar") };
const pill = (label, color, text) => h("span", { class: "pill", style: `--c:${color}` }, h("i", { style: `background:${color}` }), text || (label === "AVOID/REASSESS" ? "AVOID" : label));

/* ---------- graphique en lignes (SVG maison, sans dépendance) ---------- */
function niceTicks(min, max, n = 4) {
  const span = max - min || 1, raw = span / n, p = Math.pow(10, Math.floor(Math.log10(raw))), f = raw / p;
  const step = (f < 1.5 ? 1 : f < 3 ? 2 : f < 7 ? 5 : 10) * p, out = [];
  for (let v = Math.ceil(min / step) * step; v <= max + 1e-9; v += step) out.push(v);
  return out;
}

/* Courbe lissée fidèle aux données : interpolation cubique monotone (Fritsch-Carlson). Elle passe exactement par
   chaque vrai cours et ne dépasse jamais un sommet ni un creux entre deux points (aucun pic inventé). at(x) donne la
   hauteur exacte de la courbe dessinée à n'importe quelle abscisse : c'est ce qui permet au point de survol de
   glisser le long de la courbe au lieu de sauter d'un cours à l'autre. pts : [[x, y], …] avec des x croissants. */
function monotone(pts) {
  const n = pts.length, f = (v) => v.toFixed(1);
  if (n === 1) return { d: `M${f(pts[0][0])},${f(pts[0][1])}`, x0: pts[0][0], x1: pts[0][0], at: () => pts[0][1] };
  const dx = [], sl = [], m = new Array(n).fill(0);
  for (let i = 0; i < n - 1; i++) { dx[i] = pts[i + 1][0] - pts[i][0] || 1e-9; sl[i] = (pts[i + 1][1] - pts[i][1]) / dx[i]; }
  m[0] = sl[0]; m[n - 1] = sl[n - 2];
  for (let i = 1; i < n - 1; i++) m[i] = sl[i - 1] * sl[i] <= 0 ? 0 : (sl[i - 1] + sl[i]) / 2;
  for (let i = 0; i < n - 1; i++) {
    if (sl[i] === 0) { m[i] = 0; m[i + 1] = 0; continue; }
    const a = m[i] / sl[i], b = m[i + 1] / sl[i], q = a * a + b * b;
    if (q > 9) { const k = 3 / Math.sqrt(q); m[i] = k * a * sl[i]; m[i + 1] = k * b * sl[i]; }
  }
  let d = `M${f(pts[0][0])},${f(pts[0][1])}`;
  for (let i = 0; i < n - 1; i++) {
    const [xa, ya] = pts[i], [xb, yb] = pts[i + 1], h3 = dx[i] / 3;
    d += ` C${f(xa + h3)},${f(ya + m[i] * h3)} ${f(xb - h3)},${f(yb - m[i + 1] * h3)} ${f(xb)},${f(yb)}`;
  }
  const at = (x) => {
    let lo = 0, hi = n - 2;
    while (lo < hi) { const mid = (lo + hi + 1) >> 1; if (pts[mid][0] <= x) lo = mid; else hi = mid - 1; }
    const i = lo, t = Math.max(0, Math.min(1, (x - pts[i][0]) / dx[i])), t2 = t * t, t3 = t2 * t;
    return (2 * t3 - 3 * t2 + 1) * pts[i][1] + (t3 - 2 * t2 + t) * dx[i] * m[i] + (-2 * t3 + 3 * t2) * pts[i + 1][1] + (t3 - t2) * dx[i] * m[i + 1];
  };
  return { d, x0: pts[0][0], x1: pts[n - 1][0], at };
}

/* series : [{name, slot (1-8), values:[…|null]}] ; dates : ISO. */
function lineChart(host, { dates, series, height = 300, yfmt = null, animate = false, intraday = false, ref = null, bands = null, notes = null }) {
  host.replaceChildren();
  if (!dates.length) { host.append(h("div", { class: "empty", text: "Pas encore de données." })); return; }
  const W = Math.max(host.clientWidth || 640, 300), H = height;
  // Sur mobile, l'étiquette de fin de ligne (redondante avec la légende) est retirée : elle mangeait une grosse
  // part d'une largeur déjà réduite, ce qui rendait le graphique minuscule et illisible.
  const direct = series.length <= 4 && W >= 480;
  const col = (x) => x.color || `var(--s${x.slot})`;
  const m = { l: 50, r: direct ? 78 : 14, t: 10, b: 24 }, iw = W - m.l - m.r, ih = H - m.t - m.b, n = dates.length;
  const all = series.flatMap((x) => x.values).filter((v) => v != null);
  if (ref && ref.value != null) all.push(ref.value); // la ligne de référence (clôture de la veille) reste dans le cadre
  let lo = Math.min(...all), hi = Math.max(...all);
  const pad = (hi - lo || 1) * 0.06; lo -= pad; hi += pad;
  const X = (i) => m.l + (n === 1 ? iw / 2 : (i / (n - 1)) * iw), Y = (v) => m.t + (1 - (v - lo) / (hi - lo)) * ih;

  if (series.length >= 2) {
    host.append(h("div", { class: "legend" }, series.map((x) =>
      h("span", {}, h("span", { class: "k", style: `background:${col(x)}` }), x.name))));
  }
  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", tabindex: "0",
    "aria-label": T("Graphique en lignes : ", "Line chart: ") + series.map((x) => x.name).join(", ") + T(". Flèches gauche et droite pour lire les valeurs.", ". Use the left and right arrow keys to read the values.") });
  const tk = niceTicks(lo, hi), stp = tk.length > 1 ? tk[1] - tk[0] : 1, dec = stp >= 1 ? 0 : stp >= 0.1 ? 1 : 2;
  const axisFmt = yfmt || ((v) => fmtNum(v, dec)), tipFmt = yfmt || ((v) => fmtNum(v));
  for (const v of tk) {
    svg.append(s("line", { x1: m.l, x2: W - m.r, y1: Y(v), y2: Y(v), stroke: "var(--grid)", "stroke-width": 1 }));
    svg.append(s("text", { x: m.l - 8, y: Y(v) + 4, "text-anchor": "end", "font-size": 11, fill: "var(--muted)" }, axisFmt(v)));
  }
  svg.append(s("line", { x1: m.l, x2: W - m.r, y1: m.t + ih, y2: m.t + ih, stroke: "var(--axis)" }));
  const short = n <= 130, ticks = Math.min(W < 480 ? 3 : 5, n);
  // intraday (1J/1S) : axe et infobulle en heure ; sinon en date (année/mois pour une période longue)
  const tickFmt = intraday ? (iso) => new Date(iso).toLocaleTimeString(LOC, { hour: "2-digit", minute: "2-digit" })
    : (iso) => new Date(iso).toLocaleDateString(LOC, short ? { day: "numeric", month: "short" } : { month: "short", year: "2-digit" });
  const tipDateFmt = intraday ? (iso) => new Date(iso).toLocaleString(LOC, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : fmtDate;
  if (intraday && n > 1) {
    // repères à chaque heure pile (1J) ou au début de chaque journée (1S), avec un trait vertical discret ; on en saute
    // si la largeur ne permet pas de tous les écrire lisiblement
    const ts = dates.map((d) => new Date(d)), multiDay = ts[n - 1] - ts[0] > 36 * 3600e3;
    // plusieurs jours : on change de jour selon la date de la bourse (la séance de New York finit après minuit à Paris
    // avec l'après-Bourse ; sinon un « samedi » apparaissait au milieu de la semaine)
    const key = (i) => (multiDay ? dates[i].slice(0, 10) : ts[i].getHours() + "|" + ts[i].toDateString());
    let marks = [];
    for (let i = 1; i < n; i++) if (key(i) !== key(i - 1)) marks.push(i);
    if (!multiDay && ts[0].getMinutes() === 0) marks.unshift(0);
    const room = Math.max(1, Math.floor(iw / (multiDay ? 64 : 46))), every = Math.ceil(marks.length / room);
    marks = marks.filter((_, k) => k % every === 0);
    const lab = multiDay ? (i) => new Date(dates[i].slice(0, 10) + "T12:00:00").toLocaleDateString(LOC, { weekday: "short", day: "numeric" }) : (i) => ts[i].toLocaleTimeString(LOC, { hour: "2-digit", minute: "2-digit" });
    for (const i of marks) {
      const x = X(i), anchor = x - m.l < 18 ? "start" : W - m.r - x < 18 ? "end" : "middle";
      svg.append(s("line", { x1: x, x2: x, y1: m.t, y2: m.t + ih, stroke: "var(--grid)", "stroke-width": 1 }));
      svg.append(s("text", { x, y: H - 6, "text-anchor": anchor, "font-size": 11, fill: "var(--muted)" }, lab(i)));
    }
  } else {
    for (let k = 0; k < ticks; k++) {
      const i = ticks === 1 ? 0 : Math.round((k / (ticks - 1)) * (n - 1));
      svg.append(s("text", { x: X(i), y: H - 6, "text-anchor": k === 0 ? "start" : k === ticks - 1 ? "end" : "middle", "font-size": 11, fill: "var(--muted)" }, tickFmt(dates[i])));
    }
  }
  // zones grisées : échanges hors séance officielle (avant-Bourse, après-Bourse), avec leur nom en haut
  const half = n > 1 ? iw / (n - 1) / 2 : 0;
  for (const b of bands || []) {
    const x0 = Math.max(m.l, X(b.from) - half), x1 = Math.min(W - m.r, X(b.to) + half);
    svg.insertBefore(s("rect", { x: x0, y: m.t, width: Math.max(0, x1 - x0), height: ih, fill: "var(--grid)", opacity: 0.55 }), svg.firstChild);
    if (x1 - x0 > 70) svg.append(s("text", { x: x0 + 6, y: m.t + 13, "font-size": 10.5, fill: "var(--muted)" }, b.label));
  }
  if (ref && ref.value != null) { // ligne pointillée de référence (ex. clôture de la veille), sous la courbe
    const y = Y(ref.value);
    svg.append(s("line", { x1: m.l, x2: W - m.r, y1: y, y2: y, stroke: "var(--muted)", "stroke-width": 1, "stroke-dasharray": "4 4" }));
    svg.append(s("text", { x: m.l + 4, y: y > m.t + 16 ? y - 5 : y + 13, "font-size": 10.5, fill: "var(--muted)" }, `${ref.label} ${tipFmt(ref.value)}`));
  }
  const gs = s("g"), curves = [];
  for (const x of series) {
    // segments continus (une valeur manquante coupe la courbe en plusieurs morceaux)
    const runs = [];
    let run = [];
    x.values.forEach((v, i) => { if (v == null) { if (run.length) runs.push(run); run = []; } else run.push([X(i), Y(v)]); });
    if (run.length) runs.push(run);
    const cs = runs.map(monotone);
    curves.push(cs);
    if (x.area && cs.length === 1) { // zone sous la courbe, en dégradé (séries continues uniquement)
      const base = (m.t + ih).toFixed(1), gid = "ag" + Math.random().toString(36).slice(2, 8);
      const grad = s("linearGradient", { id: gid, x1: "0", y1: "0", x2: "0", y2: "1" });
      grad.append(s("stop", { offset: "0", "stop-color": col(x), "stop-opacity": "0.24" }), s("stop", { offset: "1", "stop-color": col(x), "stop-opacity": "0" }));
      const defs = s("defs"); defs.append(grad); svg.append(defs);
      gs.append(s("path", { d: `${cs[0].d} L${cs[0].x1.toFixed(1)},${base} L${cs[0].x0.toFixed(1)},${base} Z`, fill: `url(#${gid})` }));
    }
    if (cs.length) gs.append(s("path", { d: cs.map((c) => c.d).join(" "), fill: "none", stroke: col(x), "stroke-width": x.width || 2, "stroke-dasharray": x.dash || null, "stroke-linejoin": "round", "stroke-linecap": "round" }));
  }
  svg.append(gs);
  if (animate && !REDUCED) { // la courbe se dévoile de gauche à droite, progressivement
    const id = "rv" + Math.random().toString(36).slice(2, 8), cp = s("clipPath", { id }), rc = s("rect", { x: m.l - 4, y: 0, width: 0, height: H });
    cp.append(rc); svg.append(cp); gs.setAttribute("clip-path", `url(#${id})`);
    const t0 = performance.now(), dur = Math.min(1500, 700 + n * 0.6), full = iw + m.r + 8;
    const step = (now) => { const k = Math.max(0, Math.min(1, (now - t0) / dur)), e = 1 - Math.pow(1 - k, 3); rc.setAttribute("width", full * e); if (k < 1 && rc.isConnected) requestAnimationFrame(step); else gs.removeAttribute("clip-path"); };
    requestAnimationFrame(step);
  }
  if (direct) { // étiquettes directes, décalées pour ne pas se chevaucher
    const ends = series.map((x) => { let i = x.values.length - 1; while (i >= 0 && x.values[i] == null) i--; return i < 0 ? null : { x, y: Y(x.values[i]) }; })
      .filter(Boolean).sort((a, b) => a.y - b.y);
    let prev = -99;
    for (const e of ends) { e.ly = Math.max(e.y, prev + 13); prev = e.ly; }
    for (const e of ends) {
      svg.append(s("circle", { cx: W - m.r + 8, cy: e.ly, r: 3.5, fill: col(e.x) }));
      svg.append(s("text", { x: W - m.r + 15, y: e.ly + 4, "font-size": 11, fill: "var(--ink2)" }, e.x.name.length > 9 ? e.x.name.slice(0, 8) + "…" : e.x.name));
    }
  }
  const cross = s("line", { y1: m.t, y2: m.t + ih, stroke: "var(--ink2)", "stroke-width": 1, "stroke-dasharray": "3 3", visibility: "hidden" });
  const halos = series.map((x) => s("circle", { r: 10, fill: col(x), opacity: 0.16, visibility: "hidden" }));
  const dots = series.map((x) => s("circle", { r: 4.5, fill: col(x), stroke: "var(--bg)", "stroke-width": 2, visibility: "hidden" }));
  svg.append(cross, ...halos, ...dots);
  const hit = s("rect", { x: m.l, y: m.t, width: iw, height: ih, fill: "transparent", style: "touch-action:none" });
  svg.append(hit);
  host.append(svg);

  const tt = h("div", { class: "tt", hidden: true, role: "status" });
  host.append(tt);
  // Suivi fluide : le trait vertical et le point suivent la souris (ou le doigt) en continu, le point posé
  // exactement sur la courbe dessinée ; la bulle affiche le vrai cours le plus proche (jamais une valeur inventée
  // entre deux cours). Une seule mise à jour par image d'écran, quel que soit le nombre d'événements de la souris.
  let cur = n - 1, shown = -1, tw = 0, hw = 0, ttTop = null, raf = 0, pendingX = null;
  const nearest = (vx) => (n === 1 ? 0 : Math.max(0, Math.min(n - 1, Math.round((vx - m.l) / iw * (n - 1)))));
  function place(vx) {
    vx = Math.max(X(0), Math.min(X(n - 1), vx));
    cross.setAttribute("x1", vx); cross.setAttribute("x2", vx); cross.setAttribute("visibility", "visible");
    series.forEach((x, k) => {
      const c = curves[k].find((c) => vx >= c.x0 - 0.5 && vx <= c.x1 + 0.5), vis = c ? "visible" : "hidden";
      if (c) { const y = c.at(vx); dots[k].setAttribute("cx", vx); dots[k].setAttribute("cy", y); halos[k].setAttribute("cx", vx); halos[k].setAttribute("cy", y); }
      dots[k].setAttribute("visibility", vis); halos[k].setAttribute("visibility", vis);
    });
    const i = nearest(vx);
    if (i !== shown) { // le contenu de la bulle ne change qu'au passage d'un cours réel au suivant
      shown = cur = i;
      tt.replaceChildren(h("div", { class: "d", text: tipDateFmt(dates[i]) + (notes && notes[i] ? " · " + notes[i] : "") }));
      series.forEach((x) => { const v = x.values[i]; if (v != null) tt.append(h("div", { class: "r" }, h("span", { class: "k", style: `background:${col(x)}` }), h("b", { text: tipFmt(v) }), h("em", { text: x.name }))); });
      const v0 = series.length === 1 && ref && ref.value ? series[0].values[i] : null;
      if (v0 != null) { const pc = (v0 / ref.value - 1) * 100; tt.append(h("div", { class: "r" }, h("b", { class: pc >= 0 ? "up" : "down", text: fmtPct(pc, 2, true) }), h("em", { text: ref.short || ref.label }))); }
      tt.hidden = false;
      tw = tt.offsetWidth;
    }
    if (ttTop == null) { hw = host.clientWidth; ttTop = svg.getBoundingClientRect().top - host.getBoundingClientRect().top + m.t; tt.style.top = ttTop + "px"; }
    // à droite du point par défaut, à gauche s'il n'y a pas la place, et toujours bornée aux deux bords du graphique
    const px = (vx / W) * hw, left = px + 14 + tw > hw ? px - 14 - tw : px + 14;
    tt.style.left = Math.max(4, Math.min(left, hw - tw - 4)) + "px";
  }
  const show = (i) => place(X(Math.max(0, Math.min(n - 1, i))));
  const hide = () => {
    if (raf) { cancelAnimationFrame(raf); raf = 0; }
    pendingX = null; shown = -1; ttTop = null; tt.hidden = true;
    cross.setAttribute("visibility", "hidden");
    dots.forEach((d) => d.setAttribute("visibility", "hidden")); halos.forEach((d) => d.setAttribute("visibility", "hidden"));
  };
  const track = (e) => {
    const r = svg.getBoundingClientRect();
    pendingX = (e.clientX - r.left) / r.width * W;
    if (!raf) raf = requestAnimationFrame(() => { raf = 0; if (pendingX != null) place(pendingX); });
  };
  // Souris : suivi au survol, disparaît en sortant. Tactile : suivi au doigt tant qu'il touche l'écran (le
  // pointeur est "capturé" pour continuer à suivre le doigt même s'il glisse légèrement hors du graphique).
  hit.addEventListener("pointerdown", (e) => { hit.setPointerCapture(e.pointerId); track(e); e.preventDefault(); });
  hit.addEventListener("pointermove", track);
  hit.addEventListener("pointerup", (e) => { try { hit.releasePointerCapture(e.pointerId); } catch { /* déjà relâché */ } if (e.pointerType === "touch") hide(); });
  hit.addEventListener("pointercancel", hide);
  hit.addEventListener("pointerleave", (e) => { if (e.pointerType !== "touch") hide(); });
  // Un doigt qui reste immobile un instant sur le graphique (pour lire une valeur) peut être interprété par le
  // navigateur comme un appui long (menu contextuel / sélection de texte), ce qui annule le suivi tactile en
  // plein milieu du geste et fait disparaître brutalement le curseur. On neutralise ce geste natif à la source.
  hit.addEventListener("touchstart", (e) => e.preventDefault(), { passive: false });
  hit.addEventListener("touchmove", (e) => e.preventDefault(), { passive: false });
  hit.addEventListener("contextmenu", (e) => e.preventDefault());
  svg.addEventListener("focus", () => show(cur));
  svg.addEventListener("blur", hide);
  svg.addEventListener("keydown", (e) => {
    if (e.key === "ArrowLeft") { show(cur - 1); e.preventDefault(); }
    else if (e.key === "ArrowRight") { show(cur + 1); e.preventDefault(); }
    else if (e.key === "Escape") hide();
  });
}


/* redessine un graphique quand sa largeur change */
function autoRedraw(host, draw) {
  let w = host.clientWidth; // largeur déjà dessinée : seul un vrai changement de taille redessine
  const ro = new ResizeObserver(() => { if (host.clientWidth && host.clientWidth !== w) { w = host.clientWidth; draw(); } });
  ro.observe(host);
  return ro;
}

/* ---------- état global ---------- */
const RANGES = [["1M", 22], ["3M", 63], ["6M", 126], [T("1A", "1Y"), 400]];
let dash = null;
let activeTab = "overview";
const loaded = { market: 0, sources: 0, profile: 0 };

/* ---------- thème clair/sombre : préférence locale (cookie), indépendante du compte ---------- */
function currentTheme() { const v = cookie("theme"); return (v === "light" || v === "dark") ? v : "auto"; }
function applyTheme(v) {
  if (v === "light" || v === "dark") document.documentElement.dataset.theme = v; else delete document.documentElement.dataset.theme;
  const meta = $("theme-meta");
  if (meta) meta.content = (v === "dark" || (v === "auto" && matchMedia("(prefers-color-scheme: dark)").matches)) ? "#000000" : "#ffffff";
}
function setTheme(v) { document.cookie = `theme=${v}; max-age=31536000; path=/; samesite=lax`; applyTheme(v); renderProfile(); }

/* ---------- briques communes ---------- */
/* Section dépliable : le titre reste seul visible, le contenu s'ouvre au clic. */
function fold(title, meta, content, opts = {}) {
  const d = h("details", { class: "fold" + (opts.cls ? " " + opts.cls : "") },
    h("summary", {}, h("span", { class: "ft", text: title }), meta ? h("span", { class: "fm", text: meta }) : null),
    h("div", { class: "fb" }, content));
  if (opts.open) d.open = true;
  if (opts.onOpen) d.addEventListener("toggle", () => { if (d.open) opts.onOpen(d); });
  return d;
}

/* ---------- onglet Vue d'ensemble ---------- */
let lastStatus = null;
const ago = (d) => { const m = Math.round((Date.now() - d) / 60000); return m < 1 ? T("à l'instant", "just now") : m < 60 ? T(`il y a ${m} min`, `${m} min ago`) : T(`il y a ${Math.round(m / 60)} h`, `${Math.round(m / 60)} h ago`); };
function drawStatus() {
  const st = lastStatus; if (!st) return;
  const last = st.last_run ? new Date(st.last_run) : null, el = $("status");
  el.classList.toggle("busy", !!st.running);
  el.textContent = st.running ? T(`Mise à jour en cours (${t(st.phase || "…")})…`, `Updating (${t(st.phase || "…")})…`)
    : last ? `${T("Mis à jour à ", "Updated at ")}${last.toLocaleTimeString(LOC, { hour: "2-digit", minute: "2-digit" })} · ${ago(last)}` : T("Première collecte en cours…", "First collection in progress…");
  el.title = st.auto === false ? T("Mise à jour déclenchée par le planificateur du serveur", "Updates are triggered by the server scheduler")
    : T(`Mise à jour automatique en continu, une source à la fois${st.pending ? ` · ${st.pending} en attente` : ""}`, `Continuous automatic updates, one source at a time${st.pending ? ` · ${st.pending} pending` : ""}`);
}
function renderStatus(st, interval) { lastStatus = st; drawStatus(); renderBanner(st); }
setInterval(drawStatus, 20000);
function renderBanner(st) {
  const box = $("problems");
  box.hidden = st.sec_configured !== false;
  if (box.hidden) { box.replaceChildren(); return; }
  box.replaceChildren(h("div", {}, h("b", { text: T("SEC : ", "SEC: ") }),
    T("email de contact manquant, les positions des grands investisseurs sont désactivées. ", "contact email missing, big-investor positions are disabled. "),
    ME.admin ? h("button", { class: "link", onclick: () => showTab("profile"), text: T("Renseigner mon email (onglet Profil)", "Add my email (Profile tab)") })
      : h("span", { text: T("Demande à l'administrateur de le renseigner.", "Ask the administrator to set it.") })));
}

const GROUPS = [["BUY", "À acheter"], ["HOLD", "À garder"], ["WATCH", "À surveiller"], ["AVOID/REASSESS", "À éviter pour l'instant"]];
/* Carte cliquable sur toute sa surface : le bouton du nom s'étend sur la carte (voir .card.click dans le CSS). */
let liveQuotes = {};
function applyLive() {
  let n = 0;
  for (const [t, q] of Object.entries(liveQuotes)) {
    const row = document.querySelector(`.ri[data-t="${CSS.escape(t)}"] .ri-price`);
    if (!row) continue;
    row.replaceChildren(...[h("b", { text: money(q.price, row.closest(".ri").dataset.c) }), q.change_pct == null ? null : delta(q.change_pct)].filter(Boolean));
    n++;
  }
  const states = Object.values(liveQuotes).map((q) => q.state).filter(Boolean);
  const st = ["REGULAR", "PRE", "POST"].find((k) => states.includes(k)) || (states.length ? "CLOSED" : "REGULAR");
  const word = { REGULAR: T("en direct", "live"), PRE: T("avant-Bourse", "pre-market"), POST: T("après-Bourse", "after hours"), CLOSED: T("marché fermé, derniers cours", "market closed, last prices") }[st];
  const el = $("live"); if (el) el.textContent = n ? `· ${word} (${n} ${T("cours", "quotes")})` : "";
}
async function pollLive() {
  if (activeTab !== "overview" || document.hidden || $("detail").open) return;
  try { liveQuotes = (await api("/api/live")).quotes || {}; applyLive(); } catch (e) { /* silencieux : les cours enregistrés restent affichés */ }
}
function companyCard(r) {
  return h("div", { class: "ri click", "data-l": r.label || "", "data-t": r.ticker, "data-c": r.currency || "USD" },
    h("div", { class: "ri-name" }, logoImg(r.ticker, r.domain, 26),
      h("div", { class: "info" }, h("button", { class: "link main", onclick: () => openCompany(r.ticker, r.name), "aria-label": T(`Ouvrir l'analyse de ${r.name}`, `Open the analysis of ${r.name}`), text: r.name }),
        h("span", { class: "nm", text: r.ticker }))),
    h("div", { class: "ri-spark" }, sparkline(r.spark)),
    r.label ? pill(r.label, LABEL_COLOR[r.label]) : h("span"),
    h("div", { class: "ri-price" }, h("b", { text: r.price == null ? "–" : money(r.price, r.currency) }), r.change_1d == null ? null : delta(r.change_1d)));
}
function renderOverview(d) {
  dash = d;
  renderStatus(d.status, d.interval_min);
  const by = (k) => d.rows.filter((r) => r.label === k);
  const blocks = [];
  if (!d.rows.length) blocks.push(h("div", { class: "card empty-start" }, h("h2", { text: T("Ta liste est vide pour l'instant", "Your list is empty for now") }),
    h("p", { class: "mut", text: T("Ajoute des entreprises à suivre pour voir apparaître ta vue d'ensemble : pars des secteurs qui montent en ce moment, ou cherche-les directement par leur nom.", "Add companies to follow to see your overview come to life: start from the sectors rising right now, or search for them directly by name.") }),
    h("div", { class: "row", style: "display:flex;gap:10px;flex-wrap:wrap" }, h("button", { class: "btn primary", text: T("+ Ajouter des entreprises", "+ Add companies"), onclick: () => showTab("market") }),
      h("button", { class: "btn", text: T("Rechercher une entreprise", "Search a company"), onclick: () => { $("q").focus(); } }))));
  const head = (title, n, color) => h("h2", { class: "gh" }, h("i", { class: "dot", style: `background:${color}` }), title, h("span", { class: "count", text: n }));
  for (const [k, title] of GROUPS) {
    const rows = by(k);
    if (rows.length) blocks.push(head(title, rows.length, LABEL_COLOR[k]), h("div", { class: "cgrid" }, rows.map(companyCard)));
  }
  const wait = d.rows.filter((r) => !r.label);
  if (wait.length) blocks.push(head("En attente de données", wait.length, "var(--axis)"), h("div", { class: "cgrid" }, wait.map(companyCard)));
  if (d.avg_buy_perf != null) blocks.push(h("p", { class: "hint", text: T("Performance moyenne des « À acheter » depuis leur recommandation : ", "Average performance of the \"Buy\" picks since their recommendation: ") + fmtPct(d.avg_buy_perf, 2, true) + "." }));
  $("groups").replaceChildren(...blocks);
  applyLive();
}
function sparkline(d, w = 92, hh = 34) {
  if (!d || d.length < 2) return "";
  const mn = Math.min(...d), mx = Math.max(...d), r = mx - mn || 1;
  const pts = d.map((v, i) => `${((i / (d.length - 1)) * w).toFixed(1)},${(hh - 3 - ((v - mn) / r) * (hh - 6)).toFixed(1)}`).join(" ");
  const svg = s("svg", { width: w, height: hh, viewBox: `0 0 ${w} ${hh}`, role: "img", "aria-label": T("Évolution du cours", "Price trend") });
  svg.append(s("polyline", { fill: "none", stroke: d[d.length - 1] >= d[0] ? "var(--up)" : "var(--down)", "stroke-width": 1.8, points: pts, "stroke-linejoin": "round", "stroke-linecap": "round" }));
  return svg;
}

function rangeButtons(host, get, set) {
  host.replaceChildren(...RANGES.map(([l, n]) => h("button", { "aria-pressed": get() === n ? "true" : "false", onclick: () => { set(n); rangeButtons(host, get, set); } }, l)));
}

/* ---------- onglet Opportunités ---------- */
function signalItem(sg) {
  const up = sg.impact >= 0;
  return h("li", {},
    h("span", { class: "imp " + (up ? "up" : "down"), title: T("Poids de ce signal dans le score total : plus le chiffre est grand, plus il compte.", "Weight of this signal in the total score: the bigger the number, the more it counts."), text: `${up ? "▲ +" : "▼ −"}${fmtNum(Math.abs(sg.impact), 1)}` }),
    h("div", {},
      h("div", {}, h("span", { class: "kind", text: KIND_FR[sg.kind] || sg.kind }), sg.text),
      (sg.url || sg.source) && h("div", { class: "src" }, sg.url ? link(sg.url, T("Source : ", "Source: ") + (t(sg.source) || T("lien", "link"))) : (sg.source ? T("Source : ", "Source: ") + t(sg.source) : ""), sg.date ? " · " + fmtDate(sg.date) : "")));
}
function oppsBlocks(d) {
  const blocks = [h("p", { class: "mut", text: "Clique sur une entreprise pour voir pourquoi elle apparaît ici. ▲ pousse vers l'achat, ▼ appelle à la prudence." })];
  if (d.upcoming.length) blocks.push(h("p", { class: "mut" }, h("b", { text: "Prochains résultats : " }),
    d.upcoming.slice(0, 4).map((u) => T(`${u.ticker} le ${fmtDate(u.date)}`, `${u.ticker} on ${fmtDate(u.date)}`)).join(" · ")));
  blocks.push(h("div", { class: "olist" }, d.items.map((it) => {
    const det = h("details", { class: "fold opp" },
      h("summary", {}, logoImg(it.ticker, it.domain, 18), h("span", { class: "ft" }, it.name, h("span", { class: "nm tk", text: it.ticker })), it.level ? pill(it.level, LEVEL_COLOR[it.level], it.level_label || it.level) : null),
      h("div", { class: "fb" },
        h("div", { class: "src", title: T("Somme des signaux ci-dessous : plus la valeur est grande (positive ou négative), plus le mouvement du cours est probable.", "Sum of the signals below: the bigger the value (positive or negative), the more likely the price move."),
          text: `${T("Cours", "Price")} ${money(it.price, it.currency)} · ${T("total des signaux", "total signal score")} ${it.total > 0 ? "+" : it.total < 0 ? "−" : ""}${fmtNum(Math.abs(it.total), 1)}` }),
        it.signals.length ? h("ul", { class: "sig" }, it.signals.slice(0, 3).map(signalItem)) : h("div", { class: "empty", text: "Pas encore de signaux (collecte en cours)." }),
        it.signals.length > 3 ? fold(T(`Voir les ${it.signals.length - 3} autres signaux`, `See ${it.signals.length - 3} more signal${it.signals.length - 3 === 1 ? "" : "s"}`), null, h("ul", { class: "sig" }, it.signals.slice(3).map(signalItem)), { cls: "inner" }) : null,
        h("button", { class: "btn mini", style: "margin-top:8px", onclick: () => openCompany(it.ticker, it.name), text: "Ouvrir l'analyse complète" })));
    return det;
  })));
  return blocks;
}

/* Traductions des mouvements de positions (aussi utilisées dans la fiche d'une action). */
const KIND_INV = { new: T("Nouvelle position", "New position"), add: T("Renforcé", "Increased"), reduce: T("Allégé", "Reduced"), exit: T("Vendu en totalité", "Sold entirely"), hold: T("Conservé", "Held") };
/* ---------- recherche d'entreprise ---------- */
const q = $("q"), ql = $("q-list");
let qTimer = null, qSeq = 0, qItems = [], qIdx = -1;
function qHide() { ql.hidden = true; q.setAttribute("aria-expanded", "false"); qIdx = -1; }
function qSet(idx) {
  qIdx = idx;
  [...ql.children].forEach((li, i) => li.setAttribute("aria-selected", i === idx ? "true" : "false"));
  q.setAttribute("aria-activedescendant", idx >= 0 ? `q-opt-${idx}` : "");
}
function qMessage(text, cls = "empty") { ql.replaceChildren(h("li", { class: cls, role: "presentation", text })); ql.hidden = false; q.setAttribute("aria-expanded", "true"); }
async function runSearch(v) {
  const seq = ++qSeq;
  qMessage("Recherche…");
  let d;
  try { d = await api("/api/search?q=" + encodeURIComponent(v)); } catch (e) { if (seq === qSeq) qMessage(T("Recherche impossible : ", "Search failed: ") + e.message); return; }
  if (seq !== qSeq) return;
  qItems = d.results; qIdx = -1;
  if (!qItems.length) {
    const why = d.errors.length ? T(" (source indisponible : voir l'onglet Sources)", " (source unavailable: see the Sources tab)") : "";
    qMessage(T(`Aucun résultat pour « ${v} »${why}. Tu peux aussi taper directement un symbole (ex. AAPL) puis Entrée.`, `No results for "${v}"${why}. You can also type a ticker symbol directly (e.g. AAPL) and press Enter.`));
    return;
  }
  ql.replaceChildren(...qItems.map((r, i) => h("li", { role: "option", id: `q-opt-${i}`, "aria-selected": "false", onmousedown: (e) => { e.preventDefault(); qPick(r); } },
    h("b", { text: r.symbol }), h("span", { class: "qn", text: r.name }), h("span", { class: "src", text: [r.exchange, r.type].filter(Boolean).join(" · ") }), r.followed ? h("span", { class: "tag", text: "suivi" }) : null)));
  ql.hidden = false; q.setAttribute("aria-expanded", "true");
}
function qPick(r) { qHide(); q.value = ""; openCompany(r.symbol, r.name); }
q.addEventListener("input", () => { clearTimeout(qTimer); const v = q.value.trim(); if (!v) { qHide(); return; } qTimer = setTimeout(() => runSearch(v), 250); });
q.addEventListener("keydown", (e) => {
  if (e.key === "ArrowDown" && qItems.length) { qSet(Math.min(qIdx + 1, qItems.length - 1)); e.preventDefault(); }
  else if (e.key === "ArrowUp" && qItems.length) { qSet(Math.max(qIdx - 1, 0)); e.preventDefault(); }
  else if (e.key === "Escape") qHide();
  else if (e.key === "Enter") {
    e.preventDefault(); // sinon la touche « répétée » valide le bouton Fermer qui reçoit le focus à l'ouverture
    const v = q.value.trim();
    if (qIdx >= 0 && qItems[qIdx]) qPick(qItems[qIdx]);
    else if (/^[A-Za-z0-9][A-Za-z0-9.\-]{0,11}$/.test(v) && (!qItems.length || qItems.some((r) => r.symbol.toUpperCase() === v.toUpperCase()) || v === v.toUpperCase())) { qHide(); q.value = ""; openCompany(v.toUpperCase()); }
    else if (qItems.length) qPick(qItems[0]);
  }
});
q.addEventListener("blur", () => setTimeout(qHide, 150));
q.addEventListener("focus", () => { if (q.value.trim() && qItems.length) { ql.hidden = false; q.setAttribute("aria-expanded", "true"); } });

/* ---------- onglet Secteurs ---------- */
const sectorSlots = {};
function sectorSlot(etf) {
  if (sectorSlots[etf]) return sectorSlots[etf];
  const used = new Set(Object.values(sectorSlots));
  for (let i = 1; i <= 8; i++) if (!used.has(i)) return (sectorSlots[etf] = i);
  return 8;
}
const STATE_COLOR = { strong_up: "var(--good)", up: "var(--hold)", neutral: "var(--axis)", down: "var(--crit)" };
let dirtyFollow = false;
/* Suivre / ne plus suivre : les listes affichées et les fiches en mémoire sont rafraîchies à la fermeture de la fenêtre. */
function followChanged(ticker) { dirtyFollow = true; CO_CACHE.delete(ticker); SEC_CACHE.clear(); for (const k of Object.keys(loaded)) loaded[k] = 0; }
function followButton(ticker, name, followed) {
  const b = h("button", { class: "btn mini", "aria-pressed": followed ? "true" : "false" }, followed ? "Suivi ✓" : "Suivre");
  if (followed) b.disabled = true;
  else b.onclick = async () => { b.disabled = true; b.textContent = T("Ajout…", "Adding…"); try { await api(`/api/watchlist/${encodeURIComponent(ticker)}?name=${encodeURIComponent(name || "")}`, { method: "POST" }); followChanged(ticker); b.textContent = T("Suivi ✓", "Following ✓"); } catch (e) { b.textContent = T("Erreur", "Error"); } };
  return b;
}
function companyChip(b) {
  return h("div", { class: "card click chip2" },
    h("div", { class: "co-top" }, logoImg(b.ticker, b.domain, 15), h("button", { class: "link main", onclick: () => openCompany(b.ticker, b.name), "aria-label": T(`Analyser ${b.ticker}`, `Analyze ${b.ticker}`), text: b.ticker }), pill(b.label, LABEL_COLOR[b.label])),
    h("span", { class: "nm", text: b.name || "" }));
}
function sectorCard(x, i) {
  return h("div", { class: "card click sec", "data-s": x.state },
    h("div", { class: "oh" },
      h("div", {}, h("h2", {}, h("button", { class: "link main", onclick: () => openSector(x.id), "aria-label": T(`Voir toutes les entreprises du secteur ${x.name}`, `See all companies in the ${x.name} sector`), text: `${i + 1}. ${x.name}` })),
        h("span", { class: "nm", text: `${x.etf} · ${T("tendance", "trend")} ${x.trend_label}` })),
      h("div", { class: "sright" }, delta(x.r3m == null ? null : x.r3m * 100), h("span", { class: "mut", text: " sur 3 mois" }), pill(x.state, STATE_COLOR[x.state], x.state_label))),
    x.best.length ? h("div", { class: "chips2" }, x.best.slice(0, 3).map(companyChip)) : h("div", { class: "empty", text: "Classement des entreprises indisponible." }),
    h("div", { class: "more", text: "Voir toutes les entreprises du secteur →" }));
}

/* ---------- fenêtre d'un secteur : beaucoup d'entreprises ---------- */
const sector = { id: null };
async function openSector(id) {
  const dlg = $("detail");
  sector.id = id; company.ticker = null; company.from = null;
  $("d-title").textContent = T("Secteur", "Sector");
  $("d-body").replaceChildren(loadingView(id.toUpperCase(), T("Classement des entreprises du secteur", "Ranking the sector's companies"),
    [T("Composition de l'ETF", "ETF holdings"), T("Cours des entreprises", "Company prices"), T("Tendance et momentum", "Trend and momentum"), T("Classement final", "Final ranking")]));
  if (!dlg.open) { dlg.showModal(); $("d-body").focus({ preventScroll: true }); }
  const sh = SEC_CACHE.get(id);
  if (sh) { renderSector(sh.d); prefetchCompanies(sh.d.companies || []); if (!fresh(sh)) api("/api/sector/" + encodeURIComponent(id)).then((x) => { SEC_CACHE.set(id, { d: x, at: Date.now() }); }).catch(() => {}); return; }
  let d;
  try { d = await api("/api/sector/" + encodeURIComponent(id)); }
  catch (e) { if (sector.id === id) $("d-body").replaceChildren(h("div", { class: "err", text: T("Erreur : ", "Error: ") + e.message })); return; }
  if (sector.id !== id) return;
  if (!d.partial) SEC_CACHE.set(id, { d, at: Date.now() });
  renderSector(d);
  // les entreprises du secteur se préchargent en arrière-plan : un clic dessus juste après devient instantané
  prefetchCompanies(d.companies || []);
  if (d.partial) { // top affiché tout de suite ; le classement complet arrive quelques secondes après
    let tries = 0;
    const again = async () => {
      if (sector.id !== id || !$("detail").open || ++tries > 8) return;
      try { const x = await api("/api/sector/" + encodeURIComponent(id)); if (sector.id !== id) return; renderSector(x); if (x.partial) setTimeout(again, 4000); }
      catch (e) { /* on garde l'affichage actuel */ }
    };
    setTimeout(again, 4000);
  }
}
function sectorRow(b, i, sid) {
  return h("div", { class: "chg click srow", "data-l": b.label },
    h("span", { class: "rk", text: String(i + 1) }),
    h("div", { class: "sr-name" }, logoImg(b.ticker, b.domain, 22), h("div", { class: "info" }, h("button", { class: "link main", onclick: () => openCompany(b.ticker, b.name, false, () => openSector(sid)), "aria-label": T(`Analyser ${b.ticker}`, `Analyze ${b.ticker}`) }, b.ticker), h("span", { class: "nm", text: b.name || "" }))),
    h("div", { class: "sr-mid" }, pill(b.label, LABEL_COLOR[b.label], `${b.label === "AVOID/REASSESS" ? "AVOID" : b.label} · ${b.score.toFixed(0)}`)),
    h("div", { class: "sr-num" }, delta(b.r3m == null ? null : b.r3m * 100), h("span", { class: "src", text: " 3 mois" })),
    h("div", { class: "sr-num" }, delta(b.rs3m == null ? null : b.rs3m * 100), h("span", { class: "src", text: " vs secteur" })),
    h("div", { class: "acts" }, followButton(b.ticker, b.name, b.followed)));
}
function renderSector(d) {
  $("d-title").textContent = d.name;
  const body = [h("div", { class: "card-head" },
    h("div", {}, h("span", { class: "mut", text: `${d.etf} · ${d.kind}` }), " ", d.state ? pill(d.state, STATE_COLOR[d.state], d.state_label) : null, " ", d.r3m == null ? null : [delta(d.r3m * 100), h("span", { class: "mut", text: " sur 3 mois" })]),
    h("button", { class: "btn mini", text: "Actualiser", onclick: () => api(`/api/sector/${encodeURIComponent(d.id)}?refresh=true`).then(renderSector) }))];
  if (d.partial) body.push(h("p", { class: "hint", text: T("Aperçu du classement : la liste complète se charge…", "Ranking preview: the full list is loading…") }));
  body.push(d.companies.length
    ? [h("p", { class: "mut", text: T(`${d.companies.length} entreprises classées (tendance, momentum, surperformance face au secteur). Clique sur une entreprise pour l'analyser.`, `${d.companies.length} companies ranked (trend, momentum, outperformance vs. the sector). Click a company to analyze it.`) }),
      h("div", { class: "srows" }, d.companies.map((b, i) => sectorRow(b, i, d.id)))]
    : h("div", { class: "empty", text: "Aucune entreprise n'a pu être classée (données de cours indisponibles)." }));
  if (d.errors && d.errors.length) body.push(h("p", { class: "hint", text: T("Non récupéré : ", "Not retrieved: ") + d.errors.join(" ; ") }));
  body.push(h("p", { class: "note", text: "Classement technique fondé sur les seuls cours. Ce n'est pas un conseil en investissement." }));
  $("d-body").replaceChildren(...body.flat()); enter($("d-body"));
}
function sectorsBlocks(d) {
  if (!d.available) return [h("div", { class: "card" }, h("h2", { text: "Analyse des secteurs en cours" }),
    h("p", { class: "mut", text: "Le premier calcul prend 1 à 2 minutes après le lancement." }))];
  const top = d.sectors.slice(0, 5), rest = d.sectors.slice(5);
  const blocks = [h("p", { class: "mut", text: "Clique sur un secteur pour voir toutes ses entreprises, classées ; sous chaque secteur, ses 3 meilleures." }),
    h("div", { class: "olist" }, top.map(sectorCard))];
  if (rest.length) blocks.push(fold(T(`Voir les ${rest.length} autres secteurs`, `See the ${rest.length} other sectors`), null, h("div", { class: "scroll" }, h("table", { class: "wrapt" },
    h("thead", {}, h("tr", {}, h("th", { text: "Secteur" }), h("th", { class: "l", text: "État" }), h("th", { text: "3 mois" }), h("th", { text: "vs marché" }))),
    h("tbody", {}, rest.map((x) => h("tr", { class: "rowlink", onclick: (e) => { if (!e.target.closest("button")) openSector(x.id); } },
      h("td", {}, h("button", { class: "link", onclick: () => openSector(x.id), "aria-label": T(`Voir les entreprises du secteur ${x.name}`, `See the companies in the ${x.name} sector`) }, x.name), h("span", { class: "nm", text: x.etf })), h("td", { class: "l" }, pill(x.state, STATE_COLOR[x.state], x.state_label)),
      h("td", {}, delta(x.r3m == null ? null : x.r3m * 100)), h("td", {}, delta(x.rs3m == null ? null : x.rs3m * 100)))))))));
  const chartHost = h("div", { class: "chart" });
  const names = Object.fromEntries(d.sectors.map((x) => [x.etf, x.name]));
  const draw = () => {
    const series = Object.entries(d.chart.series).map(([etf, vals]) => {
      const base = vals.find((v) => v != null);
      return { name: names[etf] || etf, slot: sectorSlot(etf), values: vals.map((v) => (v == null || !base ? null : (v / base) * 100)) };
    });
    lineChart(chartHost, { dates: d.chart.dates, series, height: 300, yfmt: (v) => fmtNum(v, 0) });
  };
  let ro = null;
  blocks.push(fold("Graphique comparatif des secteurs", "6 mois, base 100", chartHost, { onOpen: () => { draw(); if (!ro) ro = autoRedraw(chartHost, draw); } }));
  return blocks;
}
/* Secteurs (contexte large) et opportunités (idées concrètes) réunis dans un seul onglet : un même écran,
   deux vues qu'on bascule d'un tap, sans rechargement (les deux jeux de données sont chargés ensemble une fois). */
let marketView = "sectors", marketData = null;
function paintMarket() {
  if (!marketData) return;
  const { sd, od } = marketData;
  const seg = h("div", { class: "seg mkt-seg", role: "group", "aria-label": "Vue" },
    [["sectors", "Secteurs"], ["opps", "Opportunités"]].map(([v, l]) =>
      h("button", { class: "segb", "aria-pressed": String(marketView === v), text: l, onclick: () => { marketView = v; paintMarket(); } })));
  $("market").replaceChildren(seg, ...(marketView === "sectors" ? sectorsBlocks(sd) : oppsBlocks(od)));
}
async function renderMarket() {
  const [sd, od] = await Promise.all([api("/api/sectors"), api("/api/opportunities")]);
  loaded.market = Date.now();
  marketData = { sd, od };
  paintMarket();
}
/* ---------- onglet Sources ---------- */
async function renderSources() {
  const d = await api("/api/sources");
  loaded.sources = Date.now();
  const inProv = new Set(d.providers.map((p) => p.name));
  const blocks = [h("div", { class: "card" }, h("h2", { text: "Fournisseurs de données" }),
    h("p", { class: "mut", text: "Plus il y a de sources actives, plus les cours sont recoupés et fiables. Aucune source gratuite n'est garantie exacte à 100 % : c'est le recoupement qui détecte les écarts. Les clés se règlent dans l'onglet Profil." }),
    h("div", { class: "provs" }, d.providers.map((p) => h("div", { class: "prov" },
      h("div", { class: "oh" }, h("div", {}, h("b", { text: p.label }), h("div", { class: "src", text: p.note }), h("div", { class: "src", text: `${t(p.realtime)}${p.free_limit ? " · " + T("gratuit : ", "free: ") + t(p.free_limit) : ""}` })),
        h("div", { class: "acts" }, pill(p.paused ? "En pause" : p.configured ? "Actif" : "Clé manquante", p.paused ? "var(--warn)" : p.configured ? "var(--good)" : "var(--axis)"),
          p.daily_limit ? h("span", { class: "src", text: T(`${p.used_today}/${p.daily_limit} aujourd'hui`, `${p.used_today}/${p.daily_limit} today`) }) : null))))))];
  const rows = d.sources.filter((x) => !inProv.has(x.source)).map((x) => {
    const st = x.paused ? ["En pause", "var(--warn)"] : x.failing ? ["Indisponible", "var(--warn)"] : x.ok ? ["Actif", "var(--good)"] : ["Pas encore utilisée", "var(--axis)"];
    return h("tr", {}, h("td", { class: "l", text: x.label }), h("td", { class: "l" }, pill(st[0], st[1])),
      h("td", { class: "l", text: x.last_ok ? new Date(x.last_ok).toLocaleString(LOC) : "–" }));
  });
  blocks.push(h("h3", { text: "Autres sources" }), h("div", { class: "card flush" }, h("div", { class: "scroll" }, h("table", { class: "wrapt" },
    h("thead", {}, h("tr", {}, h("th", { class: "l", text: "Source" }), h("th", { class: "l", text: "État" }), h("th", { class: "l", text: "Dernière réussite" }))),
    h("tbody", {}, rows)))));
  blocks.push(h("div", { style: "margin-top:10px" }, h("button", { class: "btn", text: "Relancer la collecte maintenant", onclick: async () => { await fetch("/api/refresh", { method: "POST" }); setTimeout(renderSources, 1500); } })));
  $("sources").replaceChildren(...blocks);
}

/* ---------- onglet Profil : mes infos, langue, devise, suivi, clés, sécurité ---------- */
const postJson = (path, body) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
async function saveApiKey(p, input, msg) {
  if (!input.value.trim()) return;
  msg.textContent = "…";
  try { await postJson("/api/settings", { api_keys: { [p.key_name]: input.value } }); msg.textContent = T("Enregistrée.", "Saved."); setTimeout(renderProfile, 1200); }
  catch (e) { msg.textContent = T("Erreur : ", "Error: ") + e.message; }
}
function field(label, input, hint) { return h("label", { class: "fld" }, h("span", { text: label }), input, hint ? h("span", { class: "src", text: hint }) : null); }

/* ---------- appli installable ---------- */
const isStandalone = () => matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
const isIOS = () => /iphone|ipad|ipod/i.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
// la proposition d'installation est gardée de côté par index.html (window.__installEvt) ; ici on rafraîchit juste l'onglet
addEventListener("beforeinstallprompt", () => { if (activeTab === "profile") renderProfile(); });
addEventListener("appinstalled", () => { window.__installEvt = null; if (activeTab === "profile") renderProfile(); });
function appCard() {
  let body;
  if (isStandalone()) body = h("p", { class: "mut", text: T("Tu utilises l'appli installée ✓", "You're using the installed app ✓") });
  else if (window.__installEvt) body = [
    h("p", { class: "mut", text: T("Installe Market Sentinel comme une vraie appli : une icône sur ton écran d'accueil ou ton bureau, et sa propre fenêtre, sans la barre du navigateur.",
      "Install Market Sentinel as a real app: an icon on your home screen or desktop, and its own window, without the browser bar.") }),
    h("div", { class: "row" }, h("button", { class: "btn primary", text: T("Installer l'appli", "Install the app"), onclick: async () => {
      const e = window.__installEvt;
      if (!e) return;
      e.prompt();
      try { await e.userChoice; } catch { /* fenêtre fermée */ }
      window.__installEvt = null; // une proposition ne sert qu'une fois
      renderProfile();
    } }))];
  else if (isIOS()) body = h("p", { class: "mut", text: T("Sur iPhone ou iPad, dans Safari : touche le bouton Partager (le carré avec une flèche vers le haut), puis « Sur l'écran d'accueil ».",
    "On iPhone or iPad, in Safari: tap the Share button (the square with an up arrow), then “Add to Home Screen”.") });
  else body = h("p", { class: "mut", text: T("Dans Chrome ou Edge : clique sur l'icône d'installation à droite de la barre d'adresse, ou ouvre le menu ⋮ puis « Installer Market Sentinel ». Sur Android : menu ⋮ puis « Installer l'application ».",
    "In Chrome or Edge: click the install icon at the right of the address bar, or open the ⋮ menu then “Install Market Sentinel”. On Android: ⋮ menu then “Install app”.") });
  return h("div", { class: "card" }, h("h2", { text: T("Application", "App") }), body);
}

async function renderProfile() {
  const d = await api("/api/profile");
  loaded.profile = Date.now();
  const p = d.profile, shown = p.display_name || d.user;
  $("me-i").textContent = (shown[0] || "?").toUpperCase();
  const blocks = [];
  blocks.push(h("div", { class: "card prof-head" }, h("div", { class: "avatar big", "aria-hidden": "true" }, (shown[0] || "?").toUpperCase()),
    h("div", { style: "flex:1;min-width:0" }, h("h2", { style: "margin:0", text: shown }),
      h("div", { class: "mut", text: d.admin ? `@${d.user}${d.contact_email ? " · " + d.contact_email : ""} · ${T("administrateur", "administrator")}` : T("Compte invité", "Guest account") }))));
  blocks.push(appCard());

  // langue et devise : appliquées tout de suite (rechargement de la page) ; thème : appliqué instantanément, sans rechargement
  const lang = h("div", { class: "seg", role: "group", "aria-label": "Langue" }, [["fr", "Français"], ["en", "English"]].map(([v, l]) =>
    h("button", { class: "segb", "aria-pressed": String(p.language === v), text: l, onclick: () => savePrefs({ language: v, currency: p.currency === defaultCur(p.language) ? defaultCur(v) : p.currency }) })));
  const cur = h("select", { "aria-label": "Devise d'affichage", onchange: (e) => savePrefs({ currency: e.target.value }) },
    CURRENCIES.map((c) => h("option", { value: c, selected: c === p.currency }, `${c} · ${CUR_NAMES[c]()}`)));
  const themeNow = currentTheme();
  const theme = h("div", { class: "seg", role: "group", "aria-label": "Thème" }, [["auto", T("Système", "System")], ["light", T("Clair", "Light")], ["dark", T("Sombre", "Dark")]].map(([v, l]) =>
    h("button", { class: "segb", "aria-pressed": String(themeNow === v), text: l, onclick: () => setTheme(v) })));
  blocks.push(h("div", { class: "card" }, h("h2", { text: "Préférences d'affichage" }),
    h("div", { class: "prefs" }, field(T("Langue de l'interface", "Interface language"), lang),
      field(T("Devise d'affichage", "Display currency"), cur),
      field(T("Thème", "Theme"), theme)),
    h("p", { class: "hint", text: RATES_INFO && RATES_INFO.date
      ? T(`Taux de change : ${RATES_INFO.source || "BCE"} du ${fmtDate(RATES_INFO.date)}. Les cours et montants sont convertis ; les variations en % restent celles de la devise de cotation de chaque titre.`,
          `Exchange rates: ${RATES_INFO.source === "BCE" ? "ECB" : RATES_INFO.source || "ECB"} of ${fmtDate(RATES_INFO.date)}. Prices and amounts are converted; % changes stay those of each stock's trading currency.`)
      : T("Taux de change indisponibles pour l'instant : les montants restent dans leur devise d'origine.", "Exchange rates unavailable for now: amounts stay in their original currency.") })));

  // infos personnelles
  const name = h("input", { type: "text", maxlength: 40, value: p.display_name || "", placeholder: d.admin ? d.user : "", "aria-label": "Nom affiché" });
  const email = h("input", { type: "email", value: d.contact_email || "", placeholder: "toi@exemple.com", disabled: d.email_from_env || !d.admin, "aria-label": "Email" });
  const msg = h("span", { class: "mut", role: "status" });
  const save = h("button", { class: "btn", text: "Enregistrer", onclick: async () => {
    msg.textContent = "…";
    try { const body = { display_name: name.value }; if (d.admin && !d.email_from_env && email.value.trim() !== (d.contact_email || "")) body.contact_email = email.value; await postJson("/api/profile", body); msg.textContent = T("Enregistré.", "Saved."); setTimeout(renderProfile, 800); }
    catch (e) { msg.textContent = T("Erreur : ", "Error: ") + e.message; }
  } });
  blocks.push(h("div", { class: "card" }, h("h2", { text: "Mes informations" }),
    h("div", { class: "prefs" }, d.admin ? field(T("Identifiant", "Username"), h("input", { type: "text", value: d.user, disabled: true })) : null, field(T("Nom affiché", "Display name"), name),
      d.admin ? field("Email", email, T("Sert aussi à identifier le programme auprès de la SEC (obligatoire pour les positions des grands investisseurs) ; partagé par tous les utilisateurs, jamais envoyé ailleurs.", "Also used to identify the program to the SEC (required for big-investor positions); shared by all users, never sent anywhere else.")) : null),
    h("div", { class: "row" }, save, msg)));

  // suivi
  const f = d.follow, c = f.counts || {};
  const unfollow = (r) => h("button", { class: "btn mini ghost", text: "Ne plus suivre", onclick: async () => { await api(`/api/watchlist/${encodeURIComponent(r.ticker)}`, { method: "DELETE" }); followChanged(r.ticker); dirtyFollow = false; renderProfile(); } });
  blocks.push(h("div", { class: "card" }, h("h2", { text: "Mon suivi" }),
    h("div", { class: "stats" }, [[f.total, T("entreprises suivies", "followed companies")], [c.BUY || 0, "BUY"], [c.HOLD || 0, "HOLD"], [c.WATCH || 0, "WATCH"], [c["AVOID/REASSESS"] || 0, "AVOID"]]
      .map(([n, l]) => h("div", { class: "stat" }, h("b", { text: String(n) }), h("span", { text: l })))),
    f.avg_buy_perf != null ? h("p", { class: "hint", text: T("Performance moyenne des « À acheter » depuis leur recommandation : ", "Average performance of BUY picks since their recommendation: ") + fmtPct(f.avg_buy_perf, 2, true) }) : null,
    d.last_run ? h("p", { class: "hint", text: T("Dernière mise à jour : ", "Last update: ") + new Date(d.last_run).toLocaleString(LOC) }) : null,
    f.total ? fold(T(`Voir les ${f.total} entreprises suivies`, `Show the ${f.total} followed companies`), null, h("div", { class: "ilist" }, f.rows.map((r) => h("div", { class: "ri sm", "data-l": r.label || "" },
      h("div", { class: "ri-name" }, h("button", { class: "link", onclick: () => openCompany(r.ticker, r.name), text: r.name }), h("span", { class: "nm", text: r.ticker })),
      r.label ? pill(r.label, LABEL_COLOR[r.label]) : h("span"), r.removable ? unfollow(r) : h("span", { class: "src", text: "Liste de base" }))))) : null));

  // clés et connexions
  const keyed = d.providers.filter((x) => x.needs_key);
  if (d.admin) blocks.push(h("div", { class: "card" }, h("h2", { text: T("Clés d'API partagées", "Shared API keys") }),
    h("p", { class: "mut", text: T("Ces clés servent à tous les utilisateurs du site : elles ajoutent des sources en temps réel et permettent de recouper les cours. Elles restent côté serveur et ne sont jamais affichées aux autres comptes.", "These keys serve every user of the site: they add real-time sources and let prices be cross-checked. They stay on the server and are never shown to other accounts.") }),
    h("div", { class: "provs" }, keyed.map((x) => {
      const input = !x.from_env ? h("input", { type: "password", placeholder: x.configured ? "clé enregistrée (laisser vide pour la garder)" : "colle ta clé ici", autocomplete: "off", "aria-label": T(`Clé ${x.label}`, `${x.label} key`) }) : null;
      const m = h("span", { class: "mut" });
      return h("div", { class: "prov" }, h("div", { class: "oh" }, h("div", {}, h("b", { text: x.label }), h("div", { class: "src", text: x.note })),
        pill(x.configured ? "Actif" : "Clé manquante", x.configured ? "var(--good)" : "var(--axis)")),
        h("div", { class: "row" }, input, input ? h("button", { class: "btn mini", text: "Enregistrer", onclick: () => saveApiKey(x, input, m) }) : h("span", { class: "src", text: "Définie sur le serveur" }),
          x.signup ? h("a", { class: "src", href: x.signup, target: "_blank", rel: "noopener noreferrer", text: "Obtenir une clé gratuite" }) : null, m));
    }))));

  // comptes (administrateur)
  if (d.admin) blocks.push(usersCard(d.users || []));

  // sécurité
  const cur1 = h("input", { type: "password", autocomplete: "current-password", "aria-label": "Mot de passe actuel" }), new1 = h("input", { type: "password", autocomplete: "new-password", "aria-label": "Nouveau mot de passe" });
  const pm = h("span", { class: "mut", role: "status" });
  blocks.push(h("div", { class: "card" }, h("h2", { text: "Sécurité" }),
    !d.admin ? h("p", { class: "mut", text: T("Tu restes connecté sur cet appareil. Si tu te déconnectes, il te faudra un nouveau code d'invitation pour revenir.", "You stay signed in on this device. If you sign out, you'll need a new invitation code to come back.") })
      : d.password_env ? h("p", { class: "mut", text: "Le mot de passe est défini sur le serveur (variable LOGIN_PASSWORD) : modifie-le dans Cloud Run." })
      : [h("div", { class: "prefs" }, field(T("Mot de passe actuel", "Current password"), cur1), field(T("Nouveau mot de passe (8 caractères min.)", "New password (8 characters min.)"), new1)),
        h("div", { class: "row" }, h("button", { class: "btn", text: "Changer le mot de passe", onclick: async () => {
          pm.textContent = "…";
          try { await postJson("/api/password", { current: cur1.value, new: new1.value }); pm.textContent = T("Mot de passe modifié.", "Password changed."); cur1.value = new1.value = ""; }
          catch (e) { pm.textContent = T("Erreur : ", "Error: ") + e.message; }
        } }), pm)],
    h("div", { class: "row", style: "margin-top:14px" }, h("button", { class: "btn ghost", text: "Déconnexion", onclick: logout }))));
  $("profile").replaceChildren(...blocks.flat());
}
function usersCard(users) {
  const guests = users.filter((u) => !u.owner);
  const rows = guests.map((u) => {
    const pmsg = h("span", { class: "mut" });
    const del = h("button", { class: "btn mini ghost", text: T("Supprimer l'accès", "Remove access"), onclick: async () => {
      if (!confirm(T(`Supprimer le compte de « ${u.display} » ? La personne sera déconnectée et devra demander un nouveau code pour revenir.`, `Delete "${u.display}"'s account? They will be signed out and need a new code to come back.`))) return;
      try { await api(`/api/users/${encodeURIComponent(u.name)}`, { method: "DELETE" }); renderProfile(); } catch (e) { pmsg.textContent = t("Erreur : ") + e.message; }
    } });
    return h("div", { class: "urow" },
      h("div", { class: "ri-name" }, h("b", { text: u.display }), h("span", { class: "nm", text: u.created ? T(`invité le ${fmtDate(u.created)}`, `invited ${fmtDate(u.created)}`) : "" })),
      pill("user", "var(--axis)", T("Invité", "Guest")),
      h("div", { class: "acts" }, del, pmsg));
  });
  return h("div", { class: "card" }, h("h2", { text: T("Accès au site", "Site access") }),
    h("p", { class: "mut", text: T("L'accès se fait uniquement sur invitation : quand quelqu'un demande un code depuis la page de connexion, tu le reçois par email, et c'est toi qui décides de le lui transmettre. Tu es le seul administrateur.",
      "Access is by invitation only: when someone requests a code from the sign-in page, you receive it by email and decide whether to pass it on. You are the only administrator.") }),
    guests.length ? h("div", { class: "ulist" }, rows) : h("div", { class: "empty", text: T("Aucun invité pour l'instant.", "No guests yet.") }));
}
const CUR_NAMES = { EUR: () => T("Euro", "Euro"), USD: () => T("Dollar US", "US dollar"), GBP: () => T("Livre sterling", "Pound sterling"), CHF: () => T("Franc suisse", "Swiss franc"),
  CAD: () => T("Dollar canadien", "Canadian dollar"), JPY: () => T("Yen", "Yen"), AUD: () => T("Dollar australien", "Australian dollar") };
const defaultCur = (lg) => (lg === "en" ? "USD" : "EUR");
async function savePrefs(body) {
  try { await postJson("/api/profile", body); location.reload(); } catch (e) { $("status").textContent = T("Erreur : ", "Error: ") + e.message; }
}
async function logout() { await fetch("/logout", { method: "POST" }); location.href = "/login"; }

/* ---------- fiabilité des données : cours recoupés, comptes SEC, documents officiels ---------- */
const pctN = (v, d = 1) => (v == null ? "–" : fmtNum(v, d) + PCT); // pourcentage sans signe forcé
const fmtCross = (v, f) => (v == null ? "–" : f === "pct" ? pctN(v * 100, 1) : fmtNum(v, 1));
function dataPanel(d) {
  const q = d.data_quality, off = d.official || {}, out = [];
  if (q.reasons.length || q.flags.length) out.push(h("ul", { class: "plain" }, q.flags.map((x) => h("li", {}, h("span", { class: "down", text: "▼ " }), x)), q.reasons.map((x) => h("li", {}, h("span", { class: "up", text: "▲ " }), x))));
  if (q.consensus) out.push(h("h3", { text: T(`Cours ${q.consensus.live ? "en direct" : "récent"} : ${q.consensus.n} source(s)`, `${q.consensus.live ? "Live" : "Recent"} price: ${q.consensus.n} source(s)`) }), h("div", { class: "scroll" }, h("table", { class: "wrapt matrix" }, h("tbody", {},
    q.consensus.sources.map((s) => h("tr", {}, h("td", { text: s.label }), h("td", { text: money(s.price, d.currency) }), h("td", { class: "src", text: s.delayed ? "différé" : "temps réel" }),
      h("td", { class: "src", text: new Date(s.ts).toLocaleString(LOC, { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }) }), h("td", {}, s.outlier ? h("span", { class: "down", text: "écart" }) : h("span", { class: "up", text: "concordant" }))))))));
  if (q.verify) out.push(h("h3", { text: T(`Dernière clôture (${fmtDate(q.verify.date)}) recoupée`, `Last close (${fmtDate(q.verify.date)}) cross-checked`) }), h("div", { class: "scroll" }, h("table", { class: "wrapt matrix" }, h("tbody", {},
    h("tr", {}, h("td", { text: ({ yahoo: "Yahoo Finance", stooq: "Stooq", twelvedata: "Twelve Data", tiingo: "Tiingo", polygon: "Polygon.io", alphavantage: "Alpha Vantage" }[q.price_source || q.verify.base.source] || q.price_source) + T(" (source principale)", " (main source)") }), h("td", { text: money(q.verify.base.close, d.currency) }), h("td", {}), h("td", {})),
    q.verify.others.map((o) => h("tr", {}, h("td", { text: o.label }), h("td", { text: money(o.close, d.currency) }), h("td", { class: "src", text: o.date === q.verify.date ? T("même jour", "same day") : T(`dernier jour : ${fmtDate(o.date)}`, `last day: ${fmtDate(o.date)}`) }),
      h("td", {}, o.diff_pct == null ? h("span", { class: "mut", text: "non comparable" }) : Math.abs(o.diff_pct) <= 0.3 ? h("span", { class: "up", text: T(`concordant (${fmtPct(o.diff_pct, 2, true)})`, `consistent (${fmtPct(o.diff_pct, 2, true)})`) }) : h("span", { class: "down", text: T(`écart ${fmtPct(o.diff_pct, 2, true)}`, `gap ${fmtPct(o.diff_pct, 2, true)}`) }))))))));
  if (q.crosscheck) out.push(h("h3", { text: "Fondamentaux Yahoo comparés aux comptes officiels (SEC)" }), h("div", { class: "scroll" }, h("table", { class: "wrapt" },
    h("thead", {}, h("tr", {}, h("th", { text: "Indicateur" }), h("th", { text: "Yahoo" }), h("th", { text: "SEC" }), h("th", { class: "l", text: "Verdict" }))),
    h("tbody", {}, q.crosscheck.rows.map((r) => h("tr", {}, h("td", { text: r.metric }), h("td", { text: fmtCross(r.yahoo, r.fmt) }), h("td", { text: fmtCross(r.sec, r.fmt) }),
      h("td", { class: "l" }, r.status === "ok" ? h("span", { class: "up", text: "cohérent" }) : r.status === "ecart" ? h("span", { class: "down", text: "écart" }) : h("span", { class: "mut", text: "n/d" }))))))),
    h("p", { class: "hint" }, q.crosscheck.note + " ", link(q.crosscheck.url, "Comptes officiels")));
  if ((off.filings || []).length) out.push(h("h3", { text: "Documents officiels récents (SEC EDGAR)" }), h("ul", { class: "plain" }, off.filings.map((f) => h("li", {},
    h("span", { class: "src", text: fmtDate(f.date) + " · " }), link(f.url, f.label), f.what && f.what.length ? h("span", { class: "src", text: " : " + f.what.map((w) => t(w)).join(", ") }) : null))));
  if (off.insiders && off.insiders.forms_read) out.push(h("h3", { text: "Transactions des dirigeants (formulaire 4, marché)" }),
    h("p", { class: "mut", text: T(`${off.insiders.buys} achat(s) (≈ ${fmtUsd(off.insiders.buy_value)}) et ${off.insiders.sells} vente(s) (≈ ${fmtUsd(off.insiders.sell_value)}) sur ${off.insiders.days} jours.`, `${off.insiders.buys} purchase(s) (≈ ${fmtUsd(off.insiders.buy_value)}) and ${off.insiders.sells} sale(s) (≈ ${fmtUsd(off.insiders.sell_value)}) over ${off.insiders.days} days.`) }),
    h("ul", { class: "plain" }, off.insiders.trades.slice(0, 6).map((t) => h("li", { class: "src" }, T(`${t.date} · ${t.insider}${t.role ? ` (${t.role})` : ""} : ${t.code === "P" ? "achat" : "vente"} de ${fmtNum(t.shares, 0)} actions à ${money(t.price, "USD")} · `, `${t.date} · ${t.insider}${t.role ? ` (${t.role})` : ""}: ${t.code === "P" ? "purchase" : "sale"} of ${fmtNum(t.shares, 0)} shares at ${money(t.price, "USD")} · `), link(t.url, "formulaire")))));
  if (!out.length) out.push(h("div", { class: "empty", text: "Aucune vérification disponible pour l'instant : renseigne ton email SEC et des clés de fournisseurs dans l'onglet Sources." }));
  return h("div", { class: "dq-panel" }, out);
}
function dataQualityRow(d) {
  const q = d.data_quality;
  const panel = dataPanel(d);
  panel.hidden = true;
  const btn = h("button", { class: "link mini", style: "margin-left:0", text: "Voir la vérification", "aria-expanded": "false" });
  btn.onclick = () => { panel.hidden = !panel.hidden; btn.setAttribute("aria-expanded", String(!panel.hidden)); btn.textContent = panel.hidden ? T("Voir la vérification", "Show the check") : T("Masquer", "Hide"); };
  const srcs = q.consensus ? q.consensus.n : 0;
  return h("div", { class: "dq" },
    h("div", { class: "dq-row" }, h("span", { class: "mut", text: "Fiabilité des données : " }), h("span", { class: "pill", style: `--c:${TONE_COLOR[q.tone]}` }, h("i", { style: `background:${TONE_COLOR[q.tone]}` }), q.level_label || q.level),
      h("span", { class: "src", text: [srcs ? T(`${srcs} source(s) de cours${q.consensus.live ? " en direct" : ""}`, `${srcs} price source(s)${q.consensus.live ? " (live)" : ""}`) : null, q.verify && q.verify.n_compared ? T(`clôture recoupée par ${q.verify.n_compared}`, `close cross-checked by ${q.verify.n_compared}`) : null, (d.official || {}).facts ? T("comptes SEC", "SEC filings") : null].filter(Boolean).join(" · ") }), btn),
    panel);
}

/* ---------- fenêtre d'analyse d'une entreprise ---------- */
const TONE_COLOR = { good: "var(--good)", info: "var(--hold)", neutral: "var(--axis)", warn: "var(--warn)", crit: "var(--crit)" };
const n0 = (v) => fmtNum(v, 0), n1 = (v) => fmtNum(v, 1), n2 = (v) => fmtNum(v, 2), pctF = (v) => (v == null ? "–" : pctN(v * 100, 1));
let KCUR = "USD", KFIN = "USD"; // devise de cotation / devise des comptes (peut différer pour les ADR)
const bigM = (v) => moneyBig(v, KFIN), monM = (v) => money(v, KCUR), monF = (v) => money(v, KFIN);
const KEYDATA = [["Capitalisation", "marketCap", bigM], ["Valeur d'entreprise", "enterpriseValue", bigM], ["Chiffre d'affaires (12 mois)", "totalRevenue", bigM],
  ["P/E (12 mois)", "trailingPE", n1], ["P/E prévisionnel", "forwardPE", n1], ["PEG", "trailingPegRatio", n1], ["Cours / valeur comptable", "priceToBook", n1],
  ["Cours / chiffre d'affaires", "priceToSalesTrailing12Months", n1], ["VE / EBITDA", "enterpriseToEbitda", n1], ["BPA (12 mois)", "trailingEps", monF], ["BPA prévisionnel", "forwardEps", monF],
  ["Croissance du CA", "revenueGrowth", pctF], ["Croissance des bénéfices", "earningsGrowth", pctF], ["Marge brute", "grossMargins", pctF], ["Marge opérationnelle", "operatingMargins", pctF],
  ["Marge nette", "profitMargins", pctF], ["ROE", "returnOnEquity", pctF], ["ROA", "returnOnAssets", pctF], ["Trésorerie", "totalCash", bigM], ["Dette", "totalDebt", bigM],
  ["Dette / capitaux propres (%)", "debtToEquity", n0], ["Liquidité courante", "currentRatio", n2], ["Flux de trésorerie disponible", "freeCashflow", bigM],
  ["Rendement du dividende", "trailingAnnualDividendYield", pctF], ["Taux de distribution", "payoutRatio", pctF], ["Bêta", "beta", n2], ["Plus haut 52 sem.", "fiftyTwoWeekHigh", monM],
  ["Plus bas 52 sem.", "fiftyTwoWeekLow", monM], ["Objectif moyen des analystes", "targetMeanPrice", monM], ["Nombre d'analystes", "numberOfAnalystOpinions", n0],
  ["Recommandation (1 achat, 5 vente)", "recommendationMean", n1], ["Vente à découvert (% du flottant)", "shortPercentOfFloat", pctF], ["Détenu par les initiés", "heldPercentInsiders", pctF],
  ["Détenu par les institutions", "heldPercentInstitutions", pctF], ["Employés", "fullTimeEmployees", n0]];
/* Cache des fiches : tout est préchargé à l'ouverture du site (puis toutes les heures), donc un clic affiche la fiche immédiatement. */
const CO_CACHE = new Map(), SEC_CACHE = new Map(), CANDLE_CACHE = new Map(), HOUR = 3600 * 1000;
const fresh = (c) => c && Date.now() - c.at < HOUR;
async function prefetch(paths, cache, keyOf) {
  for (const path of paths) {
    const key = keyOf(path);
    if (fresh(cache.get(key))) continue;
    try { const d = await api(path.url); if (d && d.found !== false) cache.set(key, { d, at: Date.now() }); } catch (e) { /* on réessaiera à la prochaine heure */ }
    await new Promise((r) => setTimeout(r, 400)); // un à la fois : pas de rafale de requêtes
  }
}
const prefetchCompanies = (rows) => prefetch(rows.map((r) => ({ url: "/api/company/" + encodeURIComponent(r.ticker), t: r.ticker })), CO_CACHE, (p) => p.t);
const prefetchSectors = (ids) => prefetch(ids.map((id) => ({ url: "/api/sector/" + encodeURIComponent(id), id })), SEC_CACHE, (p) => p.id);
const company = { range: "month", ro: null, ticker: null, from: null };
const TONE_ICON = { pos: ["▲", "up"], neg: ["▼", "down"], neutral: ["•", "mut"] };

async function openCompany(ticker, hintName, refresh = false, from = null) {
  const dlg = $("detail");
  company.ticker = ticker; company.from = from; sector.id = null; cmpv.ticker = null;
  $("d-title").textContent = hintName ? `${hintName} (${ticker})` : ticker;
  const hit = !refresh && CO_CACHE.get(ticker);
  if (hit) { // déjà en mémoire : affichage immédiat, sans écran de chargement
    if (!dlg.open) { dlg.showModal(); $("d-body").focus({ preventScroll: true }); }
    renderCompany(hit.d);
    if (hit.d.loading_more || Date.now() - hit.at > 10 * 60 * 1000) refreshCompanyQuietly(ticker);
    return;
  }
  $("d-body").replaceChildren(loadingView(ticker, T(`Analyse de ${hintName || ticker}`, `Analyzing ${hintName || ticker}`),
    [T("Cours et historique", "Prices and history"), T("Fiche société et chiffres clés", "Company profile and key figures"), T("Actualités et analystes", "News and analysts"),
      T("Grands investisseurs et documents SEC", "Big investors and SEC filings"), T("Calcul du verdict", "Computing the verdict")]));
  if (!dlg.open) { dlg.showModal(); $("d-body").focus({ preventScroll: true }); }
  let d;
  try { d = await api("/api/company/" + encodeURIComponent(ticker) + (refresh ? "?refresh=true" : "")); }
  catch (e) { if (company.ticker === ticker) $("d-body").replaceChildren(h("div", { class: "err", text: T("Erreur : ", "Error: ") + e.message })); return; }
  if (company.ticker !== ticker) return;
  if (d.found) CO_CACHE.set(ticker, { d, at: Date.now() });
  renderCompany(d);
  // fiche affichée dès que l'essentiel est là ; actualités, analystes et documents officiels arrivent ensuite
  if (d.loading_more) {
    let tries = 0;
    const again = async () => {
      if (company.ticker !== ticker || !$("detail").open || ++tries > 6) return;
      try { const x = await api("/api/company/" + encodeURIComponent(ticker)); if (x.found) CO_CACHE.set(ticker, { d: x, at: Date.now() }); if (company.ticker !== ticker) return; renderCompany(x); if (x.loading_more) setTimeout(again, 6000); }
      catch (e) { /* on garde la fiche affichée */ }
    };
    setTimeout(again, 5000);
  }
}
/* Met la fiche en mémoire à jour sans toucher à l'écran (sauf si de nouvelles données importantes sont arrivées et que la fiche est encore ouverte). */
async function refreshCompanyQuietly(ticker) {
  try {
    const x = await api("/api/company/" + encodeURIComponent(ticker));
    if (!x.found) return;
    CO_CACHE.set(ticker, { d: x, at: Date.now() });
    if (company.ticker === ticker && $("detail").open && !x.loading_more) { const y = $("d-body").scrollTop; renderCompany(x); $("d-body").scrollTop = y; }
  } catch (e) { /* on garde l'affichage actuel */ }
}

function sectionCard(s) {
  return h("div", { class: "card sect" },
    h("div", { class: "oh" }, h("h2", { text: s.label }), h("span", { class: "src", text: T(`poids ${s.weight} %`, `weight ${s.weight}%`) })),
    s.score == null ? h("div", { class: "empty", text: "Données indisponibles" })
      : h("div", { class: "comp2" }, h("span", { class: "t" }, h("i", { style: `width:${s.score}%;background:${s.score >= 65 ? "var(--good)" : s.score <= 40 ? "var(--crit)" : "var(--info)"}` })), h("b", { text: s.score.toFixed(0) })),
    h("ul", { class: "plain" }, s.reasons.map((r) => h("li", {}, h("span", { class: TONE_ICON[r.tone][1], text: TONE_ICON[r.tone][0] + " " }), r.text))));
}


/* ---------- fiche d'une action : l'essentiel d'abord, tout visible ---------- */
const CHART_RANGES = [["day", T("1J", "1D")], ["week", T("1S", "1W")], ["month", "1M"], ["3m", "3M"], ["6m", "6M"], ["year", T("1A", "1Y")], ["max", "MAX"]];
// état du marché (fourni par Yahoo avec la fiche société) : [libellé, classe de couleur]
// calculé par le serveur avec la courbe « 1J » (heure + derniers échanges), et non plus lu dans la fiche société,
// rechargée seulement toutes les 12 h
const MARKET_STATE = {
  REGULAR: [T("Marché ouvert", "Market open"), "up"],
  PRE: [T("Avant-Bourse", "Pre-market"), ""], POST: [T("Après-Bourse", "After hours"), ""],
  CLOSED: [T("Marché fermé", "Market closed"), ""],
};
const PHASE_NAME = { pre: T("avant-Bourse", "pre-market"), post: T("après-Bourse", "after hours") };
// courbes de la fiche, partagées entre la courbe et le cours en tête de fiche (une seule requête à la fois par période)
const CANDLE_WAIT = new Map();
async function getCandles(t, range) {
  const key = t + ":" + range, hit = CANDLE_CACHE.get(key);
  if (hit && Date.now() - hit.at < CANDLE_TTL[range] * 60 * 1000) return hit.d;
  if (!CANDLE_WAIT.has(key)) {
    CANDLE_WAIT.set(key, api(`/api/company/${encodeURIComponent(t)}/candles?range=${range}`)
      .then((x) => { if (x && x.found) CANDLE_CACHE.set(key, { d: x, at: Date.now() }); return x; })
      .finally(() => CANDLE_WAIT.delete(key)));
  }
  return CANDLE_WAIT.get(key);
}
function periodStats(dates, px) {
  const pts = px.map((v, i) => [dates[i], v]).filter(([, v]) => v != null);
  if (pts.length < 2) return null;
  const first = pts[0][1], last = pts[pts.length - 1][1], vals = pts.map((x) => x[1]);
  const hi = Math.max(...vals), lo = Math.min(...vals);
  const days = (new Date(pts[pts.length - 1][0]) - new Date(pts[0][0])) / 86400000;
  const rets = vals.slice(1).map((v, i) => v / vals[i] - 1), mean = rets.reduce((x, y) => x + y, 0) / rets.length;
  const vol = Math.sqrt(rets.reduce((x, y) => x + (y - mean) ** 2, 0) / rets.length) * Math.sqrt(252);
  return { change: (last / first - 1) * 100, perYear: days >= 330 ? (Math.pow(last / first, 365 / days) - 1) * 100 : null,
    hi, lo, hiDate: pts[vals.indexOf(hi)][0], loDate: pts[vals.indexOf(lo)][0], fromHigh: (last / hi - 1) * 100, vol: vol * 100, days };
}
function yearlyReturns(dates, px) {
  const byYear = {};
  dates.forEach((d, i) => { if (px[i] != null) (byYear[d.slice(0, 4)] = byYear[d.slice(0, 4)] || []).push(px[i]); });
  const years = Object.keys(byYear).sort().slice(-5), out = [];
  years.forEach((y) => {
    const prev = byYear[String(+y - 1)], base = prev ? prev[prev.length - 1] : byYear[y][0], end = byYear[y][byYear[y].length - 1];
    if (base) out.push({ year: y, pct: (end / base - 1) * 100, partial: y === String(new Date().getFullYear()) });
  });
  return out;
}
function statTile(label, value, sub, cls) { return h("div", { class: "st" }, h("span", { class: "mut", text: label }), h("b", { class: cls || "", text: value }), sub ? h("span", { class: "src", text: sub }) : null); }

function renderCompany(d) {
  if (company.ro) company.ro.disconnect();
  company.ro = null;
  const t = d.ticker;
  if (!d.found) {
    $("d-body").replaceChildren(h("div", { class: "card" }, h("h2", { text: T(`Aucune donnée pour « ${t} »`, `No data for "${t}"`) }),
      h("p", { class: "mut", text: "Symbole inconnu, retiré de la cote, ou source de données indisponible. Essaie avec la recherche par nom, ou consulte l'onglet Sources." }),
      d.errors.length ? h("ul", { class: "plain" }, d.errors.map((e) => h("li", { text: e }))) : null));
    return;
  }
  $("d-title").textContent = `${d.name} (${t})`;
  const a = d.assessment, r = d.rating, opp = d.opportunity, ev = d.events || {}, info = d.info || {};
  KCUR = d.currency || "USD"; KFIN = info.financialCurrency || KCUR;
  if (!CHART_RANGES.some(([v]) => v === company.range)) company.range = "month";
  const body = [];

  /* --- identité et actions --- */
  const followBtn = h("button", { class: "btn mini" }, d.in_watchlist ? (d.removable ? "Ne plus suivre" : "Dans ta watchlist ✓") : "Suivre");
  if (!d.in_watchlist) followBtn.onclick = async () => { followBtn.disabled = true; await api(`/api/watchlist/${encodeURIComponent(t)}?name=${encodeURIComponent(d.name)}`, { method: "POST" }); followChanged(t); followBtn.textContent = T("Suivi ✓", "Following ✓"); };
  else if (d.removable) followBtn.onclick = async () => { followBtn.disabled = true; await api(`/api/watchlist/${encodeURIComponent(t)}`, { method: "DELETE" }); followChanged(t); followBtn.textContent = T("Retiré", "Removed"); };
  else followBtn.disabled = true;
  const back = company.from;
  // « Actualiser » : la fiche reste affichée. Le cours et la courbe sont remis à jour tout de suite (une seule
  // requête rapide) ; le reste (historique, fiche société, actualités, analystes, SEC) est rechargé en parallèle,
  // morceau par morceau, puis la fiche est redessinée. Avant, tout était rechargé source après source derrière un
  // écran de chargement, ce qui pouvait prendre une minute.
  const refreshBtn = h("button", { class: "btn mini", text: T("Actualiser", "Refresh") });
  refreshBtn.onclick = async () => {
    refreshBtn.disabled = true; refreshBtn.textContent = T("Actualisation…", "Refreshing…");
    const enc = encodeURIComponent(t);
    for (const k of [...CANDLE_CACHE.keys()]) if (k.startsWith(t + ":")) CANDLE_CACHE.delete(k);
    const parts = ["prices", "fund", "news", "events", "sec"].map((p) => api(`/api/company/${enc}/refresh?part=${p}`, { method: "POST" }).catch(() => null));
    try {
      const x = await api(`/api/company/${enc}/candles?range=day&fresh=true`);
      if (x && x.found) { CANDLE_CACHE.set(t + ":day", { d: x, at: Date.now() }); if (company.ticker === t) { paintHead(x); draw(false); } }
    } catch (e) { /* le reste continue */ }
    await Promise.all(parts);
    if (company.ticker !== t || !$("detail").open) return;
    try {
      const x = await api("/api/company/" + enc);
      if (x.found) CO_CACHE.set(t, { d: x, at: Date.now() });
      if (company.ticker !== t) return;
      const y = $("d-body").scrollTop; renderCompany(x); $("d-body").scrollTop = y;
    } catch (e) { refreshBtn.disabled = false; refreshBtn.textContent = T("Actualiser", "Refresh"); }
  };
  const cmpBtn = h("button", { class: "btn mini primary", text: "Comparer", title: "Compare cette action avec les meilleures actions du moment", onclick: () => openCompareView(t, d.name, () => openCompany(t, d.name, false, back)) });
  body.push(h("div", { class: "card-head" }, h("div", {}, logoImg(t, d.domain, 22), back ? h("button", { class: "btn mini", style: "margin-right:10px", text: "← Retour", onclick: back }) : null, h("span", { class: "mut", text: [d.exchange, d.sector, d.industry].filter(Boolean).join(" · ") })),
    h("div", { style: "display:flex;gap:8px;flex-wrap:wrap" }, followBtn, cmpBtn, refreshBtn)));

  /* --- 1. l'indice : le niveau, le cours, une phrase, trois raisons --- */
  // Le cours affiché d'abord est celui enregistré ; il est remplacé dès que la courbe du jour arrive par le dernier
  // échange connu (avant- et après-Bourse compris), avec sa variation depuis la dernière clôture officielle.
  const priceHost = h("span", { class: "vprice" }, h("b", { text: money(d.price, d.currency) }), " ", d.change_1d == null ? null : delta(d.change_1d));
  const paintHead = (x) => {
    if (!x || !x.found || x.last_native == null || company.ticker !== t || !priceHost.isConnected) return;
    // même valeur que la fin de la courbe (déjà convertie dans la devise choisie), variation calculée dans la devise de cotation
    const lastConv = [...x.close].reverse().find((v) => v != null);
    const pc = x.prev_close_native, chg = pc ? (x.last_native / pc - 1) * 100 : null, ms = MARKET_STATE[x.state];
    const lastAt = x.dates.length ? new Date(x.dates[x.dates.length - 1]) : null;
    let sub = ms ? ms[0] : "";
    if ((x.state === "PRE" || x.state === "POST") && lastAt) sub += " · " + lastAt.toLocaleTimeString(LOC, { hour: "2-digit", minute: "2-digit" });
    if (x.state === "CLOSED" && x.session) sub += " · " + T("cours du ", "price of ") + new Date(x.session + "T12:00:00").toLocaleDateString(LOC, { weekday: "short", day: "numeric", month: "short" });
    priceHost.replaceChildren(...[h("b", { text: lastConv != null ? money(lastConv, x.currency) : money(x.last_native, d.currency) }), " ", chg == null ? null : delta(chg),
      sub ? h("div", { class: ("src " + (ms ? ms[1] : "")).trim(), text: sub }) : null].filter((v) => v != null));
  };
  getCandles(t, "day").then(paintHead).catch(() => { /* le cours enregistré reste affiché */ });
  const summary = h("div", { class: "verdict", style: `border-left-color:${r ? LABEL_COLOR[r.label] : "var(--axis)"}` },
    h("div", { class: "vhead" },
      r ? h("span", { class: "pill big" }, h("i", { style: `background:${LABEL_COLOR[r.label]}` }), r.label) : null,
      r ? h("span", { class: "vscore" }, h("b", { text: r.score.toFixed(0) }), " / 100") : null,
      priceHost),
    r ? [h("p", { class: "vsum", text: r.headline }),
      h("ul", { class: "plain" }, r.points.slice(0, 3).map((x) => h("li", {}, h("span", { class: TONE_ICON[x.tone][1], text: TONE_ICON[x.tone][0] + " " }), x.text)))]
      : h("p", { class: "vsum", text: "Historique de cours insuffisant pour attribuer un niveau (BUY, HOLD…)." }));
  const facts = [];
  if (info.targetMeanPrice && d.price) { const up = (info.targetMeanPrice / d.price - 1) * 100; facts.push([T("Objectif des analystes", "Analyst target"), money(info.targetMeanPrice, d.currency), fmtPct(up, 0, true)]); }
  const nx = (ev.earnings_dates || []).find((x) => new Date(x) >= new Date(new Date().toDateString()));
  if (nx) facts.push([T("Prochains résultats", "Next earnings"), fmtDate(nx)]);
  if (a) facts.push([T("Avis global", "Overall view"), a.verdict.label]);
  if (facts.length) summary.append(h("div", { class: "kf" }, facts.slice(0, 3).map(([l, v, s2]) => h("div", {}, h("span", { class: "mut", text: l }), h("b", { text: v }), s2 ? h("span", { class: "src", text: s2 }) : null))));
  body.push(summary);

  /* --- mouvement notable : les actualités récentes qui pourraient expliquer le cours du jour --- */
  if (d.movement && d.movement.items.length) {
    const m = d.movement;
    body.push(h("div", { class: "card sec2 movement" },
      h("div", { class: "mv-head" },
        h("span", { class: "mv-tag " + (m.direction === "up" ? "up" : "down") }, m.direction === "up" ? "▲ " : "▼ ", T("Mouvement notable", "Notable move")),
        !m.matched ? h("span", { class: "mut", text: T("actualités récentes, lien non confirmé", "recent news, link not confirmed") }) : null),
      h("ul", { class: "news" }, m.items.map((n) => h("li", {},
        n.sentiment ? h("b", { class: n.sentiment > 0 ? "up" : "down", text: n.sentiment > 0 ? "▲ " : "▼ " }) : null,
        link(n.url, n.title), h("div", { class: "src", text: `${tr(n.source)} · ${fmtDate(n.published)}` }))))));
  }

  /* --- 2. évolution du cours : courbe simple (le zoom a été retiré, il posait des soucis). Données via /candles,
     calculé à la demande : sur « 1J »/« 1S » ça donne un vrai suivi heure par heure (avant, cette courbe découpait
     les seules clôtures quotidiennes du score, ce qui ne donnait que 2 à 5 points). */
  const chartHost = h("div", { class: "chart cm-chart" }), rangeHost = h("div", { class: "ranges", role: "group", "aria-label": "Période" }), statsHost = h("div", { class: "sts" }), hintHost = h("p", { class: "hint" });
  const draw = async (anim) => {
    const range = company.range, hit = CANDLE_CACHE.get(t + ":" + range);
    if (!(hit && Date.now() - hit.at < CANDLE_TTL[range] * 60 * 1000)) chartHost.replaceChildren(h("div", { class: "empty", text: T("Chargement…", "Loading…") }));
    let x;
    try { x = await getCandles(t, range); }
    catch (e) { if (company.ticker === t && company.range === range) chartHost.replaceChildren(h("div", { class: "err", text: T("Erreur : ", "Error: ") + e.message })); return; }
    if (company.ticker !== t || company.range !== range) return; // la période (ou la fiche) a changé pendant le chargement
    if (!x.found) { chartHost.replaceChildren(h("div", { class: "empty", text: T("Données indisponibles pour le moment (source bloquée).", "Data unavailable right now (source blocked).") })); return; }
    if (range === "day") paintHead(x);
    if (company.ticker !== t || company.range !== range) return;
    const st = periodStats(x.dates, x.close), lab = (CHART_RANGES.find(([v]) => v === range) || [""])[1];
    // 1J : la variation du jour se mesure contre la clôture de la veille (comme dans les applis de bourse), pas contre
    // le premier point de la séance
    const last = [...x.close].reverse().find((v) => v != null);
    const vsPrev = x.prev_close && last != null ? (last / x.prev_close - 1) * 100 : null;
    const when = x.intraday ? (iso) => new Date(iso).toLocaleString(LOC, { weekday: "short", hour: "2-digit", minute: "2-digit" }) : fmtDate;
    if (!st) statsHost.replaceChildren(h("div", { class: "empty", text: T("Pas assez de données sur cette période.", "Not enough data for this period.") }));
    else {
      const change = vsPrev != null ? vsPrev : st.change, volWord = st.vol < 25 ? T("faible", "low") : st.vol < 45 ? T("moyenne", "medium") : T("élevée", "high");
      statsHost.replaceChildren(...[
        statTile(T(`Évolution sur ${lab}`, `Change over ${lab}`), fmtPct(change, 2, true), vsPrev != null ? T("depuis la clôture de la veille", "since the previous close") : null, change >= 0 ? "up" : "down"),
        st.perYear != null ? statTile(T("En moyenne par an", "Average per year"), fmtPct(st.perYear, 1, true), null, st.perYear >= 0 ? "up" : "down") : null,
        statTile(T("Plus haut", "High"), money(st.hi, x.currency), when(st.hiDate)),
        statTile(T("Plus bas", "Low"), money(st.lo, x.currency), when(st.loDate)),
        statTile(T("Écart avec le plus haut", "From the high"), fmtPct(st.fromHigh, 1, true), null, st.fromHigh >= -0.5 ? "up" : ""),
        // volatilité annualisée : n'a de sens que sur des cours quotidiens, pas sur des points de 5 minutes ou d'une heure
        !x.intraday ? statTile(T("Variations du cours", "Price swings"), volWord, T(`${fmtNum(st.vol, 0)} % par an`, `${fmtNum(st.vol, 0)}% per year`)) : null].filter(Boolean));
    }
    const up = vsPrev != null ? vsPrev >= 0 : (last || 0) >= (x.close.find((v) => v != null) || 0);
    const ref = x.prev_close ? { value: x.prev_close, label: T("Clôture veille", "Previous close"), short: T("vs clôture veille", "vs previous close") } : null;
    const bands = [], notes = x.phase ? x.phase.map((p) => PHASE_NAME[p] || null) : null;
    if (x.phase) x.phase.forEach((p, i) => { if (p === "reg") return; const b = bands[bands.length - 1]; if (b && b.p === p && b.to === i - 1) b.to = i; else bands.push({ p, from: i, to: i, label: MARKET_STATE[p === "pre" ? "PRE" : "POST"][0] }); });
    lineChart(chartHost, { dates: x.dates, series: [{ name: T("Cours", "Price"), color: up ? "var(--up)" : "var(--down)", area: true, width: 2.4, values: x.close }], height: 300, animate: anim, intraday: x.intraday, ref, bands, notes });
    const session = x.session ? new Date(x.session + "T12:00:00").toLocaleDateString(LOC, { weekday: "long", day: "numeric", month: "long" }) : null;
    hintHost.textContent = [session ? T(`Séance du ${session}, heures affichées à ton heure locale.`, `Session of ${session}, times shown in your local time.`) : null,
      bands.length ? T("Zones grisées : échanges avant et après la séance officielle.", "Shaded areas: trading before and after the regular session.") : null,
      x.currency ? T(`Cours en ${x.currency}. Survole (ou touche) la courbe pour voir le prix exact.`, `Prices in ${x.currency}. Hover (or touch) the chart for the exact price.`) : null].filter(Boolean).join(" ");
  };
  const drawPeriods = () => rangeHost.replaceChildren(...CHART_RANGES.map(([v, l]) => h("button", { "aria-pressed": String(company.range === v), onclick: () => { company.range = v; drawPeriods(); draw(true); } }, l)));
  drawPeriods();
  body.push(h("div", { class: "card sec2" }, h("div", { class: "card-head" }, h("h2", { text: T("Évolution du cours", "Price history") }), rangeHost),
    chartHost, statsHost, hintHost));

  /* --- 3. progression année par année --- */
  const yrs = yearlyReturns(d.chart.dates, d.chart.close);
  if (yrs.length >= 2) {
    const mx = Math.max(...yrs.map((y) => Math.abs(y.pct)), 1);
    body.push(h("div", { class: "card sec2" }, h("h2", { text: T("Progression année par année", "Year by year") }),
      h("div", { class: "yrs" }, yrs.map((y) => h("div", { class: "yr" }, h("span", { class: "y", text: y.year + (y.partial ? T(" (en cours)", " (YTD)") : "") }),
        h("span", { class: "bar" }, h("i", { class: y.pct >= 0 ? "pos" : "neg", style: `width:${Math.max(2, (Math.abs(y.pct) / mx) * 100)}%` })), h("b", { class: y.pct >= 0 ? "up" : "down", text: fmtPct(y.pct, 1, true) })))),
      h("p", { class: "hint", text: T("Évolution du cours sur chaque année civile.", "Price change over each calendar year.") })));
  }

  /* --- 4. chiffres clés en langage simple --- */
  const kt = [["marketCap", T("Taille de l'entreprise", "Company size"), bigM, T("valeur totale en bourse", "total stock market value")],
    ["revenueGrowth", T("Croissance des ventes", "Sales growth"), pctF, T("par rapport à l'an dernier", "vs last year")],
    ["earningsGrowth", T("Croissance des bénéfices", "Profit growth"), pctF, T("par rapport à l'an dernier", "vs last year")],
    ["profitMargins", T("Marge nette", "Net margin"), pctF, T("bénéfice sur 100 de ventes", "profit per 100 of sales")],
    [info.forwardPE ? "forwardPE" : "trailingPE", T("Prix / bénéfices (P/E)", "Price / earnings (P/E)"), n1, T("années de bénéfices dans le prix", "years of profit in the price")],
    ["trailingAnnualDividendYield", T("Dividende", "Dividend"), pctF, T("versé chaque année, en % du prix", "paid yearly, % of price")]]
    .filter(([k]) => info[k] != null).slice(0, 6);
  if (kt.length) body.push(h("div", { class: "card sec2" }, h("h2", { text: T("Chiffres clés", "Key figures") }),
    h("div", { class: "sts" }, kt.map(([k, l, f, sub]) => statTile(l, f(info[k]), sub)))));

  /* --- 5. actualités --- */
  const newsList = d.news.length ? h("ul", { class: "news" }, d.news.slice(0, 5).map((n) => h("li", {},
    n.sentiment ? h("b", { class: n.sentiment > 0 ? "up" : "down", text: n.sentiment > 0 ? "▲ " : "▼ " }) : null, link(n.url, n.title), h("div", { class: "src", text: `${tr(n.source)} · ${fmtDate(n.published)}` }))))
    : h("div", { class: "empty", text: d.loading_more ? T("Chargement des actualités…", "Loading news…") : "Aucune actualité collectée." });
  body.push(h("div", { class: "card sec2" }, h("h2", { text: T("Dernières actualités", "Latest news") }), newsList));

  /* --- 6. analystes et grands investisseurs --- */
  const who = [];
  if (ev.target) who.push(h("p", { class: "vsum2" }, T(`Les analystes visent en moyenne ${money(ev.target.mean, d.currency)} (entre ${money(ev.target.low, d.currency)} et ${money(ev.target.high, d.currency)}).`, `Analysts target ${money(ev.target.mean, d.currency)} on average (from ${money(ev.target.low, d.currency)} to ${money(ev.target.high, d.currency)}).`)));
  (ev.analyst || []).slice(0, 3).forEach((x) => who.push(h("div", { class: "src", text: `${fmtDate(x.date)} · ${x.firm}${T(" : ", ": ")}${x.from ? x.from + " → " : ""}${x.to} (${x.action})` })));
  if (d.holders.length) who.push(h("h3", { text: T("Grands investisseurs", "Big investors") }), d.holders.slice(0, 3).map((x) => h("div", { class: "chg" },
    h("div", {}, h("b", { text: x.investor.split(" (")[0] }), h("span", { class: "tag", text: `${KIND_INV[x.kind]} · ${x.period_label}` })), h("p", { class: "why", text: x.why }))));
  if (who.length) body.push(h("div", { class: "card sec2" }, h("h2", { text: T("Analystes et grands investisseurs", "Analysts and big investors") }), who));

  /* --- 7. le reste, pour les plus curieux (un seul menu) --- */
  const more = [];
  if (d.data_quality) more.push(dataQualityRow(d));
  if (a && (a.strengths.length || a.weaknesses.length)) {
    const col = (title, items, cls, icon) => h("div", {}, h("h3", { text: title }), items.length
      ? h("ul", { class: "plain" }, items.slice(0, 3).map((x) => h("li", {}, h("span", { class: cls, text: icon + " " }), x.text))) : h("div", { class: "empty", text: "Aucun point marqué." }));
    more.push(h("div", { class: "cols" }, col("Points forts", a.strengths, "up", "▲"), col("Points faibles", a.weaknesses, "down", "▼")));
  }
  if (a) more.push(h("h3", { text: "Analyse détaillée par thème" }), h("div", { class: "grid2" }, a.sections.map(sectionCard)));
  if (opp && opp.signals.length) more.push(h("h3", { text: "Signaux d'opportunité" }), h("ul", { class: "sig" }, opp.signals.map(signalItem)));
  if (d.institutions.length) more.push(h("h3", { text: "Principaux actionnaires" }), h("div", { class: "scroll" }, h("table", { class: "matrix" }, h("thead", {}, h("tr", {}, h("th", { text: "Institution" }), h("th", { text: "% du capital" }), h("th", { text: "Variation" }))),
    h("tbody", {}, d.institutions.map((x) => h("tr", {}, h("td", { text: x.holder }), h("td", { text: x.pct == null ? "–" : pctN(x.pct * 100, 2) }), h("td", {}, x.change == null ? "–" : delta(x.change * 100))))))));
  if (d.insiders && d.insiders.recent.length) more.push(h("h3", { text: "Dirigeants" }), h("p", { class: "mut", text: T(`${d.insiders.buys} achat(s), ${d.insiders.sells} vente(s) sur les ${d.insiders.recent.length} dernières opérations.`, `${d.insiders.buys} purchase(s), ${d.insiders.sells} sale(s) over the last ${d.insiders.recent.length} transactions.`) }),
    h("ul", { class: "plain" }, d.insiders.recent.slice(0, 5).map((x) => h("li", { class: "src", text: `${x.date} · ${x.insider} (${x.position})${T(" : ", ": ")}${x.text || T("opération", "transaction")}${x.value ? ` · ≈ ${fmtUsd(x.value)}` : ""}` }))));
  const kd = KEYDATA.filter(([, k]) => info[k] != null);
  if (kd.length) more.push(h("h3", { text: "Toutes les données clés" }), h("dl", { class: "kd" }, kd.map(([l, k, f]) => h("div", {}, h("dt", { text: l }), h("dd", { text: f(info[k]) })))));
  if (info.longBusinessSummary) more.push(h("h3", { text: "À propos" }), h("p", { class: "why", text: info.longBusinessSummary + T(" (description Yahoo, en anglais)", " (Yahoo description)") }));
  if (safeUrl(info.website)) more.push(h("p", { class: "hint" }, "Site officiel : ", link(info.website, info.website)));
  if (more.length) body.push(fold(T("Plus de détails", "More details"), T("fiabilité, points forts et faibles, actionnaires, chiffres complets", "reliability, strengths and weaknesses, shareholders, full figures"), more));

  if (d.errors.length || d.problems.length) body.push(h("p", { class: "hint" }, "Certaines données n'ont pas pu être récupérées. ",
    h("button", { class: "link", onclick: () => { $("detail").close(); showTab("sources"); }, text: "Voir l'onglet Sources" })));
  body.push(h("p", { class: "note", text: "Analyse indicative fondée sur des seuils génériques ; les fondamentaux viennent de Yahoo Finance et peuvent contenir des erreurs. Ce n'est pas un conseil en investissement." }));
  $("d-body").replaceChildren(...body.flat()); enter($("d-body"));
  draw(true);
  company.ro = autoRedraw(chartHost, () => draw(false)); // redessine à la nouvelle largeur ; la donnée est déjà en cache, pas de nouvel appel réseau
}
$("d-close").onclick = closeDlg;
$("detail").addEventListener("cancel", (e) => { e.preventDefault(); closeDlg(); });
$("detail").addEventListener("click", (e) => { if (e.target === $("detail")) closeDlg(); });
/* Sur mobile, le bouton « retour » ferme la fiche ouverte au lieu de quitter le site. */
let dlgPushed = false, popClosing = false;
new MutationObserver(() => { if ($("detail").open && !dlgPushed) { history.pushState({ dlg: 1 }, "", location.href); dlgPushed = true; } }).observe($("detail"), { attributes: true, attributeFilter: ["open"] });
addEventListener("popstate", () => { if ($("detail").open) { popClosing = true; closeDlg(); } });
$("detail").addEventListener("close", () => {
  if (dlgPushed) { dlgPushed = false; if (!popClosing) history.back(); }
  popClosing = false;
  if (dirtyFollow) { dirtyFollow = false; refreshTab(true).then(() => { if (typeof prefetchAll === "function") prefetchAll(); }); }
  if (company.ro) company.ro.disconnect(); if (cmpv.ro) cmpv.ro.disconnect(); company.ticker = null; sector.id = null; cmpv.ticker = null; });

// minutes de cache côté client : court pour toutes les périodes, pour qu'elles se terminent toutes sur le même cours
// actuel (le serveur garde lui-même l'historique en cache, seul le dernier point est recalculé)
const CANDLE_TTL = { day: 1, week: 1, month: 1, "3m": 1, "6m": 1, year: 1, max: 1 };

/* ---------- comparaison avec les meilleures actions du moment ---------- */
const cmpv = { ticker: null, range: 126, chosen: null, ro: null, slots: {} };
function cmpSlot(t, base) {
  if (base) return 1;
  if (!cmpv.slots[t]) { const used = new Set(Object.values(cmpv.slots)); for (let i = 2; i <= 8; i++) if (!used.has(i)) { cmpv.slots[t] = i; break; } }
  return cmpv.slots[t] || 8;
}
async function openCompareView(ticker, name, back, chosen) {
  const dlg = $("detail");
  cmpv.ticker = ticker; company.ticker = null; sector.id = null;
  if (!chosen) { cmpv.slots = {}; cmpv.chosen = null; }
  $("d-title").textContent = T(`Comparer ${name || ticker}`, `Compare ${name || ticker}`);
  $("d-body").replaceChildren(loadingView(ticker, T("Comparaison avec les meilleures actions du moment", "Comparing with today's best stocks"),
    [T("Meilleures actions du moment", "Today's best stocks"), T("Historique des cours", "Price history"), T("Indicateurs côte à côte", "Side-by-side indicators")]));
  if (!dlg.open) { dlg.showModal(); $("d-body").focus({ preventScroll: true }); }
  let d;
  try { d = await api(`/api/compare-best/${encodeURIComponent(ticker)}` + (chosen ? `?with_=${encodeURIComponent(chosen.join(","))}` : "")); }
  catch (e) { if (cmpv.ticker === ticker) $("d-body").replaceChildren(h("div", { class: "err", text: T("Erreur : ", "Error: ") + e.message })); return; }
  if (cmpv.ticker !== ticker) return;
  renderCompareView(d, name, back);
}
function renderCompareView(d, name, back) {
  if (cmpv.ro) cmpv.ro.disconnect();
  cmpv.ro = null;
  const t = d.ticker, label = d.name || name || t;
  if (!d.found) { $("d-body").replaceChildren(h("div", { class: "empty", text: "Pas assez de données de cours pour comparer cette action." })); return; }
  cmpv.chosen = d.chosen;
  const body = [h("div", { class: "card-head" }, h("button", { class: "btn mini", text: T(`← Retour à ${label}`, `← Back to ${label}`), onclick: back }), null)];
  body.push(h("div", { class: "verdict" }, h("h3", { class: "compare-title", text: T(`${label} face aux meilleures actions du moment`, `${label} vs. today's best stocks`) }),
    h("p", { class: "vsum", style: "margin:0", text: d.summary || "Pas assez de données pour résumer la comparaison." })));
  body.push(h("h3", { text: "Actions comparées (clique pour changer)" }), h("div", { class: "chips" }, d.candidates.map((c) => {
    const on = d.chosen.includes(c.ticker);
    return h("button", { class: "chip", "aria-pressed": on ? "true" : "false", title: `${c.name || c.ticker} · ${c.sector}`,
      onclick: () => { const cur = new Set(d.chosen); if (cur.has(c.ticker)) { if (cur.size > 1) cur.delete(c.ticker); } else if (cur.size < 5) cur.add(c.ticker); openCompareView(t, label, back, [...cur]); } },
      h("span", { class: "sw", style: on ? `background:var(--s${cmpSlot(c.ticker, false)})` : "" }), c.ticker, h("span", { class: "chip-l", text: c.label }));
  })));
  const chartHost = h("div", { class: "chart" }), rangeHost = h("div", { class: "ranges", role: "group", "aria-label": "Période" });
  const draw = () => {
    const n = cmpv.range, dates = d.dates.slice(-n);
    const series = Object.keys(d.series).map((k) => {
      const v = d.series[k].slice(-n), base = v.find((x) => x != null);
      return { name: k, slot: cmpSlot(k, k === t), values: v.map((x) => (x == null || !base ? null : (x / base) * 100)) };
    });
    lineChart(chartHost, { dates, series, height: 320, yfmt: (v) => fmtNum(v, 0) });
  };
  rangeButtons(rangeHost, () => cmpv.range, (n) => { cmpv.range = n; draw(); });
  body.push(h("div", { class: "card-head" }, h("h3", { style: "margin:0", text: "Performance (base 100)" }), rangeHost), chartHost,
    h("p", { class: "hint", text: "Base 100 au début de la période : on compare les performances, pas les prix." }));
  const pc = (v) => (v == null ? "–" : delta(v * 100));
  body.push(h("h3", { text: "Chiffres côte à côte" }), h("div", { class: "card flush" }, h("div", { class: "scroll" }, h("table", { class: "wrapt cmpt" },
    h("thead", {}, h("tr", {}, h("th", { text: "Action" }), h("th", { class: "l", text: "Niveau" }), h("th", { text: "3 mois" }), h("th", { text: "6 mois" }),
      h("th", { title: "Ampleur des variations du cours sur la période : plus c'est élevé, plus le cours bouge fort (à la hausse comme à la baisse).", text: "Volatilité" }),
      h("th", { text: "vs plus haut" }),
      h("th", { title: "Indicateur technique (0 à 100) : au-dessus de 70, l'action est jugée « chère » à court terme ; en dessous de 30, « survendue ».", text: "RSI" }))),
    h("tbody", {}, d.rows.map((r) => h("tr", { class: r.base ? "base" : "" },
      h("td", {}, h("span", { class: "sw2", style: `background:var(--s${cmpSlot(r.ticker, r.base)})` }), h("button", { class: "link", onclick: () => openCompany(r.ticker, r.name, false, () => openCompareView(t, label, back, cmpv.chosen)) }, r.ticker), r.base ? h("span", { class: "tag", text: "ton choix" }) : null, h("span", { class: "nm", text: r.name || "" })),
      h("td", { class: "l" }, r.label ? pill(r.label, LABEL_COLOR[r.label], `${r.label === "AVOID/REASSESS" ? "AVOID" : r.label} · ${r.score.toFixed(0)}`) : "–"),
      h("td", {}, pc(r.r3m)), h("td", {}, pc(r.r6m)), h("td", { text: r.vol == null ? "–" : fmtPct(r.vol * 100, 0) }), h("td", {}, pc(r.dd)),
      h("td", { text: r.rsi == null ? "–" : r.rsi.toFixed(0) }))))))));
  body.push(h("p", { class: "note", text: "Comparaison fondée sur les cours uniquement. Les « meilleures actions du moment » sont les mieux classées des meilleurs secteurs. Ce n'est pas un conseil en investissement." }));
  $("d-body").replaceChildren(...body.flat()); enter($("d-body"));
  draw();
  cmpv.ro = autoRedraw(chartHost, draw);
}

/* ---------- onglets, rafraîchissement ---------- */
const TABS = ["overview", "market", "sources", "profile"];
function showTab(name) {
  activeTab = name;
  for (const b of document.querySelectorAll(".tabs button")) b.setAttribute("aria-selected", b.dataset.tab === name ? "true" : "false");
  const prev = TABS.find((id) => !$(id).hidden);
  for (const id of TABS) $(id).hidden = id !== name;
  const panel = $(name);
  if (!panel.children.length) panel.replaceChildren(...skeleton());
  if (prev && prev !== name && panel.animate && !REDUCED) panel.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 220, easing: "ease-out" });
  moveTabLine();
  history.replaceState(null, "", "#" + name);
  const cur = document.querySelector(`.tabs button[data-tab="${name}"]`); if (cur && cur.scrollIntoView) cur.scrollIntoView({ inline: "center", block: "nearest" });
  refreshTab(true).then(() => { enter(panel); if (name === "overview") pollLive(); });
}
async function refreshTab(force) {
  try {
    const old = Date.now() - 60000;
    if (activeTab === "overview") renderOverview(await api("/api/dashboard"));
    else {
      if (activeTab === "market" && (force || loaded.market < old)) await renderMarket();
      else if (activeTab === "sources" && (force || loaded.sources < old)) await renderSources();
      else if (activeTab === "profile" && force) await renderProfile();
      const d = await api("/api/dashboard");
      dash = d; renderStatus(d.status, d.interval_min);
    }
  } catch (e) { $("status").textContent = T("Serveur injoignable…", "Server unreachable…"); }
}
for (const b of document.querySelectorAll(".tabs button")) b.onclick = () => showTab(b.dataset.tab);
$("me").onclick = () => showTab("profile");
{ // en-tête toujours visible ; bouton « retour en haut » après un long défilement
  const top = $("topwrap"), up = $("totop");
  const onScroll = () => { top.classList.toggle("scrolled", scrollY > 4); up.hidden = scrollY < 700; };
  addEventListener("scroll", onScroll, { passive: true }); onScroll();
  up.onclick = () => scrollTo({ top: 0, behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
}
$("refresh").onclick = async () => { await fetch("/api/refresh", { method: "POST" }); setTimeout(() => refreshTab(true), 800); };
const start = location.hash.slice(1);
let ME = { admin: false };
loadRates().then(() => api("/api/me")).then((m) => { ME = m; const n = (m.profile && m.profile.display_name) || m.user || "?"; $("me-i").textContent = n[0].toUpperCase(); }).catch(() => {})
  .finally(() => showTab(TABS.includes(start) ? start : "overview"));
// Actions long terme : affichage rafraîchi et fiches préchargées toutes les heures.
let prefetching = false;
async function prefetchAll() {
  if (prefetching) return;
  prefetching = true;
  try {
    if (!dash) dash = await api("/api/dashboard");
    await prefetchCompanies(dash.rows || []);
    const s2 = await api("/api/sectors");
    if (s2.available) {
      await prefetchSectors(s2.sectors.slice(0, 5).map((x) => x.id));
      // les 3 meilleures de chaque secteur affiché en aperçu : visibles (et cliquables) dès l'onglet Marché ouvert
      await prefetchCompanies(s2.sectors.slice(0, 5).flatMap((x) => (x.best || []).slice(0, 3)));
    }
    const od = await api("/api/opportunities");
    await prefetchCompanies(od.items || []);
  } catch (e) { /* silencieux */ } finally { prefetching = false; }
}
setTimeout(prefetchAll, 1500);
setInterval(prefetchAll, HOUR);
setInterval(() => { if (!$("detail").open) refreshTab(false); }, HOUR);
setTimeout(pollLive, 2500);
setInterval(pollLive, 5 * 60 * 1000); // toutes les 5 minutes (le serveur ne retélécharge pas plus d'une fois toutes les 3 minutes)
document.addEventListener("visibilitychange", () => { if (!document.hidden) pollLive(); });
