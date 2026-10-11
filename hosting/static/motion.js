"use strict";
/* Animations : entrées en cascade, trait glissant des onglets, ouverture/fermeture des fenêtres, écran de chargement.
   Tout est désactivé si l'utilisateur demande moins d'animations (prefers-reduced-motion). */
const REDUCED = matchMedia("(prefers-reduced-motion: reduce)").matches;
const EASE = "cubic-bezier(.2,.7,.2,1)";

/* Fait apparaître les blocs de premier niveau d'une page, en cascade (Web Animations : aucune classe résiduelle). */
function enter(root, sel = ".ri,.card,.sec,.fold,.gh,.stat,.prov,.fund,.verdict,.empty,.banner,.sk-row") {
  if (REDUCED || !root || !root.animate) return;
  const all = [...root.querySelectorAll(sel)];
  const els = all.filter((e) => !all.some((o) => o !== e && o.contains(e))).slice(0, 18);
  els.forEach((e, i) => e.animate([{ opacity: 0, transform: "translateY(14px)" }, { opacity: 1, transform: "none" }],
    { duration: 460, delay: i * 45, easing: EASE, fill: "backwards" }));
}

/* Trait sous l'onglet actif : glisse d'un onglet à l'autre. */
function tabsFade() {
  const n = document.querySelector(".tabs");
  if (!n) return;
  n.classList.toggle("fade-l", n.scrollLeft > 6);
  n.classList.toggle("fade-r", n.scrollLeft + n.clientWidth < n.scrollWidth - 6);
}
function moveTabLine() {
  const nav = document.querySelector(".tabs");
  if (!nav) return;
  let line = nav.querySelector(".tabline");
  if (!line) { line = document.createElement("i"); line.className = "tabline"; nav.append(line); }
  const b = nav.querySelector('[aria-selected="true"]');
  if (!b) return;
  line.style.width = b.offsetWidth + "px";
  line.style.transform = `translateX(${b.offsetLeft}px)`;
  tabsFade();
}
addEventListener("resize", moveTabLine);
addEventListener("DOMContentLoaded", () => { const n = document.querySelector(".tabs"); if (n) n.addEventListener("scroll", tabsFade, { passive: true }); });
if (document.fonts && document.fonts.ready) document.fonts.ready.then(moveTabLine);

/* Squelette d'une page en attente de ses premières données. */
function skeleton(n = 5) {
  return Array.from({ length: n }, (_, i) => h("div", { class: "sk-row", style: `--w:${70 - (i % 3) * 12}%` }, h("i", { class: "sk sk-a" }), h("i", { class: "sk sk-b" }), h("i", { class: "sk sk-c" })));
}

/* Fenêtres : fermeture animée (Échap, clic à l'extérieur, bouton Fermer). */
function closeDlg() {
  const d = document.getElementById("detail");
  if (!d || !d.open) return;
  if (REDUCED) { d.close(); return; }
  d.classList.add("closing");
  setTimeout(() => { d.classList.remove("closing"); d.close(); }, 190);
}

/* Écran de chargement d'une fenêtre : une courbe qui se dessine, des étapes qui défilent, un squelette de la fiche. */
function loadingView(label, title, steps) {
  const path = "M4 58 L30 50 L52 55 L82 34 L108 42 L138 22 L166 30 L196 12 L236 18";
  const chart = s("svg", { class: "ld-chart", viewBox: "0 0 240 70", "aria-hidden": "true" });
  const defs = s("defs"), g = s("linearGradient", { id: "ldg", x1: "0", x2: "0", y1: "0", y2: "1" });
  g.append(s("stop", { offset: "0", style: "stop-color:var(--ink);stop-opacity:.16" }), s("stop", { offset: "1", style: "stop-color:var(--ink);stop-opacity:0" }));
  const clip = s("clipPath", { id: "ldc" }), clipRect = s("rect", { x: 0, y: 0, width: 0, height: 70 });
  clip.append(clipRect);
  defs.append(g, clip);
  chart.append(defs);
  for (const y of [14, 32, 50]) chart.append(s("line", { x1: 0, x2: 240, y1: y, y2: y, class: "ld-grid" }));
  const line = s("path", { d: path, class: "ld-line" }), dot = s("circle", { cx: 4, cy: 58, r: 3.6, class: "ld-dot" }), ring = s("circle", { cx: 4, cy: 58, r: 3.6, class: "ld-ring" });
  chart.append(s("path", { d: path + " L236 70 L4 70 Z", class: "ld-area", fill: "url(#ldg)", "clip-path": "url(#ldc)" }), line, ring, dot);
  const stepEl = h("div", { class: "ld-step" }), dots = h("div", { class: "ld-dots", "aria-hidden": "true" }, steps.map(() => h("i")));
  let k = 0;
  const show = () => {
    stepEl.replaceChildren(h("span", { class: "ld-pulse" }), steps[k]);
    stepEl.animate && !REDUCED && stepEl.animate([{ opacity: 0, transform: "translateY(6px)" }, { opacity: 1, transform: "none" }], { duration: 300, easing: EASE });
    [...dots.children].forEach((d, i) => d.classList.toggle("on", i <= k));
  };
  show();
  const view = h("div", { class: "ld", role: "status", "aria-live": "polite", "aria-label": title },
    h("div", { class: "ld-hero" }, h("span", { class: "ld-chip", text: label }), chart, h("div", { class: "ld-title", text: title }), stepEl, dots, h("div", { class: "ld-bar", "aria-hidden": "true" }, h("i"))),
    h("div", { class: "ld-sk", "aria-hidden": "true" }, h("i", { class: "sk sk-pill" }), h("i", { class: "sk sk-big" }), h("i", { class: "sk sk-line" }), h("i", { class: "sk sk-line s" }), h("div", { class: "ld-grid2" }, h("i", { class: "sk sk-box" }), h("i", { class: "sk sk-box" }), h("i", { class: "sk sk-box" }))));
  const timer = setInterval(() => { if (!view.isConnected) { clearInterval(timer); return; } if (k < steps.length - 1) { k++; show(); } }, 1400);
  // La courbe se dessine progressivement : rapide au début, de plus en plus lente, sans jamais se terminer tant que les données n'arrivent pas.
  // Mise à jour plafonnée à ~12 images/seconde (au lieu de chaque frame) : getPointAtLength() est une requête de géométrie SVG
  // coûteuse, et cet écran peut rester affiché plusieurs secondes sur un chargement lent — pas besoin de 60 im/s pour un
  // mouvement aussi lent et progressif.
  const t0 = performance.now();
  let p = 0, L = 0, lastT = 0;
  const STEP = 1000 / 12;
  const frame = (now) => {
    if (!view.isConnected) { if (now - t0 < 400) requestAnimationFrame(frame); return; }
    if (now - lastT < STEP) { requestAnimationFrame(frame); return; }
    lastT = now;
    if (!L) L = line.getTotalLength();
    const stepBoost = steps.length > 1 ? (k / (steps.length - 1)) * 0.12 : 0;
    const target = Math.min(0.94, (1 - Math.exp(-Math.max(0, now - t0) / 5200)) * 0.82 + stepBoost);
    p += (target - p) * 0.06;
    line.style.strokeDasharray = `${L * p} ${L}`;
    const pt = line.getPointAtLength(L * p);
    dot.setAttribute("cx", pt.x); dot.setAttribute("cy", pt.y); ring.setAttribute("cx", pt.x); ring.setAttribute("cy", pt.y);
    clipRect.setAttribute("width", pt.x);
    requestAnimationFrame(frame);
  };
  if (REDUCED) { line.style.strokeDasharray = "0.6 1"; } else requestAnimationFrame(frame);
  return view;
}
