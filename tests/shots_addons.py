"""Screenshots of the Add-ons page and an add-on's own page at iPad size into shots/ (needs Playwright).
Fails on page JS errors."""
import os, shutil, sys, tempfile, threading, time
os.environ["DATA_DIR"] = tempfile.mkdtemp()
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import uvicorn
import app as webapp
from playwright.sync_api import sync_playwright

SHOTS = os.path.join(ROOT, "shots")
os.makedirs(SHOTS, exist_ok=True)
adir = os.path.join(os.environ["DATA_DIR"], "addons")
os.makedirs(adir)
shutil.copy(os.path.join(ROOT, "addons_examples", "answer_counter.py"), adir)
open(os.path.join(adir, "typo.py"), "w").write('"""Has a mistake."""\ndef oops(:\n')
os.makedirs(os.path.join(adir, "1234567"))
open(os.path.join(adir, "1234567", "__init__.py"), "w").write("from aqt import mw\n")
srv = uvicorn.Server(uvicorn.Config(webapp.app, port=8771, log_level="error"))
threading.Thread(target=srv.run, daemon=True).start()
time.sleep(1.5)
U = "http://127.0.0.1:8771"
errors = []
with sync_playwright() as p:
    b = p.chromium.launch()
    for scheme in ("light", "dark"):
        pg = b.new_page(viewport={"width": 820, "height": 1180}, color_scheme=scheme)
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(U + "/login"); pg.fill("[name=username]", "demo"); pg.fill("[name=password]", "secret123")
        pg.click("button")
        pg.goto(U + "/addons")
        if scheme == "light":
            pg.click("#addon-answer_counter button")
        pg.screenshot(path=os.path.join(SHOTS, f"addons{'-dark' if scheme == 'dark' else ''}.png"), full_page=True)
        pg.goto(U + "/addon/answer_counter/")
        pg.screenshot(path=os.path.join(SHOTS, f"addon-page{'-dark' if scheme == 'dark' else ''}.png"))
    b.close()
srv.should_exit = True
assert not errors, errors
print("shots saved")
