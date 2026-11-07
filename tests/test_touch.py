"""iPad and Chromebook polish: home-screen manifest and icons, offline page and service worker,
HTML error pages, safe-area/touch CSS, and the optional tap-to-answer setting."""
import json, os, re, struct, sys, tempfile, zlib
os.environ["DATA_DIR"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fastapi.testclient import TestClient
import app as webapp
from flashcards import touch

ok = lambda m: print("PASS", m)
c = TestClient(webapp.app)
HTML = {"Accept": "text/html,application/xhtml+xml"}


def png_size(data):
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    w, h, depth, ctype = struct.unpack(">IIBB", data[16:26])
    assert depth == 8 and ctype == 2
    idat = b"".join(data[i + 8:i + 8 + struct.unpack(">I", data[i:i + 4])[0]]
                    for i in [m.start() - 4 for m in re.finditer(b"IDAT", data)])
    assert len(zlib.decompress(idat)) == h * (1 + w * 3)
    return w, h


# ---------- manifest and icons (no login needed: the browser fetches them on its own) ----------
m = c.get("/manifest.json").json()
assert m["display"] == "standalone" and m["start_url"] == "/" and m["scope"] == "/" and m["name"] == "Flashcards"
sizes = {i["sizes"] for i in m["icons"]}
assert {"192x192", "512x512"} <= sizes and {"any", "maskable"} <= {i["purpose"] for i in m["icons"]}
for i in m["icons"]:
    r = c.get(i["src"]); assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    n = int(i["sizes"].split("x")[0]); assert png_size(r.content) == (n, n), i
ok("manifest: standalone, scope /, 192 and 512 PNG icons (any + maskable) that decode")

for path in ("/apple-touch-icon.png", "/apple-touch-icon-precomposed.png"):
    r = c.get(path); assert r.status_code == 200 and png_size(r.content) == (180, 180), path
assert png_size(c.get("/favicon.ico").content) == (64, 64)
# Corner is the blue background, centre of the front card is white.
data = touch.icon_png(64)
raw = zlib.decompress(data[data.index(b"IDAT") + 4:-16])
px = lambda x, y: tuple(raw[y * (1 + 64 * 3) + 1 + x * 3: y * (1 + 64 * 3) + 4 + x * 3])
assert px(0, 0) == touch.ACCENT and px(20, 40) == (255, 255, 255), (px(0, 0), px(20, 40))
ok("apple-touch-icon 180x180 (also -precomposed), favicon; icon is blue with a white card")

# ---------- head tags, CSS and scripts on every page ----------
r = c.get("/login")
for tag in ('rel="manifest"', 'rel="apple-touch-icon" href="/apple-touch-icon.png"', 'name="apple-mobile-web-app-capable"',
            'name="apple-mobile-web-app-status-bar-style"', 'name="theme-color"', "viewport-fit=cover",
            'id="fc-offline"', "serviceWorker", "isSecureContext"):
    assert tag in r.text, tag
for rule in ("env(safe-area-inset-top)", "env(safe-area-inset-bottom)", "touch-action:manipulation",
             "header a{min-height:44px", "overscroll-behavior:none"):
    assert rule in r.text, rule
assert r.text.count('name="theme-color"') == 2   # system theme: one per colour scheme
ok("every page: apple-touch-icon, theme colour, safe areas, 44px header links, offline banner, service worker")

# ---------- service worker and offline page ----------
r = c.get("/sw.js")
assert r.status_code == 200 and "javascript" in r.headers["content-type"] and r.headers["cache-control"] == "no-cache"
assert "'/offline'" in r.text and "mode!=='navigate'" in r.text and ".catch(" in r.text
ok("service worker: caches only /offline and shows it when a page can't be reached")
r = c.get("/offline")
assert r.status_code == 200 and "You're offline" in r.text and "Try again" in r.text and 'href="/"' in r.text
assert not re.search(r'(src|href)="(?!/")', r.text) and "<link" not in r.text and "<script" not in r.text
assert "prefers-color-scheme:dark" in r.text and "min-height:44px" in r.text
ok("offline page: no login, self-contained (no outside files), dark mode, 44px buttons")

# ---------- error pages ----------
r = c.get("/no/such/page", headers=HTML)
assert r.status_code == 404 and "text/html" in r.headers["content-type"] and "Page not found" in r.text
assert 'href="/login"' in r.text and "<header>" in r.text
assert c.get("/no/such/page").json() == {"detail": "Not Found"}   # API callers keep JSON
ok("404: HTML page for browsers (with header), JSON for fetch/API callers")

c.post("/login", data={"username": "touchy", "password": "secret123"})
r = c.get("/no/such/page", headers=HTML)
assert r.status_code == 404 and 'href="/">Decks' in r.text and "Log out" in r.text
r = c.get("/study/notanumber", headers=HTML)
assert r.status_code == 422 and "Something wasn't right" in r.text
assert c.get("/study/notanumber").status_code == 422 and "detail" in c.get("/study/notanumber").json()
r = c.get("/study/1/answer", headers=HTML)
assert r.status_code == 405 and "Can't open this page" in r.text and "allow" in {k.lower() for k in r.headers}
ok("logged in: 404 links to Decks; 422 and 405 are readable pages; 405 keeps its Allow header")


@webapp.app.get("/__boom_for_test")
def boom():
    raise RuntimeError("test failure")


c500 = TestClient(webapp.app, raise_server_exceptions=False)
c500.post("/login", data={"username": "touchy", "password": "secret123"})
r = c500.get("/__boom_for_test", headers=HTML)
assert r.status_code == 500 and "Something went wrong" in r.text and "Check Database" in r.text
assert "test failure" not in r.text   # no internals shown
r = c500.get("/__boom_for_test"); assert r.status_code == 500 and r.text == "Internal Server Error"
ok("500: friendly page without internals for browsers, plain text otherwise")

# ---------- tap to answer (preference) ----------
with webapp.collection("touchy") as col:
    n = col.new_note(col.models.by_name("Basic")); n["Front"] = "maison"; n["Back"] = "house"
    col.add_note(n, 1)
    assert touch.tap_zones(col) is False
r = c.get("/preferences")
assert 'name="tap_zones" value="1"' in r.text and 'name="tap_zones" value="1" checked' not in r.text
r = c.get("/study/1")
assert "TAP=false" in r.text and 'id="taphint"' not in r.text
ok("tap to answer is off by default (study screen has no tap hint)")

r = c.post("/preferences", data={"rollover": "4", "learn_ahead": "20", "timebox": "0", "show_counts": "1",
                                  "show_times": "1", "theme": "system", "tap_zones": "1"})
assert "Preferences saved." in r.text and 'name="tap_zones" value="1" checked' in r.text
with webapp.collection("touchy") as col:
    assert col.get_config(touch.TAP_KEY) is True
    p = col.get_preferences()
    assert p.reviewing.show_remaining_due_counts and p.scheduling.rollover == 4   # other prefs unchanged
r = c.get("/study/1")
assert "TAP=true" in r.text and 'id="taphint"' in r.text and "Tap the card to show the answer" in r.text
for s in ("pointercancel", "tapZones(fr.contentDocument)", "Date.now()-shownAt<400", "clientWidth/2?1:3"):
    assert s in r.text, s
ok("tap to answer on: saved in the collection config, study screen gets tap zones and a hint")

r = c.post("/preferences", data={"rollover": "4", "learn_ahead": "20", "timebox": "0", "theme": "system"})
with webapp.collection("touchy") as col:
    assert col.get_config(touch.TAP_KEY) is False
ok("unticking turns it off again")

# ---------- study screen: double-tap and connection safety ----------
r = c.get("/study/1")
assert "if(!shown||sent)return;sent=true;" in r.text and "Couldn't reach the server" in r.text
assert "html,body{overscroll-behavior:none}" in r.text and "user-select:none" in r.text
assert "env(safe-area-inset-bottom)" in r.text
ok("study screen: one answer per card even on double tap, no pull-to-refresh/overscroll, offline message for actions")

# Answering still works normally.
cid = int(re.search(r'name="card_id" value="(\d+)"', c.get("/study/1").text).group(1))
r = c.post("/study/1/answer", data={"card_id": cid, "rating": 3, "ms": 1000})
with webapp.collection("touchy") as col:
    assert col.get_card(cid).reps == 1
ok("answering still works")
print("ALL TOUCH TESTS PASSED")
