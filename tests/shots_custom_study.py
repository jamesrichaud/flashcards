"""Screenshots of the custom study page at iPad size into shots/ (needs Playwright)."""
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
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 820, "height": 1180})
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(U + "/login"); pg.fill("[name=username]", "demo"); pg.fill("[name=password]", "secret123")
    pg.click("button")
    with webapp.collection("demo") as col:
        did = col.decks.id("French")
        nt = col.models.by_name("Basic")
        for i, tag in enumerate(["verbs", "nouns", "verbs"]):
            n = col.new_note(nt); n["Front"], n["Back"] = f"mot {i}", "word"; n.tags = [tag]
            col.add_note(n, did)
    pg.goto(f"{U}/deck/{did}/custom")
    pg.screenshot(path=os.path.join(SHOTS, "custom_study.png"), full_page=True)
    pg.check("input[value=cram]")
    pg.screenshot(path=os.path.join(SHOTS, "custom_study_cram.png"), full_page=True)
    pg.click("button.primary")
    pg.screenshot(path=os.path.join(SHOTS, "custom_study_session.png"), full_page=True)
    pg.emulate_media(color_scheme="dark")
    pg.goto(f"{U}/deck/{did}/custom")
    pg.screenshot(path=os.path.join(SHOTS, "custom_study_dark.png"), full_page=True)
    b.close()
    assert not errors, errors
srv.should_exit = True
print("screenshots saved")
