"""Screenshots of the card info page at iPad size into shots/ (needs Playwright)."""
import os, sys, tempfile, threading, time
os.environ["DATA_DIR"] = tempfile.mkdtemp()
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import uvicorn
import app as webapp
from anki.scheduler.v3 import CardAnswer
from playwright.sync_api import sync_playwright

SHOTS = os.path.join(ROOT, "shots")
os.makedirs(SHOTS, exist_ok=True)
srv = uvicorn.Server(uvicorn.Config(webapp.app, port=8766, log_level="error"))
threading.Thread(target=srv.run, daemon=True).start()
time.sleep(1.5)
U = "http://127.0.0.1:8766"
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 820, "height": 1180})
    pg.goto(U + "/login"); pg.fill("[name=username]", "demo"); pg.fill("[name=password]", "secret123")
    pg.click("button")
    with webapp.collection("demo") as col:
        did = col.decks.id("French")
        n = col.new_note(col.models.by_name("Basic")); n["Front"] = "maison"; n["Back"] = "house"
        col.add_note(n, did)
        col.decks.select(did)
        for r in (CardAnswer.AGAIN, CardAnswer.GOOD, CardAnswer.GOOD):
            qc = col.sched.get_queued_cards().cards[0]; card = col.get_card(qc.card.id); card.start_timer()
            col.sched.answer_card(col.sched.build_answer(card=card, states=qc.states, rating=r))
        cid = n.cards()[0].id
    pg.goto(f"{U}/card/{cid}/info?back=/browse")
    pg.screenshot(path=os.path.join(SHOTS, "card_info.png"), full_page=True)
    pg.emulate_media(color_scheme="dark")
    pg.reload()
    pg.screenshot(path=os.path.join(SHOTS, "card_info_dark.png"), full_page=True)
    b.close()
srv.should_exit = True
print("screenshots saved")
