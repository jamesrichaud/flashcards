"""Screenshots of undo/redo (header buttons and toast) at iPad size into shots/ (needs Playwright).
Also drives Ctrl+Z / Ctrl+Shift+Z for real and fails on page JS errors."""
import os, sys, tempfile, threading, time
os.environ["DATA_DIR"] = tempfile.mkdtemp()
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import uvicorn
import app as webapp
from playwright.sync_api import sync_playwright

SHOTS = os.path.join(ROOT, "shots")
os.makedirs(SHOTS, exist_ok=True)
srv = uvicorn.Server(uvicorn.Config(webapp.app, port=8768, log_level="error"))
threading.Thread(target=srv.run, daemon=True).start()
time.sleep(1.5)
U = "http://127.0.0.1:8768"
errors = []
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 820, "height": 1180})
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(U + "/login"); pg.fill("[name=username]", "demo"); pg.fill("[name=password]", "secret123")
    pg.click("button")
    pg.goto(U + "/browse")
    with webapp.collection("demo") as col:
        n = col.new_note(col.models.by_name("Basic")); n["Front"] = "maison"; n["Back"] = "house"
        col.add_note(n, 1)
    pg.goto(U + "/")
    pg.screenshot(path=os.path.join(SHOTS, "undo_header.png"))
    pg.keyboard.press("Control+z")
    pg.wait_for_selector("#fc-toast.on")
    assert "Add Note undone" in pg.inner_text("#fc-toast")
    pg.screenshot(path=os.path.join(SHOTS, "undo_toast.png"))
    with webapp.collection("demo") as col:
        assert not col.find_notes("maison")
    pg.keyboard.press("Control+Shift+z")
    pg.wait_for_function("document.getElementById('fc-toast').textContent.includes('redone')")
    with webapp.collection("demo") as col:
        assert col.find_notes("maison")
    pg.emulate_media(color_scheme="dark")
    pg.click("form.fc-ur button")
    pg.wait_for_selector("#fc-toast.on")
    pg.screenshot(path=os.path.join(SHOTS, "undo_dark.png"))
    b.close()
srv.should_exit = True
assert not errors, errors
print("screenshots saved")
