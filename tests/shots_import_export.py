"""Screenshots of the import/export pages at iPad size into shots/ (needs Playwright)."""
import os, sys, tempfile, threading, time
os.environ["DATA_DIR"] = tempfile.mkdtemp()
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import uvicorn
import app as webapp
from playwright.sync_api import sync_playwright

SHOTS = os.path.join(ROOT, "shots")
os.makedirs(SHOTS, exist_ok=True)
srv = uvicorn.Server(uvicorn.Config(webapp.app, port=8770, log_level="error"))
threading.Thread(target=srv.run, daemon=True).start()
time.sleep(1.5)
U = "http://127.0.0.1:8770"
tmp = tempfile.mkdtemp()
csv = os.path.join(tmp, "words.csv")
open(csv, "w").write("front,back,tags\nhola,hello,spanish\nadiós,goodbye,spanish\n\"uno, dos\",\"one, two\",numbers\n")
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 820, "height": 1180})
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(U + "/login"); pg.fill("[name=username]", "demo"); pg.fill("[name=password]", "secret123")
    pg.click("button")
    pg.goto(U + "/import")
    pg.click("text=Options")
    pg.screenshot(path=os.path.join(SHOTS, "import.png"), full_page=True)
    pg.set_input_files("form[action='/import/csv'] input[type=file]", csv)
    pg.click("form[action='/import/csv'] button")
    pg.wait_for_selector("text=Field mapping")
    pg.screenshot(path=os.path.join(SHOTS, "import_csv.png"), full_page=True)
    pg.select_option("[name=map0]", "2"); pg.select_option("[name=map1]", "1")
    pg.click("button[name=go]")
    pg.wait_for_selector("text=Last import")
    pg.click("#log summary")
    pg.screenshot(path=os.path.join(SHOTS, "import_log.png"), full_page=True)
    pg.goto(U + "/export")
    pg.screenshot(path=os.path.join(SHOTS, "export.png"), full_page=True)
    pg.check("input[value=notes]")
    assert pg.is_visible("#scope") and pg.is_visible("input[name=tags]")
    pg.screenshot(path=os.path.join(SHOTS, "export_notes.png"), full_page=True)
    pg.emulate_media(color_scheme="dark")
    pg.reload()
    pg.screenshot(path=os.path.join(SHOTS, "export_dark.png"), full_page=True)
    b.close()
    assert not errors, errors
srv.should_exit = True
print("screenshots saved")
