"""Screenshots of the deck list, overview (description, move) and description editor at iPad size
into shots/ (needs Playwright). Fails on page JS errors."""
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
    with webapp.collection("demo") as col:
        for name in ("Languages::French::Verbs", "Languages::French::Nouns", "Languages::Spanish",
                     "Science::Physics", "Science::Chemistry", "History"):
            did = col.decks.id(name)
            for i in range(3):
                n = col.new_note(col.models.by_name("Basic")); n["Front"] = f"{name} {i}"; n["Back"] = "x"
                col.add_note(n, did)
        sci = col.decks.id_for_name("Science"); lang = col.decks.id_for_name("Languages")
        from anki.decks import DeckCollapseScope
        col.decks.set_collapsed(lang, False, DeckCollapseScope.REVIEWER)
        col.decks.set_collapsed(sci, True, DeckCollapseScope.REVIEWER)
        d = col.decks.get(sci)
        d["desc"] = "<b>Physics and chemistry</b> for the exam.<br>Study 20 minutes a day."
        col.decks.update_dict(d)
    pg.goto(U + "/")
    pg.screenshot(path=os.path.join(SHOTS, "deck_list.png"), full_page=True)
    pg.click('button[aria-label="Expand Science"]')
    pg.wait_for_load_state()
    assert pg.is_visible("text=Physics")
    pg.goto(U + f"/deck/{sci}")
    pg.screenshot(path=os.path.join(SHOTS, "deck_overview_description.png"), full_page=True)
    pg.click("text=Description")
    pg.wait_for_load_state()
    pg.screenshot(path=os.path.join(SHOTS, "deck_description.png"), full_page=True)
    pg.emulate_media(color_scheme="dark")
    pg.goto(U + "/")
    pg.screenshot(path=os.path.join(SHOTS, "deck_list_dark.png"), full_page=True)
    b.close()
srv.should_exit = True
assert not errors, errors
print("screenshots saved")
