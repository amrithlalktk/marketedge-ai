/* MarketEdge AI — minimal service worker for Web Push notifications only (no caching). */
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch {
    data = { title: "MarketEdge AI", body: event.data ? event.data.text() : "" };
  }
  const title = data.title || "MarketEdge AI";
  const url = typeof data.url === "string" && data.url.startsWith("/") ? data.url : typeof data.link === "string" && data.link.startsWith("/") ? data.link : "/notifications";
  event.waitUntil(self.registration.showNotification(title, { body: data.body || "", tag: data.tag || undefined, data: { url } }));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || "/notifications";
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((list) => {
      for (const c of list) {
        if ("focus" in c && new URL(c.url).origin === self.location.origin) {
          c.navigate(url);
          return c.focus();
        }
      }
      return self.clients.openWindow(url);
    }),
  );
});
