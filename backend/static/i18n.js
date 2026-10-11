"use strict";
/* Langue (FR/EN) et devise d'affichage. Les textes générés par le serveur arrivent déjà traduits ;
   ici : formats de nombres/dates, conversion de devises (taux BCE) et traduction des textes de l'interface (dictionnaire). */
const cookie = (k) => { const m = document.cookie.match(new RegExp("(?:^|; )" + k + "=([^;]*)")); return m ? decodeURIComponent(m[1]) : null; };
/* Français par défaut : l'anglais seulement si l'utilisateur l'a choisi dans son profil (cookie ms_lang posé à l'enregistrement). */
const LANG = cookie("ms_lang") === "en" ? "en" : "fr";
const CURRENCIES = ["EUR", "USD", "GBP", "CHF", "CAD", "JPY", "AUD"];
const CUR = CURRENCIES.includes(cookie("ms_cur")) ? cookie("ms_cur") : (LANG === "en" ? "USD" : "EUR");
const LOC = LANG === "en" ? "en-US" : "fr-FR";
document.documentElement.lang = LANG;
// anciens cookies (langue déduite du navigateur) : supprimés pour ne plus forcer l'anglais
for (const k of ["lang", "cur"]) if (cookie(k)) document.cookie = `${k}=; max-age=0; path=/; samesite=lax`;
/* Firebase Hosting ne transmet au serveur que le cookie « __session » : langue et devise partent donc aussi en en-têtes. */
const _fetch = window.fetch.bind(window);
window.fetch = (input, init = {}) => {
  const url = typeof input === "string" ? input : input.url;
  if (url.startsWith("/")) init = { ...init, headers: { ...(init.headers || {}), "X-MS-Lang": LANG, "X-MS-Cur": CUR } };
  return _fetch(input, init);
};
const T = (fr, en) => (LANG === "en" ? en : fr);

/* Dictionnaire FR -> EN (textes exacts) et motifs pour les textes à valeurs variables : voir dict-en.js */
const DICT = {}, PATTERNS = [];
function t(str) {
  if (LANG !== "en" || typeof str !== "string" || !str) return str;
  if (DICT[str] !== undefined) return DICT[str];
  const trimmed = str.trim();
  if (trimmed !== str && DICT[trimmed] !== undefined) return str.replace(trimmed, DICT[trimmed]);
  for (const [re, fn] of PATTERNS) { const m = str.match(re); if (m) return fn(...m.slice(1)); }
  return str;
}

/* ---- nombres, dates ---- */
const fmtDate = (d) => new Date(d).toLocaleDateString(LOC, { day: "numeric", month: "short", year: "numeric" });
const fmtNum = (v, d = 2) => (v == null ? "–" : Number(v).toLocaleString(LOC, { minimumFractionDigits: d, maximumFractionDigits: d }));
const PCT = LANG === "en" ? "%" : " %";
const fmtPct = (v, d = 0, sign = false) => (v == null ? "–" : (sign ? (v >= 0 ? "+" : "−") : v < 0 ? "−" : "") + fmtNum(Math.abs(v), d) + PCT);

/* ---- devises : taux de la BCE (1 EUR = x devise), chargés au démarrage ---- */
let RATES = null, RATES_INFO = null;
async function loadRates() {
  try { const r = await fetch("/api/fx"); if (r.ok) { RATES_INFO = await r.json(); RATES = RATES_INFO.rates && Object.keys(RATES_INFO.rates).length ? RATES_INFO.rates : null; } } catch (e) { /* hors ligne : montants non convertis */ }
}
/* Convertit v de la devise "from" vers la devise choisie ; renvoie [valeur, devise réellement affichée]. */
function convert(v, from = "USD") {
  if (v == null) return [null, from];
  if (from === "GBp") { v = v / 100; from = "GBP"; }
  if (!RATES || from === CUR || !RATES[from] || !RATES[CUR]) return [v, from];
  return [(v / RATES[from]) * RATES[CUR], CUR];
}
const SYM = { EUR: "€", USD: "$", GBP: "£", CHF: "CHF", CAD: "C$", JPY: "¥", AUD: "A$" };
function money(v, from = "USD", d) {
  if (v == null) return "–";
  const [x, cur] = convert(v, from);
  const dd = d != null ? d : (cur === "JPY" ? 0 : 2);
  const s = fmtNum(x, dd), sym = SYM[cur] || cur;
  return LANG === "fr" ? `${s} ${sym}` : (sym.length === 1 || sym.endsWith("$") ? `${sym}${s}` : `${s} ${sym}`);
}
function moneyBig(v, from = "USD") {
  if (v == null) return "–";
  const [x0, cur] = convert(Math.abs(v), from);
  const x = x0, sign = v < 0 ? "−" : "";
  const [n, u] = x >= 1e12 ? [x / 1e12, T(" T", "T")] : x >= 1e9 ? [x / 1e9, T(" Md", "B")] : x >= 1e6 ? [x / 1e6, T(" M", "M")] : [x / 1e3, T(" k", "k")];
  const s = fmtNum(n, x >= 1e9 ? 1 : 0) + u, sym = SYM[cur] || cur;
  return sign + (LANG === "fr" ? `${s} ${sym}` : (sym.length === 1 || sym.endsWith("$") ? `${sym}${s}` : `${s} ${sym}`));
}
const fmtUsd = (x) => moneyBig(x, "USD");
