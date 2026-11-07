"""Screenshots of Import from link into shots/ (needs Playwright and a Chromium that starts).
iPad (820x1180) and Chromebook (1366x768), light and dark: the Import page with the link box,
the progress page during a (slow, local) download, and a failed download. Fails on page JS errors."""
import os, sys, tempfile, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
os.environ["DATA_DIR"] = tempfile.mkdtemp()
os.environ["REMINDERS"] = "0"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import uvicorn
import app as webapp
from flashcards import shared_decks as sd
from playwright.sync_api import sync_playwright

SHOTS = os.path.join(ROOT, "shots")
os.makedirs(SHOTS, exist_ok=True)


class Slow(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.startswith("/page"):
            body = b"<html>hello</html>"
            self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers()
            self.wfile.write(body); return
        size = 40 * 1024 * 1024
        self.send_response(200); self.send_header("Content-Length", str(size)); self.end_headers()
        try:
            self.wfile.write(b"PK\x03\x04" + b"\0" * (12 * 1024 * 1024))  # stalls at 12 of 40 MB
            time.sleep(60)
        except OSError:
            pass


files = ThreadingHTTPServer(("127.0.0.1", 0), Slow)
threading.Thread(target=files.serve_forever, daemon=True).start()
FILES = f"http://127.0.0.1:{files.server_address[1]}"
sd.address_allowed = lambda ip: True  # the test file server is on this machine

srv = uvicorn.Server(uvicorn.Config(webapp.app, port=8773, log_level="error"))
threading.Thread(target=srv.run, daemon=True).start()
time.sleep(1.5)
U = "http://127.0.0.1:8773"
errors = []
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 820, "height": 1180})
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(U + "/login"); pg.fill("[name=username]", "demo"); pg.fill("[name=password]", "secret123")
    pg.click("button")
    pg.goto(U + "/import")
    pg.fill("input[name=url]", FILES + "/Spanish%20Verbs.apkg")
    pg.click("button:text('Download and import')")
    pg.wait_for_function("document.getElementById('dltext').textContent.indexOf('12.0 of 40.0') >= 0", timeout=15000)
    progress_url = pg.url
    pg.goto(U + "/import")
    pg.fill("input[name=url]", FILES + "/page.html")
    pg.click("button:text('Download and import')")
    pg.wait_for_selector("#dlback:visible", timeout=15000)
    error_url = pg.url
    for size, (w, h) in (("ipad", (820, 1180)), ("chromebook", (1366, 768))):
        pg.set_viewport_size({"width": w, "height": h})
        for scheme in ("light", "dark"):
            pg.emulate_media(color_scheme=scheme)
            pg.goto(U + "/import")
            pg.screenshot(path=os.path.join(SHOTS, f"import_link_{size}_{scheme}.png"), full_page=True)
            pg.goto(progress_url); pg.wait_for_timeout(900)
            pg.screenshot(path=os.path.join(SHOTS, f"import_link_progress_{size}_{scheme}.png"), full_page=True)
            pg.goto(error_url)
            pg.screenshot(path=os.path.join(SHOTS, f"import_link_error_{size}_{scheme}.png"), full_page=True)
    b.close()
srv.should_exit = True
files.shutdown()
assert not errors, errors
print("screenshots saved")
