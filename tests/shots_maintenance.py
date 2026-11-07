"""Screenshots of Backups, restore confirm, Check Database and Empty Cards at iPad size into shots/
(needs Playwright). Fails on page JS errors."""
import os, sys, tempfile, threading, time
os.environ["DATA_DIR"] = tempfile.mkdtemp()
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import uvicorn
import app as webapp
from flashcards import maintenance as mt
from playwright.sync_api import sync_playwright

SHOTS = os.path.join(ROOT, "shots")
os.makedirs(SHOTS, exist_ok=True)
srv = uvicorn.Server(uvicorn.Config(webapp.app, port=8770, log_level="error"))
threading.Thread(target=srv.run, daemon=True).start()
time.sleep(1.5)
U = "http://127.0.0.1:8770"
errors = []
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 820, "height": 1180})
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.on("dialog", lambda d: d.accept())
    pg.goto(U + "/login"); pg.fill("[name=username]", "demo"); pg.fill("[name=password]", "secret123")
    pg.click("button")
    with webapp.collection("demo") as col:
        opt = col.models.by_name("Basic (optional reversed card)")
        n = col.new_note(opt); n["Front"] = "maison"; n["Back"] = "house"; n["Add Reverse"] = "y"
        col.add_note(n, 1); n["Add Reverse"] = ""; col.update_note(n)
        mt.make_backup("demo", col)
    pg.goto(U + "/settings")
    pg.screenshot(path=os.path.join(SHOTS, "maintenance_settings.png"), full_page=True)
    pg.goto(U + "/backups"); pg.click("text=Back up now"); pg.wait_for_selector(".flash")
    pg.screenshot(path=os.path.join(SHOTS, "backups.png"), full_page=True)
    pg.click("text=Restore…")
    pg.screenshot(path=os.path.join(SHOTS, "backups_restore.png"), full_page=True)
    pg.goto(U + "/check-database"); pg.click("button.primary"); pg.wait_for_selector("#result")
    pg.screenshot(path=os.path.join(SHOTS, "check_database.png"), full_page=True)
    pg.goto(U + "/empty-cards")
    pg.screenshot(path=os.path.join(SHOTS, "empty_cards.png"), full_page=True)
    pg.click("button.danger"); pg.wait_for_selector(".flash")
    pg.emulate_media(color_scheme="dark")
    pg.screenshot(path=os.path.join(SHOTS, "empty_cards_dark.png"), full_page=True)
    b.close()
srv.should_exit = True
assert not errors, errors
print("screenshots saved")
