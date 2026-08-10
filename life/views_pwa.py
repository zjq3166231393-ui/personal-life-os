"""PWA views: manifest.json, service worker, offline page, icon."""
from django.http import HttpResponse, JsonResponse
from django.views.decorators.cache import cache_page
import struct, zlib


@cache_page(3600)
def manifest(request):
    data = {
        "name": "Personal Life OS",
        "short_name": "Life OS",
        "description": "个人生活管理系统",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#f5f7fb",
        "theme_color": "#2563eb",
        "orientation": "portrait-primary",
        "icons": [
            {"src": "/pwa-icon/192/", "sizes": "192x192", "type": "image/png"},
            {"src": "/pwa-icon/512/", "sizes": "512x512", "type": "image/png"},
        ],
        "lang": "zh-CN",
    }
    return JsonResponse(data)


def pwa_icon(request, size):
    """Generate a simple blue-square PNG icon at the given size."""
    s = int(size)
    # Minimal PNG: blue square with white "L" letter area
    def make_png(w, h, r, g, b):
        raw = b''
        for y in range(h):
            raw += b'\x00'  # filter none
            for x in range(w):
                # Center circle in lighter blue
                cx, cy = w//2, h//2
                dist = ((x-cx)**2 + (y-cy)**2)**0.5
                if dist < w*0.35:
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
const CACHE = 'lifeos-v1';
const URLS = [
  '/',
  '/static/offline.html',
  'https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css',
  'https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@400;600;700&display=swap',
];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(URLS)));
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))));
  self.clients.claim();
});

self.addEventListener('push', e => {
  const data = e.data ? e.data.json() : {};
  const title = data.title || 'Personal Life OS';
  const opts = { body: data.body || '', icon: '/pwa-icon/192/', badge: '/pwa-icon/192/' };
  e.waitUntil(self.registration.showNotification(title, opts));
});

self.addEventListener('notificationclick', e => {
  e.notification.close();
  e.waitUntil(clients.openWindow('/'));
});

self.addEventListener('fetch', e => {
  // Never cache API calls or dynamic data
  if (e.request.url.includes('/api/') || e.request.url.includes('/accounts/')) {
    return;
  }
  e.respondWith(
    caches.match(e.request).then(cached =>
      cached || fetch(e.request).then(resp => {
        if (resp.ok && e.request.method === 'GET') {
          const clone = resp.clone();
          caches.open(CACHE).then(c => c.put(e.request, clone));
        }
        return resp;
      }).catch(() => {
        if (e.request.mode === 'navigate') {
          return caches.match('/static/offline.html');
        }
      })
    )
  );
});
"""
    return HttpResponse(sw, content_type="application/javascript")