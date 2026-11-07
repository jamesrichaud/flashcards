"""iPad and Chromebook polish: home-screen app (manifest + PNG icons), offline page served by a
small service worker, an "offline" banner, readable HTML error pages, and the optional
tap-left/right-half answering on the study screen (a preference, kept in the collection's config
under `flashcardsTapZones` so it follows the user to every device).

The icons are drawn here (no image library needed): a blue square with two white cards, drawn
with signed distance functions so the edges are smooth, encoded as PNG with zlib.
"""
import functools, html, struct, zlib

from fastapi import APIRouter, Request
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, Response, PlainTextResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

import app as core
from flashcards.i18n import t, N_

router = APIRouter()

TAP_KEY = "flashcardsTapZones"
ACCENT = (0x2f, 0x6f, 0xde)


def tap_zones(col):
    return bool(col.get_config(TAP_KEY, False))


# ---------- icons ----------
def _png(w, h, rows):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
    raw = b"".join(b"\x00" + bytes(r) for r in rows)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def _box(px, py, cx, cy, hw, hh, r):
    """Signed distance from (px, py) to a rounded rectangle (negative inside)."""
    qx, qy = abs(px - cx) - hw + r, abs(py - cy) - hh + r
    out = (max(qx, 0) ** 2 + max(qy, 0) ** 2) ** 0.5
    return out + min(max(qx, qy), 0) - r


@functools.lru_cache(maxsize=8)
def icon_png(size):
    """Full-bleed square (iOS and Android round the corners themselves); the drawing stays
    inside the centre circle so it also works as a maskable icon."""
    s = float(size)
    # (centre x, centre y, half width, half height, radius) in fractions of the size, and colour.
    shapes = [((0.56, 0.42, 0.24, 0.16, 0.04), (0xa9, 0xc5, 0xf3)),   # card behind
              ((0.45, 0.57, 0.26, 0.18, 0.045), (255, 255, 255)),      # card in front
              ((0.45, 0.57, 0.14, 0.026, 0.026), ACCENT)]              # a line of text on it
    shapes = [(tuple(v * s for v in g), c) for g, c in shapes]
    rows = []
    for y in range(size):
        row = []
        py = y + 0.5
        for x in range(size):
            px = x + 0.5
            col = ACCENT
            for (cx, cy, hw, hh, r), c in shapes:
                d = _box(px, py, cx, cy, hw, hh, r)
                a = 0.5 - d
                if a <= 0:
                    continue
                if a >= 1:
                    col = c
                else:
                    col = tuple(round(col[i] + (c[i] - col[i]) * a) for i in range(3))
            row.extend(col)
        rows.append(row)
    return _png(size, size, rows)


def _icon(size):
    return Response(icon_png(size), media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})


@router.get("/apple-touch-icon.png")
@router.get("/apple-touch-icon-precomposed.png")
def apple_touch_icon():
    return _icon(180)


@router.get("/icon-192.png")
def icon_192():
    return _icon(192)


@router.get("/icon-512.png")
def icon_512():
    return _icon(512)


@router.get("/favicon.ico")
def favicon():
    return _icon(64)   # PNG data; every current browser accepts it under this name


@router.get("/manifest.json")
def manifest():
    icons = [{"src": f"/icon-{n}.png", "sizes": f"{n}x{n}", "type": "image/png", "purpose": p}
             for n in (192, 512) for p in ("any", "maskable")]
    return {"name": "Flashcards", "short_name": "Flashcards", "id": "/", "start_url": "/", "scope": "/",
            "display": "standalone", "orientation": "any", "background_color": "#f6f6f4",
            "theme_color": "#2f6fde", "icons": icons}


# ---------- head tags and scripts added to every page ----------
def head_html(theme):
    if theme == "dark":
        colour = '<meta name="theme-color" content="#232325">'
    elif theme == "light":
        colour = '<meta name="theme-color" content="#ffffff">'
    else:
        colour = ('<meta name="theme-color" content="#ffffff" media="(prefers-color-scheme: light)">'
                  '<meta name="theme-color" content="#232325" media="(prefers-color-scheme: dark)">')
    return ('<meta name="apple-mobile-web-app-status-bar-style" content="default">'
            '<link rel="apple-touch-icon" href="/apple-touch-icon.png">'
            '<link rel="icon" type="image/png" sizes="192x192" href="/icon-192.png">' + colour)


# Offline banner: shown while the device says it has no connection, so a tap that can't reach
# the server isn't a surprise. The service worker needs HTTPS (or localhost); it is skipped
# quietly elsewhere and the browser's own offline page is shown instead.
def body_script():
    """The offline banner and service worker registration, in the request's language.
    core.page reads it as `touch_mod.BODY_SCRIPT` (see __getattr__ below)."""
    banner = html.escape(t("You're offline. Changes can't be saved until the connection is back."), False)
    study = html.escape(t("Study offline"), False)   # the offline study page (flashcards/offline.py)
    return f"""<div id="fc-offline" role="status" hidden>{banner} <a href="/offline/study">{study}</a></div>
<script>(function(){{const b=document.getElementById('fc-offline');
function upd(){{b.hidden=navigator.onLine!==false;}}window.addEventListener('online',upd);window.addEventListener('offline',upd);upd();
if('serviceWorker' in navigator&&window.isSecureContext)navigator.serviceWorker.register('/sw.js').catch(()=>{{}});}})();</script>"""


def __getattr__(name):
    # BODY_SCRIPT and OFFLINE_HTML are built on each use so they follow the request's language.
    if name == "BODY_SCRIPT":
        return body_script()
    if name == "OFFLINE_HTML":
        return offline_html()
    raise AttributeError(name)

CSS = """
html{touch-action:manipulation}
header{padding:max(10px,env(safe-area-inset-top)) max(16px,env(safe-area-inset-right)) 10px max(16px,env(safe-area-inset-left))}
header a{min-height:44px;display:inline-flex;align-items:center}
main{padding-left:max(16px,env(safe-area-inset-left));padding-right:max(16px,env(safe-area-inset-right));
padding-bottom:calc(60px + env(safe-area-inset-bottom))}
a.sort{display:inline-flex;align-items:center;min-height:44px}
.btable a.deck{min-height:44px;padding:11px 0;margin:-8px 0}
summary{min-height:44px}
input[type=checkbox],input[type=radio]{min-width:22px;min-height:22px}
html:has([data-noundokey]){overscroll-behavior:none}
#fc-offline{position:fixed;left:0;right:0;bottom:0;z-index:40;background:#8a1f1f;color:#fff;text-align:center;
padding:10px 16px calc(10px + env(safe-area-inset-bottom));font-size:15px}
#fc-offline a{color:#fff;font-weight:600;text-decoration:underline;display:inline-flex;align-items:center;min-height:44px;margin:-12px 0}
#fc-toast{top:calc(70px + env(safe-area-inset-top))}
"""

SW_JS = """// Flashcards service worker. Everything comes from the server while it can be reached (network
// first), so nothing goes stale. When it can't be reached:
// - a page (GET) shows the offline study page (flashcards/offline.py, cached by the offline script in
//   "fc-shell"), which studies the decks kept offline; without one, the "You're offline" page;
// - an answer on the study screen (POST /study/<deck>/answer) goes to the offline study page with the
//   answer, which queues it; other form posts show the "You're offline" page;
// - card media, the offline page's script and the bundled MathJax come from Cache Storage
//   (with Range requests answered, which Safari uses for audio).
const C='fc-offline-v2';
self.addEventListener('install',e=>{e.waitUntil(caches.open(C).then(c=>c.addAll(['/offline','/icon-192.png'])).then(()=>self.skipWaiting()));});
// Only this worker's own old caches are deleted: the offline copies' media (fc-media:<user>) and the
// offline study page (fc-shell) stay.
self.addEventListener('activate',e=>{e.waitUntil(caches.keys().then(ks=>Promise.all(ks.filter(k=>k.startsWith('fc-offline-')&&k!==C).map(k=>caches.delete(k)))).then(()=>self.clients.claim()));});
const OFFLINE_GET=/^\\/(media|static|offline\\/client\\.js|icon-|apple-touch-icon|favicon|manifest\\.json)/;
async function fromCache(req){const u=new URL(req.url);
 const r=await caches.match(u.pathname,{ignoreSearch:true})||await caches.match(req,{ignoreSearch:true});
 if(!r)return Response.error();
 const range=req.headers.get('range'),m=range&&/^bytes=(\\d*)-(\\d*)$/.exec(range.trim());
 if(!m||r.status!==200)return r;
 const buf=await r.arrayBuffer(),n=buf.byteLength;let a=m[1]===''?Math.max(0,n-(+m[2]||0)):+m[1],b=m[1]!==''&&m[2]!==''?Math.min(+m[2],n-1):n-1;
 if(a>=n||a>b)return new Response('',{status:416,headers:{'Content-Range':'bytes */'+n}});
 return new Response(buf.slice(a,b+1),{status:206,headers:{'Content-Type':r.headers.get('Content-Type')||'application/octet-stream',
  'Content-Range':'bytes '+a+'-'+b+'/'+n,'Content-Length':String(b-a+1),'Accept-Ranges':'bytes'}});}
async function offlinePage(){return await caches.match('/offline/study',{ignoreSearch:true})||
 await caches.open(C).then(c=>c.match('/offline'))||Response.error();}
async function offlinePost(copy){const u=new URL(copy.url),m=/^\\/study\\/(\\d+)\\/answer$/.exec(u.pathname);
 if(m&&await caches.match('/offline/study')){try{const f=await copy.formData();
  const a={cid:+f.get('card_id'),rating:+f.get('rating'),ms:+f.get('ms')||0,ts:Date.now()};
  if(a.cid&&a.rating>=1&&a.rating<=4)return Response.redirect(self.location.origin+'/study/'+m[1]+'?fcans='+encodeURIComponent(JSON.stringify(a)),303);}catch(x){}}
 return await caches.open(C).then(c=>c.match('/offline'))||Response.error();}
self.addEventListener('fetch',e=>{const req=e.request,u=new URL(req.url);if(u.origin!==self.location.origin)return;
 if(req.mode!=='navigate'){if(req.method==='GET'&&OFFLINE_GET.test(u.pathname))e.respondWith(fetch(req).catch(()=>fromCache(req)));return;}
 if(req.method==='GET'){e.respondWith(fetch(req).catch(()=>offlinePage()));return;}
 const copy=req.clone();e.respondWith(fetch(req).catch(()=>offlinePost(copy)));});
// Study reminders (flashcards/reminders.py): show the push, and open the app when it is tapped.
self.addEventListener('push',e=>{let d={};try{d=e.data?e.data.json():{};}catch(x){d={body:e.data?e.data.text():''};}
 e.waitUntil(self.registration.showNotification(d.title||'Flashcards',{body:d.body||'',tag:d.tag||'fc-reminder',
  icon:'/icon-192.png',badge:'/icon-192.png',data:{url:d.url||'/'}}));});
self.addEventListener('notificationclick',e=>{e.notification.close();const url=(e.notification.data&&e.notification.data.url)||'/';
 e.waitUntil(clients.matchAll({type:'window',includeUncontrolled:true}).then(ws=>{for(const w of ws){if('focus' in w){w.navigate&&w.navigate(url).catch(()=>{});return w.focus();}}
  return clients.openWindow(url);}));});
"""


@router.get("/sw.js")
def service_worker():
    return Response(SW_JS, media_type="text/javascript", headers={"Cache-Control": "no-cache"})


# Self-contained (no login, no outside files) because it is shown when the server is unreachable.
OFFLINE_TEMPLATE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><title>%(title)s</title>
<style>:root{color-scheme:light dark}body{margin:0;font:17px/1.45 -apple-system,system-ui,Segoe UI,Roboto,sans-serif;
background:#f6f6f4;color:#1d1d1f;display:flex;min-height:100vh;align-items:center;justify-content:center;
padding:20px env(safe-area-inset-right) env(safe-area-inset-bottom) env(safe-area-inset-left);box-sizing:border-box}
.box{background:#fff;border:1px solid #e3e3e0;border-radius:14px;padding:24px;max-width:440px;margin:16px}
h1{font-size:22px;margin:0 0 8px}p{color:#6e6e73}.row{display:flex;gap:10px;flex-wrap:wrap}
button,a{font:inherit;padding:11px 18px;border-radius:10px;border:1px solid #e3e3e0;background:#fff;color:#1d1d1f;
min-height:44px;text-decoration:none;cursor:pointer;display:inline-flex;align-items:center;touch-action:manipulation}
.primary{background:#2f6fde;border-color:#2f6fde;color:#fff}
@media (prefers-color-scheme:dark){body{background:#161617;color:#f2f2f2}.box,button,a{background:#232325;border-color:#38383b;color:#f2f2f2}
p{color:#a1a1a6}.primary{background:#2f6fde;border-color:#2f6fde;color:#fff}}</style></head><body>
<div class="box"><h1>%(heading)s</h1>
<p>%(text)s</p>
<div class="row"><button class="primary" onclick="location.reload()">%(again)s</button><a href="/">%(decks)s</a></div></div>
</body></html>"""


def offline_html():
    e = lambda s: html.escape(s, False)
    return OFFLINE_TEMPLATE % {
        "title": e(t("Offline")), "heading": e(t("You're offline")),
        "text": e(t("Flashcards can't reach the server. Everything already saved is safe; the last thing you tapped "
                    "may not have\nbeen saved. Check the Wi-Fi, then try again.")),
        "again": e(t("Try again")), "decks": e(t("Decks"))}


@router.get("/offline", response_class=HTMLResponse)
def offline():
    return HTMLResponse(offline_html(), headers={"Cache-Control": "no-cache"})


# ---------- error pages ----------
MESSAGES = {
    401: (N_("Please log in"), N_("You need to log in to see this page.")),
    403: (N_("Not allowed"), N_("You can't open this page.")),
    404: (N_("Page not found"), N_("There's nothing at this address. It may have been deleted, or the link is old.")),
    405: (N_("Can't open this page"), N_("This page can't be opened like that. Go back and use the buttons on the page.")),
    422: (N_("Something wasn't right"), N_("Part of the address or the form wasn't understood. Go back and try again.")),
    500: (N_("Something went wrong"), N_("The server hit a problem. Nothing more was changed. Go back and try again; "
                                         "if it keeps happening, Settings > Check Database may help.")),
}


def wants_html(request):
    return "text/html" in request.headers.get("accept", "")


def error_page(request, status):
    title, text = MESSAGES.get(status, MESSAGES[500] if status >= 500 else (MESSAGES[422][0], MESSAGES[422][1]))
    title, text = t(title), t(text)
    try:
        user = core.current_user(request)
    except Exception:  # noqa: BLE001  (e.g. the session isn't available on a server error)
        user = None
    e = lambda s: html.escape(s, False)
    body = f"""<div class="box" style="max-width:520px;margin:40px auto"><h2 style="margin-top:0">{e(title)}</h2>
<p class="muted">{e(text)}</p><p class="muted" style="font-size:14px">{e(t("Error {status}", status=status))}</p>
<div class="row"><button type="button" onclick="history.length>1?history.back():location.assign('/')">{e(t("← Back"))}</button>
<a class="btn primary" href="{'/' if user else '/login'}">{e(t("Decks") if user else t("Log in"))}</a></div></div>"""
    r = core.page(title, body, user)
    r.status_code = status
    return r


async def on_http_error(request: Request, exc: StarletteHTTPException):
    if wants_html(request) and exc.status_code >= 400:
        r = error_page(request, exc.status_code)
        for k, v in (getattr(exc, "headers", None) or {}).items():
            r.headers[k] = v
        return r
    return await http_exception_handler(request, exc)


async def on_validation_error(request: Request, exc: RequestValidationError):
    if wants_html(request):
        return error_page(request, 422)
    return await request_validation_exception_handler(request, exc)


async def on_server_error(request: Request, exc: Exception):
    # Starlette logs the exception after this returns.
    if wants_html(request):
        return error_page(request, 500)
    return PlainTextResponse("Internal Server Error", status_code=500)


core.app.add_exception_handler(StarletteHTTPException, on_http_error)
core.app.add_exception_handler(RequestValidationError, on_validation_error)
core.app.add_exception_handler(Exception, on_server_error)
