const CACHE_NAME = 'convergence-v1';
const STATIC_ASSETS = [
  '/',
  '/static/manifest.json',
];

// Install — cache static assets
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => {
      return cache.addAll(STATIC_ASSETS);
    })
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
  // Skip non-GET and API requests
  if (event.request.method !== 'GET') return;
  if (event.request.url.includes('/api/')) return;

  event.respondWith(
    fetch(event.request)
      .then(response => {
        // Cache successful responses
        if (response && response.status === 200) {
          const clone = response.clone();
          caches.open(CACHE_NAME).then(cache => cache.put(event.request, clone));
        }
        return response;
      })
      .catch(() => {
        // Fallback to cache when offline
        return caches.match(event.request).then(cached => {
          if (cached) return cached;
          // Return offline page for navigation requests
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
