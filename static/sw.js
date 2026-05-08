const CACHE_NAME = 'convergence-v3';
const STATIC_ASSETS = [
  '/',
  '/static/manifest.json',
];

// Install — cache static assets
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => cache.addAll(STATIC_ASSETS))
  );
  self.skipWaiting();
});

// Activate — clean old caches
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
    )
  );
  self.clients.claim();
});

// Fetch — network first, fallback to cache
self.addEventListener('fetch', event => {
  if (event.request.method !== 'GET') return;
  if (event.request.url.includes('/api/')) return;
  event.respondWith(
    fetch(event.request)
      .then(response => {
        if (response && response.status === 200) {
          const clone = response.clone();
          caches.open(CACHE_NAME).then(cache => cache.put(event.request, clone));
        }
        return response;
      })
      .catch(() => {
        return caches.match(event.request).then(cached => {
          if (cached) return cached;
          if (event.request.mode === 'navigate') {
            return new Response(`
              <!DOCTYPE html>
              <html>
              <head>
                <meta charset="UTF-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <title>Convergence — Offline</title>
                <style>
                  body { background: #0b0e14; color: #e8eaf0; font-family: sans-serif;
                         display: flex; align-items: center; justify-content: center;
                         min-height: 100vh; text-align: center; padding: 32px; }
                  h2 { font-size: 24px; margin-bottom: 12px; color: #c9a84c; }
                  p  { color: #6b7280; font-size: 14px; }
                  button { margin-top: 20px; background: #c9a84c; color: #0b0e14;
                           border: none; border-radius: 8px; padding: 12px 24px;
                           font-size: 14px; font-weight: 600; cursor: pointer; }
                </style>
              </head>
              <body>
                <div>
                  <div style="font-size:48px;margin-bottom:16px;">📡</div>
                  <h2>You're offline</h2>
                  <p>Convergence needs an internet connection to fetch live stock data.</p>
                  <button onclick="window.location.reload()">Try Again</button>
                </div>
              </body>
              </html>
            `, { headers: { 'Content-Type': 'text/html' } });
          }
        });
      })
  );
});

// ── Push notification handler ──────────────────────────────────────────────
self.addEventListener('push', event => {
  let data = { title: '📈 Convergence Alert', body: 'New stock alert fired.', url: '/' };
  try {
    data = event.data.json();
  } catch(e) {}

  event.waitUntil(
    self.registration.showNotification(data.title, {
      body:    data.body,
      icon:    '/static/icon-192.png',
      badge:   '/static/icon-192.png',
      tag:     data.tag || 'convergence-alert',
      data:    { url: data.url || '/' },
      vibrate: [200, 100, 200],
      actions: [
        { action: 'view',    title: '📊 View' },
        { action: 'dismiss', title: 'Dismiss' }
      ]
    })
  );
});

// Tap notification → open app
self.addEventListener('notificationclick', event => {
  event.notification.close();
  if (event.action === 'dismiss') return;
  const url = event.notification.data?.url || '/';
  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then(list => {
      for (const client of list) {
        if (client.url.includes(self.location.origin) && 'focus' in client) {
          client.navigate(url);
          return client.focus();
        }
      }
      if (clients.openWindow) return clients.openWindow(url);
    })
  );
});
