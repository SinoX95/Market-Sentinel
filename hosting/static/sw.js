/* Service worker de l'appli installable (servi à la racine, /sw.js, pour couvrir tout le site).
   Volontairement minimal : il ne met RIEN en cache (ni pages, ni scripts, ni cours), pour qu'on ne voie jamais une
   ancienne version de l'appli ou un vieux cours après une mise à jour. Son seul rôle : quand l'appli est ouverte
   sans connexion, afficher une page claire au lieu de l'erreur du navigateur. Tout le reste passe directement par
   le réseau, exactement comme sur le site. */
const OFFLINE = `<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Hors ligne · Market Sentinel</title>
<style>
:root{color-scheme:light dark;--bg:#fff;--ink:#0b0b0c;--mut:#86878d}
@media (prefers-color-scheme:dark){:root{--bg:#000;--ink:#fff;--mut:#7d7e84}}
body{margin:0;min-height:100vh;display:grid;place-items:center;padding:24px;background:var(--bg);color:var(--ink);
font:16px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,Roboto,sans-serif;text-align:center}
h1{font-size:24px;margin:16px 0 6px;letter-spacing:-.02em}p{color:var(--mut);margin:0 0 24px}
button{border:0;border-radius:99px;padding:13px 26px;font:inherit;font-weight:700;background:var(--ink);color:var(--bg);cursor:pointer}
</style></head><body><main>
<svg width="64" height="64" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2 2l20 20"/><path d="M8.5 16.5a5 5 0 0 1 7 0"/><path d="M5 12.9a10 10 0 0 1 5.2-2.8"/><path d="M19 12.9a10 10 0 0 0-2.3-1.7"/><path d="M2 8.8a15 15 0 0 1 4.2-2.7"/><path d="M22 8.8a15 15 0 0 0-11.3-3.7"/><path d="M12 20h.01"/></svg>
<h1>Pas de connexion</h1><p>Market Sentinel a besoin d'internet pour afficher les cours à jour.</p>
<button onclick="location.reload()">Réessayer</button></main>
<script>addEventListener("online", () => location.reload());</script></body></html>`;

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));
self.addEventListener("fetch", (e) => {
  if (e.request.mode !== "navigate") return; // scripts, styles, images, API : réseau direct, sans intervention
  e.respondWith(fetch(e.request).catch(() => new Response(OFFLINE, { headers: { "Content-Type": "text/html; charset=utf-8" } })));
});
