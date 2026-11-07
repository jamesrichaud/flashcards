"""Screenshots of the card-action UI at iPad size into shots/ (needs Playwright)."""
import os, sys, tempfile, threading, time
os.environ["DATA_DIR"] = tempfile.mkdtemp()
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import uvicorn
import app as webapp
from playwright.sync_api import sync_playwright

SHOTS = os.path.join(ROOT, "shots")
os.makedirs(SHOTS, exist_ok=True)
srv = uvicorn.Server(uvicorn.Config(webapp.app, port=8765, log_level="error"))
threading.Thread(target=srv.run, daemon=True).start()
time.sleep(1.5)
U = "http://127.0.0.1:8765"
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 820, "height": 1180})
    pg.goto(U + "/login"); pg.fill("[name=username]", "demo"); pg.fill("[name=password]", "secret123")
    pg.click("button")
    with webapp.collection("demo") as col:
        did = col.decks.id("French")
        m = col.models.by_name("Basic (and reversed card)")
        for a, bb in [("chien", "dog"), ("chat", "cat"), ("oiseau", "bird")]:
            n = col.new_note(m); n["Front"] = a; n["Back"] = bb; col.add_note(n, did)
        nids = sorted(col.find_notes(""))
        col.set_user_flag_for_cards(1, [col.card_ids_of_note(nids[1])[0]])
        col.sched.suspend_cards(col.card_ids_of_note(nids[2]))
    pg.goto(f"{U}/study/{did}")
    pg.keyboard.press("Control+4"); time.sleep(0.4)
    pg.keyboard.press("*"); time.sleep(0.4)
    pg.click("summary"); time.sleep(0.3)
    pg.screenshot(path=os.path.join(SHOTS, "study_actions.png"))
    pg.goto(f"{U}/edit/{nids[1]}?back=/browse")
    pg.screenshot(path=os.path.join(SHOTS, "edit_cards.png"), full_page=True)
    pg.goto(U + "/browse")
    pg.screenshot(path=os.path.join(SHOTS, "browse_states.png"))
    pg.emulate_media(color_scheme="dark")
    pg.goto(f"{U}/study/{did}"); pg.click("summary"); time.sleep(0.3)
    pg.screenshot(path=os.path.join(SHOTS, "study_actions_dark.png"))
    b.close()
srv.should_exit = True
print("screenshots saved")
