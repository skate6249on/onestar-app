const C='onestar-v12';const A=['/','/index.html','/manifest.json','/icon-192.png','/logo.png','/logo_header.jpg','/icon-512.png','/hero.jpg','/hero_ollie.jpg','/hero_park.jpg','/onestar_adult_lesson.jpg','/onestar_kids_lesson_1.jpg'];
self.addEventListener('install',e=>e.waitUntil(caches.open(C).then(c=>c.addAll(A))));
self.addEventListener('fetch',e=>{if(new URL(e.request.url).pathname.startsWith('/api/')||e.request.method!=='GET')return;e.respondWith(fetch(e.request).catch(()=>caches.match(e.request).then(r=>r||caches.match('/'))))});
