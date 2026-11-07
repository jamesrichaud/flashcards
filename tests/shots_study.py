"""Screenshots of the study screen (type-in answer, audio replay) at iPad size into shots/.
Also fails loudly if the page's JavaScript has an error. Needs Playwright + Chromium."""
import os, sys, tempfile, threading, time
os.environ["DATA_DIR"] = tempfile.mkdtemp()
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import uvicorn
import app as webapp
from playwright.sync_api import sync_playwright

SHOTS = os.path.join(ROOT, "shots")
os.makedirs(SHOTS, exist_ok=True)
srv = uvicorn.Server(uvicorn.Config(webapp.app, port=8767, log_level="error"))
threading.Thread(target=srv.run, daemon=True).start()
time.sleep(1.5)
U = "http://127.0.0.1:8767"
errors = []
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 820, "height": 1180})
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(U + "/login"); pg.fill("[name=username]", "demo"); pg.fill("[name=password]", "secret123")
    pg.click("button")
    with webapp.collection("demo") as col:
        did = col.decks.id("Typing")
        n = col.new_note(col.models.by_name("Basic (type in the answer)"))
        n["Front"] = "chien [sound:chien.mp3]"; n["Back"] = "dog"
        col.add_note(n, did)
    pg.goto(f"{U}/study/{did}"); time.sleep(0.5)
    pg.screenshot(path=os.path.join(SHOTS, "study_type_question.png"), full_page=True)
    pg.frame_locator("#frame").locator("#typeans").fill("dgo")
    pg.frame_locator("#frame").locator("#typeans").press("Enter"); time.sleep(0.8)
    assert pg.frame_locator("#frame").locator("code#typeans").count() == 1, "comparison not shown"
    pg.screenshot(path=os.path.join(SHOTS, "study_type_answer.png"), full_page=True)
    pg.emulate_media(color_scheme="dark")
    pg.screenshot(path=os.path.join(SHOTS, "study_type_answer_dark.png"), full_page=True)
    b.close()
srv.should_exit = True
assert not errors, errors
print("screenshots saved")
