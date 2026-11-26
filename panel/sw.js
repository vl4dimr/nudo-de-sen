/* Service worker del panel (alcance /admin/).
   - Deja instalar el panel como app y abrirlo aunque la señal esté mala (guarda la «cáscara»).
   - Recibe fotos y videos que se comparten desde la galería del celular («Compartir → Panel SEN»).
   - Nunca guarda en caché las respuestas de la API: los datos siempre vienen frescos del servidor. */
const VERSION = 'panel-v1';
const CASCARA = ['/admin/', '/admin/panel.js', '/admin/manifest.webmanifest',
  '/assets/giro360.js', '/assets/vendor/sweetalert2.all.min.js', '/assets/vendor/Sortable.min.js',
  '/assets/vendor/cropper.min.js', '/assets/vendor/cropper.min.css', '/assets/favicon.svg',
  '/assets/panel-icono-192.png'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(VERSION).then(c => c.addAll(CASCARA)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== VERSION && k !== 'compartidos').map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener('fetch', e => {
  const u = new URL(e.request.url);
  // Archivos compartidos desde otra app: se guardan y se abre el panel para elegir qué hacer con ellos
  if (e.request.method === 'POST' && u.pathname === '/admin/compartir') {
    e.respondWith((async () => {
      const datos = await e.request.formData();
      const c = await caches.open('compartidos');
      for (const k of await c.keys()) await c.delete(k);
      let i = 0;
      for (const f of datos.getAll('archivos')) {
        if (!(f instanceof File)) continue;
        await c.put(new Request('/admin/compartido/' + (i++)), new Response(f, {
          headers: { 'Content-Type': f.type || 'application/octet-stream', 'X-Nombre': encodeURIComponent(f.name || ('archivo-' + i)) }
        }));
      }
      return Response.redirect('/admin/?compartido=' + i, 303);
    })());
    return;
  }
  if (e.request.method !== 'GET' || u.origin !== location.origin) return;
  if (u.pathname.startsWith('/admin/api/')) return;                       // la API, siempre directo
  if (u.pathname === '/admin/' || u.pathname === '/admin/panel.js') {      // la cáscara: primero la red, si falla la copia
    e.respondWith(fetch(e.request).then(r => { const c = r.clone(); caches.open(VERSION).then(x => x.put(e.request, c)); return r; })
      .catch(() => caches.match(e.request)));
    return;
  }
  if (CASCARA.includes(u.pathname)) e.respondWith(caches.match(e.request).then(r => r || fetch(e.request)));
});
