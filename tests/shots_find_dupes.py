"""Screenshots of Find Duplicates into shots/ (needs Playwright and a Chromium that starts).
iPad (820x1180) and Chromebook (1366x768), light and dark. Opens it from Browse, runs a search,
taps Tag Duplicates, and fails on page JS errors."""
import os, sys, tempfile, threading, time
os.environ["DATA_DIR"] = tempfile.mkdtemp()
os.environ["REMINDERS"] = "0"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import uvicorn
import app as webapp
from playwright.sync_api import sync_playwright

SHOTS = os.path.join(ROOT, "shots")
os.makedirs(SHOTS, exist_ok=True)
srv = uvicorn.Server(uvicorn.Config(webapp.app, port=8772, log_level="error"))
threading.Thread(target=srv.run, daemon=True).start()
time.sleep(1.5)
U = "http://127.0.0.1:8772"
errors = []
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 820, "height": 1180})
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(U + "/login"); pg.fill("[name=username]", "demo"); pg.fill("[name=password]", "secret123")
    pg.click("button")
    with webapp.collection("demo") as col:
        fr, es = col.decks.id("French"), col.decks.id("Spanish")
        for f, did in (("chat", fr), ("<b>chat</b>", fr), ("maison", fr), ("maison", es), ("maison", es), ("perro", es)):
            n = col.new_note(col.models.by_name("Basic")); n["Front"] = f; n["Back"] = "x"; col.add_note(n, did)
    pg.goto(U + "/browse")
    pg.click("text=Find duplicates")
    pg.select_option("select[name=field]", "Front")
    pg.click("button:text('Search')")
    assert pg.locator(".dgroup").count() == 2
    pg.click("button:text('Tag Duplicates')")
    for size, (w, h) in (("ipad", (820, 1180)), ("chromebook", (1366, 768))):
        pg.set_viewport_size({"width": w, "height": h})
        for scheme in ("light", "dark"):
            pg.emulate_media(color_scheme=scheme)
            pg.goto(f"{U}/browse/dupes?field=Front&q=")
            pg.screenshot(path=os.path.join(SHOTS, f"find_dupes_{size}_{scheme}.png"), full_page=True)
    b.close()
srv.should_exit = True
assert not errors, errors
print("screenshots saved")
