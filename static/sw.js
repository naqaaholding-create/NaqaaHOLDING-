const CACHE="naqaa-market-v7-mobile-20261007";
const CORE=["./","./index.html","./welcome.html","./market.html","./account.html","./giving.html","./wallet.html","./careers.html","./company-admin.html","./employee.html","./employee-activate.html","./deals.html","./add-listing.html","./admin.html","./admin-finance.html","./assets/market.css","./assets/market.js","./mobile-runtime.js","./reown-config.js","./reown-appkit.js"];
self.addEventListener("install",event=>{event.waitUntil(caches.open(CACHE).then(cache=>cache.addAll(CORE)).then(()=>self.skipWaiting()))});
self.addEventListener("activate",event=>{event.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(k=>k!==CACHE).map(k=>caches.delete(k)))).then(()=>self.clients.claim()))});
self.addEventListener("fetch",event=>{
  if(event.request.method!=="GET") return;
  const url=new URL(event.request.url);
  if(url.origin!==location.origin) return;
  event.respondWith(fetch(event.request).then(response=>{
    const copy=response.clone(); caches.open(CACHE).then(cache=>cache.put(event.request,copy)); return response;
  }).catch(()=>caches.match("./index.html"))));
});