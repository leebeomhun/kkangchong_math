// 깡충 수학 서비스 워커: 앱 파일을 캐시해 두고 인터넷 없이도 실행되게 합니다.
// index.html 등을 고친 뒤 배포할 때는 VERSION 숫자를 올려 주세요.
const VERSION = 'v12';
const APP_CACHE = `kkangchong-app-${VERSION}`;
const FONT_CACHE = 'kkangchong-fonts';
const ASSETS = [
  './',
  './index.html',
  './sync.js',
  './manifest.webmanifest',
  './icons/icon.svg',
  './icons/icon-192.png',
  './icons/icon-512.png',
  './icons/icon-maskable-512.png',
  './icons/apple-touch-icon.png',
  './assets/stickers/pastel-friends-v1.png',
  './assets/characters/floral-bunny-v1.png',
  './assets/characters/floral-bunny-hop-v1.png',
  './assets/characters/floral-bunny-balloon-v1.png',
  './assets/characters/floral-bunny-carrot-v1.png',
  './assets/stickers/photo-friends-v1/hug-v1.png',
  './assets/stickers/photo-friends-v1/peace-v1.png',
  './assets/stickers/photo-friends-v1/reading-v1.png',
  './assets/stickers/photo-friends-v1/star-v1.png',
  './assets/stickers/photo-friends-v1/carrot-v1.png',
  './assets/stickers/photo-friends-v1/cheer-v1.png',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(APP_CACHE).then((c) => c.addAll(ASSETS)).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== APP_CACHE && k !== FONT_CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);

  // 로그인과 동기화 API는 항상 네트워크로 요청하고 캐시하지 않습니다.
  if (url.origin === self.location.origin && (url.pathname === '/unlock' || url.pathname.startsWith('/api/'))) return;

  // 페이지 자체: 네트워크 우선(업데이트 반영), 오프라인이면 캐시
  // 정상 응답(200번대)만 저장: 잠금 화면(403)이 앱 자리에 저장되지 않게
  if (req.mode === 'navigate') {
    event.respondWith(
      fetch(req)
        .then((res) => {
          if (res.ok) {
            const copy = res.clone();
            caches.open(APP_CACHE).then((c) => c.put('./index.html', copy));
          }
          return res;
        })
        .catch(() => caches.match('./index.html'))
    );
    return;
  }

  // 같은 출처 파일: 캐시 우선
  if (url.origin === self.location.origin) {
    event.respondWith(
      caches.match(req).then((hit) => hit || fetch(req).then((res) => {
        if (res.ok) {
          const copy = res.clone();
          caches.open(APP_CACHE).then((c) => c.put(req, copy));
        }
        return res;
      }))
    );
    return;
  }

  // 구글 폰트: 캐시에 있으면 바로 쓰고 뒤에서 갱신
  if (url.hostname === 'fonts.googleapis.com' || url.hostname === 'fonts.gstatic.com') {
    event.respondWith(
      caches.open(FONT_CACHE).then(async (c) => {
        const hit = await c.match(req);
        const net = fetch(req).then((res) => { c.put(req, res.clone()); return res; }).catch(() => hit);
        return hit || net;
      })
    );
  }
});
