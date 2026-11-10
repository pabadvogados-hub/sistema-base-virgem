/* Service worker do Portal do Cliente.

   MUDANÇA IMPORTANTE (02/09/2026): a PÁGINA não é mais guardada em cache.

   Por quê: a Advogada testou o conserto do botão PDF, entrou com um código
   novo, e continuou vendo o erro antigo. O servidor já estava com a versão
   nova; quem estava velho era o navegador dela, servindo o index.html que
   este service worker havia guardado. Enquanto o portal muda todo dia, cache
   de HTML só esconde correção e faz a gente caçar fantasma.

   O que ainda fica em cache: ícone e manifest, que são estáveis e é o que a
   instalação na tela de início precisa. Dado de processo NUNCA entra em cache,
   porque é pessoal e muda.

   CACHE mudou de nome de propósito: nome novo faz o navegador descartar tudo
   o que a versão anterior tinha guardado.                                    */

const CACHE = 'esc-portal-v3-sem-html';
const ESTAVEIS = ['/portal/manifest.json', '/portal/icone.svg',
                  '/portal/icone-192.png', '/portal/icone-512.png'];

self.addEventListener('install', (e) => {
  e.waitUntil(
    caches.open(CACHE)
      .then((c) => c.addAll(ESTAVEIS).catch(() => null))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys()
      .then((ks) => Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET') return;
  if (url.pathname.startsWith('/api/')) return;        // dado pessoal, nunca em cache
  if (!url.pathname.startsWith('/portal')) return;

  // Navegação e HTML: SEMPRE da rede. Sem cache, sem versão velha presa.
  const ehPagina = e.request.mode === 'navigate' ||
                   (e.request.headers.get('accept') || '').includes('text/html');
  if (ehPagina) return;

  // Ícone e manifest: cache primeiro, que é rápido e não muda.
  e.respondWith(
    caches.match(e.request).then((achado) => achado || fetch(e.request).then((r) => {
      const copia = r.clone();
      caches.open(CACHE).then((c) => c.put(e.request, copia)).catch(() => {});
      return r;
    }))
  );
});
