"""Screenshots of Preferences and the themed study screen at iPad size into shots/ (needs Playwright).
Checks that the forced themes win over the device setting and fails on page JS errors."""
import os, sys, tempfile, threading, time
os.environ["DATA_DIR"] = tempfile.mkdtemp()
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import uvicorn
import app as webapp
from playwright.sync_api import sync_playwright

SHOTS = os.path.join(ROOT, "shots")
os.makedirs(SHOTS, exist_ok=True)
srv = uvicorn.Server(uvicorn.Config(webapp.app, port=8769, log_level="error"))
threading.Thread(target=srv.run, daemon=True).start()
time.sleep(1.5)
U = "http://127.0.0.1:8769"
errors = []
bg = "getComputedStyle(document.body).backgroundColor"
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 820, "height": 1180})
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(U + "/login"); pg.fill("[name=username]", "demo"); pg.fill("[name=password]", "secret123")
    pg.click("button")
    with webapp.collection("demo") as col:
        n = col.new_note(col.models.by_name("Basic")); n["Front"] = "maison"; n["Back"] = "house"
        col.add_note(n, 1)
    pg.goto(U + "/preferences")
    pg.screenshot(path=os.path.join(SHOTS, "preferences.png"), full_page=True)
    # Dark theme on a light device.
    pg.check("input[name=theme][value=dark]"); pg.uncheck("input[name=show_times]"); pg.click("button.primary")
    pg.wait_for_selector(".flash")
    assert pg.evaluate(bg) == "rgb(22, 22, 23)", pg.evaluate(bg)
    pg.screenshot(path=os.path.join(SHOTS, "preferences_dark.png"), full_page=True)
    pg.goto(U + "/study/1"); pg.click("#show")
    pg.screenshot(path=os.path.join(SHOTS, "preferences_study_dark.png"))
    # Light theme on a dark device.
    pg.emulate_media(color_scheme="dark")
    pg.goto(U + "/preferences"); pg.check("input[name=theme][value=light]"); pg.click("button.primary")
    pg.wait_for_selector(".flash")
    assert pg.evaluate(bg) == "rgb(246, 246, 244)", pg.evaluate(bg)
    b.close()
srv.should_exit = True
assert not errors, errors
print("screenshots saved")
