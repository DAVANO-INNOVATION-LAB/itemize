/* Offline support. The page claims it works offline once loaded; without this
 * file that claim is false on reload, which is a promise the code does not keep.
 *
 * Strategy: NETWORK FIRST, cache as fallback.
 *
 * The obvious implementation is cache-first, and it is wrong here. This tool's
 * correctness depends on freshness -- state-law entries, Medicare rates and the
 * rules themselves all change. A cache-first worker keyed on a hand-bumped
 * version string means the first forgotten bump silently pins every user to a
 * stale copy: "works offline" would be true while "tells you current law"
 * quietly became false. That trade is unacceptable for this particular page.
 *
 * So: when online, always take the network answer and refresh the cache. When
 * offline, serve the last good copy. The cost is one round trip on a static
 * site; the benefit is that a correction actually reaches people.
 *
 * Nothing about a user's bill is ever cached, because nothing about it ever
 * leaves the page. */
'use strict';

const CACHE = 'itemize';
const SHELL = [
  './', './index.html', './style.css', './rules.js', './app.js', './pdf.js',
  './data/hcpcs.json', './data/asp.json', './data/dmepos.json',
  './data/nadac.json', './data/drg.json',
  './data/states.json', './data/manifest.json',
];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE)
    .then((c) => c.addAll(SHELL))
    .then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener('fetch', (e) => {
  if (e.request.method !== 'GET') return;
  if (new URL(e.request.url).origin !== self.location.origin) return;

  e.respondWith(
    fetch(e.request)
      .then((res) => {
        if (res && res.ok) {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(e.request, copy));
        }
        return res;
      })
      .catch(() => caches.match(e.request)
        .then((hit) => hit || caches.match('./index.html')))
  );
});
