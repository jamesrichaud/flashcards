"""Screenshots of the Sync page (logged out, logged in, full sync choice) at iPad size into
shots/ (needs Playwright), and a real check of the live refresh between two browsers.
Fails on page JS errors."""
import os, socket, subprocess, sys, tempfile, threading, time
os.environ["DATA_DIR"] = tempfile.mkdtemp()
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import uvicorn
import app as webapp
from playwright.sync_api import sync_playwright

SHOTS = os.path.join(ROOT, "shots")
os.makedirs(SHOTS, exist_ok=True)
srv = uvicorn.Server(uvicorn.Config(webapp.app, port=8771, log_level="error"))
threading.Thread(target=srv.run, daemon=True).start()
s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
sync = subprocess.Popen([sys.executable, "-m", "anki.syncserver"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        env=dict(os.environ, SYNC_BASE=tempfile.mkdtemp(), SYNC_HOST="127.0.0.1",
                                 SYNC_PORT=str(port), SYNC_USER1="demo:syncpw"))
time.sleep(1.5)
U = "http://127.0.0.1:8771"
errors = []
try:
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 820, "height": 1180})
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(U + "/login"); pg.fill("[name=username]", "demo"); pg.fill("[name=password]", "secret123")
        pg.click("button")
        pg.goto(U + "/sync")
        pg.screenshot(path=os.path.join(SHOTS, "sync_login.png"), full_page=True)
        pg.fill("[name=server]", f"http://127.0.0.1:{port}/"); pg.fill("[name=username]", "demo")
        pg.fill("[name=password]", "syncpw"); pg.click("button.primary"); pg.wait_for_selector(".flash")
        pg.screenshot(path=os.path.join(SHOTS, "sync.png"), full_page=True)
        pg.click("text=Sync now"); pg.wait_for_url("**/sync/full")
        pg.screenshot(path=os.path.join(SHOTS, "sync_full.png"), full_page=True)
        pg.check("[name=confirm]"); pg.click("text=Upload to the server"); pg.wait_for_selector(".flash")
        pg.emulate_media(color_scheme="dark")
        pg.screenshot(path=os.path.join(SHOTS, "sync_dark.png"), full_page=True)
        # Live refresh: a second browser changes something; the first notices when shown again.
        pg.goto(U + "/")
        other = b.new_page(viewport={"width": 1366, "height": 768})
        other.goto(U + "/login"); other.fill("[name=username]", "demo"); other.fill("[name=password]", "secret123")
        other.click("button"); other.fill("[name=name]", "Made on the Chromebook"); other.click("text=Create deck")
        pg.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
        pg.wait_for_selector("text=Made on the Chromebook", timeout=5000)
        pg.screenshot(path=os.path.join(SHOTS, "sync_live_refresh.png"), full_page=True)
        b.close()
finally:
    sync.terminate()
    srv.should_exit = True
assert not errors, errors
print("screenshots saved")
