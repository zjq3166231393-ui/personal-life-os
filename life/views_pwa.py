"""PWA views: manifest.json, service worker, offline page, icon."""
import struct
import zlib

from django.http import HttpResponse, JsonResponse
from django.views.decorators.cache import cache_page


@cache_page(3600)
def manifest(request):
    data = {
        "name": "Personal Life OS",
        "short_name": "Life OS",
        "description": "个人生活管理系统",
        "dir": "ltr",
        "lang": "zh-CN",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "background_color": "#f5f7fb",
        "theme_color": "#2563eb",
        "orientation": "portrait-primary",
        "categories": ["productivity", "lifestyle", "finance"],
        "icons": [
            {"src": "/pwa-icon/192/", "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": "/pwa-icon/512/", "sizes": "512x512", "type": "image/png", "purpose": "any"},
        ],
    }
    return JsonResponse(data)


def pwa_icon(request, size):
    """Generate a simple blue-square PNG icon at the given size."""
    s = int(size)
    # Minimal PNG: blue square with white center circle
    def make_png(w, h, r, g, b):
        raw = b''
        for y in range(h):
            raw += b'\x00'  # filter none
            for x in range(w):
                cx, cy = w // 2, h // 2
                dist = ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5
                if dist < w * 0.35:
                    raw += bytes([255, 255, 255, 255])
                else:
                    raw += bytes([r, g, b, 255])
        return raw

    raw_data = make_png(s, s, 37, 99, 235)

    def chunk(ctype, data):
        c = ctype + data
        return struct.pack('>I', len(data)) + c + struct.pack('>I', zlib.crc32(c) & 0xffffffff)

    png = b'\x89PNG\r\n\x1a\n'
    png += chunk(b'IHDR', struct.pack('>IIBBBBB', s, s, 8, 6, 0, 0, 0))
    png += chunk(b'IDAT', zlib.compress(raw_data))
    png += chunk(b'IEND', b'')
    return HttpResponse(png, content_type='image/png')


@cache_page(86400)
def service_worker(request):
    sw = """
const CACHE = 'lifeos-v3';
const RUNTIME = 'lifeos-runtime-v3';
const APP_SHELL = [
  '/static/offline.html',
  '/manifest.json',
  '/pwa-icon/192/',
  '/pwa-icon/512/',
  '/static/css/lifeos.css?v=4',
  '/static/js/i18n.js?v=2'
];
// Never cache auth / API / user-data pages (privacy + always-fresh HTML).
const NO_CACHE = ['/api/', '/accounts/', '/expenses/', '/tasks/',
                  '/notes/', '/reminders/', '/budget/', '/dashboard/',
                  '/common/', '/recurring/', '/installments/', '/categories/',
                  '/review/', '/admin/', '/export/', '/profile/'];

self.addEventListener('install', function(e) {
  e.waitUntil(
    caches.open(CACHE).then(function(c) { return c.addAll(APP_SHELL).catch(function(){}); })
      .then(function() { return self.skipWaiting(); })
  );
});

self.addEventListener('activate', function(e) {
  e.waitUntil(
    caches.keys().then(function(keys) {
      return Promise.all(keys.filter(function(k) { return k !== CACHE && k !== RUNTIME; })
        .map(function(k) { return caches.delete(k); }));
    }).then(function() { return self.clients.claim(); })
  );
});

function shouldCache(path) { return !NO_CACHE.some(function(p) { return path.indexOf(p) >= 0; }); }

self.addEventListener('fetch', function(e) {
  var url = new URL(e.request.url);
  if (e.request.method !== 'GET') return;
  if (url.origin !== self.location.origin) return; // only handle same-origin

  // Navigations: NETWORK-FIRST, never cached (user-data pages stay private & fresh).
  // Offline -> show the offline page instead of a stale cached HTML.
  if (e.request.mode === 'navigate') {
    e.respondWith(
      fetch(e.request).catch(function() { return caches.match('/static/offline.html'); })
    );
    return;
  }

  // Static assets: CACHE-FIRST, then network, then cache.
  e.respondWith(
    caches.match(e.request).then(function(cached) {
      if (cached) return cached;
      return fetch(e.request).then(function(res) {
        if (res.ok && shouldCache(url.pathname)) {
          var copy = res.clone();
          caches.open(RUNTIME).then(function(c) { return c.put(e.request, copy); });
        }
        return res;
      }).catch(function() { return cached; });
    })
  );
});

self.addEventListener('push', function(e) {
  var data = e.data ? e.data.json() : {};
  var title = data.title || 'Personal Life OS';
  var opts = { body: data.body || '', icon: '/pwa-icon/192/', badge: '/pwa-icon/192/' };
  e.waitUntil(self.registration.showNotification(title, opts));
});

self.addEventListener('notificationclick', function(e) {
  e.notification.close();
  e.waitUntil(clients.openWindow('/'));
});
"""
    return HttpResponse(sw, content_type="application/javascript")
