"""Screenshots of the offline switch (deck overview) and Preferences > Offline at iPad (820x1180) and
Chromebook (1366x768) sizes, light and dark, into shots/ (needs Playwright). Turns a deck on, waits
for the background download (IndexedDB + Cache Storage on http://127.0.0.1, a secure context),
checks the "Offline ready" line, the stored cards and media, then removes the copy. Fails on page JS
errors."""
import os, sys, tempfile, threading, time
os.environ["DATA_DIR"] = tempfile.mkdtemp()
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import uvicorn
import app as webapp
from playwright.sync_api import sync_playwright

SHOTS = os.path.join(ROOT, "shots")
os.makedirs(SHOTS, exist_ok=True)
srv = uvicorn.Server(uvicorn.Config(webapp.app, port=8774, log_level="error"))
threading.Thread(target=srv.run, daemon=True).start()
time.sleep(1.5)
U = "http://127.0.0.1:8774"
errors = []
with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 820, "height": 1180})
    pg = ctx.new_page()
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(U + "/login"); pg.fill("[name=username]", "demo"); pg.fill("[name=password]", "secret123")
    pg.click("button")
    with webapp.collection("demo") as col:
        did = col.decks.id("French")
        with open(os.path.join(col.media.dir(), "dot.png"), "wb") as f:
            f.write(webapp.touch_mod.icon_png(64))
        for i in range(30):
            n = col.new_note(col.models.by_name("Basic"))
            n["Front"] = f"maison {i} <img src=\"dot.png\">"; n["Back"] = "house"
            col.add_note(n, did)
    pg.goto(f"{U}/deck/{did}")
    pg.screenshot(path=os.path.join(SHOTS, "offline_overview_off.png"), full_page=True)
    pg.click(".fc-switch")
    pg.wait_for_selector(".flash")
    pg.wait_for_function("document.getElementById('fc-off-status').textContent.startsWith('Offline ready')",
                         timeout=30000)
    stored = pg.evaluate("""async () => { const k = await window.FCOffline.deckInfo(%d);
      const c = await caches.open('fc-media:demo'); return [k.cardIds.length, (await c.keys()).length]; }""" % did)
    assert stored == [30, 1], stored
    for name, size, scheme in (("ipad", (820, 1180), "light"), ("ipad_dark", (820, 1180), "dark"),
                               ("chromebook", (1366, 768), "light"), ("chromebook_dark", (1366, 768), "dark")):
        pg.set_viewport_size({"width": size[0], "height": size[1]}); pg.emulate_media(color_scheme=scheme)
        pg.goto(f"{U}/deck/{did}")
        pg.wait_for_function("document.getElementById('fc-off-status').textContent.startsWith('Offline ready')")
        pg.screenshot(path=os.path.join(SHOTS, f"offline_overview_{name}.png"), full_page=True)
        pg.goto(U + "/preferences")
        pg.wait_for_function("document.getElementById('fc-off-list').textContent.includes('French')")
        pg.locator("#fc-off-prefs").scroll_into_view_if_needed()
        pg.screenshot(path=os.path.join(SHOTS, f"offline_prefs_{name}.png"))
    pg.goto(f"{U}/deck/{did}")
    pg.click("#fc-off-remove button")
    pg.wait_for_selector(".flash")
    pg.wait_for_function("async () => !(await window.FCOffline.deckInfo(%d))" % did, timeout=30000)
    b.close()
srv.should_exit = True
assert not errors, errors
print("screenshots saved")
