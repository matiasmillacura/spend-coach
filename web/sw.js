/* Service worker del Coach de Gastos: cachea el shell; la API nunca se cachea. */
const CACHE = "coach-shell-v1";
const SHELL = ["/", "/app.js", "/styles.css", "/manifest.json", "/icon-192.png", "/icon-512.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin) return;
  if (url.pathname.startsWith("/api/") || url.pathname.startsWith("/auth") ||
      url.pathname === "/login" || url.pathname === "/logout") {
    return;
  }
  e.respondWith(
    fetch(e.request)
      .then((resp) => {
        const copia = resp.clone();
        caches.open(CACHE).then((c) => c.put(e.request, copia));
        return resp;
      })
      .catch(() =>
        caches.match(e.request).then((hit) => hit || (e.request.mode === "navigate" ? caches.match("/") : undefined))
      )
  );
});
