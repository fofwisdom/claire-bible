// Claire PWA service worker. The server sends this file at /sw.js with the scope "/".
// 1. Requests go to the network without a cache (network passthrough).
// 2. Web Push: the worker shows each notification and opens its entry address (docs/design/PWA_NOTIFICATION_DESIGN.md PN4 rule 1).
self.addEventListener('install', (event) => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener('fetch', (event) => {
  event.respondWith(fetch(event.request));
});

const NOTIFY_ICON = '/icon?p=android-chrome-192x192.png';
const NOTIFY_BADGE = '/icon?p=android-chrome-96x96.png';
const NOTIFY_FALLBACK_PATH = '/account/notifications';

// The entry address of a notification is a path of this server. Other values give the notification settings page.
function entryPath(value) {
  if (typeof value !== 'string' || value.charAt(0) !== '/' || value.charAt(1) === '/' || value.indexOf('\\') !== -1) return NOTIFY_FALLBACK_PATH;
  try {
    const url = new URL(value, self.location.origin);
    return url.origin === self.location.origin ? url.pathname + url.search + url.hash : NOTIFY_FALLBACK_PATH;
  } catch (_) {
    return NOTIFY_FALLBACK_PATH;
  }
}

function setBadge(unread) {
  const nav = self.navigator;
  if (!nav || typeof unread !== 'number') return Promise.resolve();
  try {
    if (unread > 0 && nav.setAppBadge) return nav.setAppBadge(unread).catch(() => {});
    if (unread <= 0 && nav.clearAppBadge) return nav.clearAppBadge().catch(() => {});
  } catch (_) {}
  return Promise.resolve();
}

function tellPages(message) {
  return self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((pages) => {
    pages.forEach((page) => {
      try { page.postMessage(message); } catch (_) {}
    });
  });
}

function postJson(path, method, body) {
  return fetch(path, { method: method, credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
}

self.addEventListener('push', (event) => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch (_) { data = {}; }
  const title = typeof data.title === 'string' && data.title ? data.title : 'Claire Bible';
  const options = {
    body: typeof data.body === 'string' ? data.body : '',
    icon: NOTIFY_ICON,
    badge: NOTIFY_BADGE,
    data: { id: typeof data.id === 'number' ? data.id : null, url: entryPath(data.url) },
  };
  // The same tag replaces the shown notification of the same record (a new count); renotify makes the device tell the person again.
  if (typeof data.tag === 'string' && data.tag) {
    options.tag = data.tag;
    options.renotify = true;
  }
  event.waitUntil(Promise.all([
    self.registration.showNotification(title, options),
    setBadge(data.unread),
    tellPages({ type: 'claire-notification', unread: typeof data.unread === 'number' ? data.unread : null }),
  ]));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const data = event.notification.data || {};
  const path = entryPath(data.url);
  event.waitUntil((async () => {
    if (typeof data.id === 'number') {
      try {
        const res = await postJson('/api/me/notifications/read', 'POST', { ids: [data.id] });
        if (res.ok) {
          const state = await res.json();
          await setBadge(state.unread);
          await tellPages({ type: 'claire-notification', unread: typeof state.unread === 'number' ? state.unread : null });
        }
      } catch (_) {}
    }
    // A page that shows the entry address already gets the focus. If there is none, a new window opens: the worker does not replace a page in which the person works.
    const pages = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    for (const page of pages) {
      let shown = '';
      try { const url = new URL(page.url); shown = url.pathname + url.search + url.hash; } catch (_) {}
      if (shown === path && 'focus' in page) {
        try { await page.focus(); return; } catch (_) {}
      }
    }
    await self.clients.openWindow(path);
  })());
});

// The browser replaced the subscription (a key change of the push service). The worker subscribes again with the same options and tells the server.
self.addEventListener('pushsubscriptionchange', (event) => {
  event.waitUntil((async () => {
    const old = event.oldSubscription || null;
    let next = event.newSubscription || null;
    try {
      if (!next && old && old.options) next = await self.registration.pushManager.subscribe(old.options);
      if (!next) return;
      await postJson('/api/me/push/subscriptions', 'POST', next.toJSON());
      if (old && old.endpoint && old.endpoint !== next.endpoint) await postJson('/api/me/push/subscriptions', 'DELETE', { endpoint: old.endpoint });
    } catch (_) {}
  })());
});
