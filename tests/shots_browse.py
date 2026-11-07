"""Screenshots of the upgraded Browse page at iPad size into shots/ (needs Playwright)."""
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
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 820, "height": 1180})
    pg.goto(U + "/login"); pg.fill("[name=username]", "demo"); pg.fill("[name=password]", "secret123")
    pg.click("button")
    with webapp.collection("demo") as col:
        did = col.decks.id("French")
        for i, (f, b_) in enumerate((("maison", "house"), ("chien", "dog"), ("chat", "cat"), ("oiseau", "bird"))):
            n = col.new_note(col.models.by_name("Basic")); n["Front"] = f; n["Back"] = b_; n.tags = ["fr"]
            col.add_note(n, did)
            if i == 1: col.sched.set_due_date([n.cards()[0].id], "3")
            if i == 2: col.set_user_flag_for_cards(1, [n.cards()[0].id])
            if i == 3: col.sched.suspend_cards([n.cards()[0].id])
    for mode in ("cards", "notes"):
        pg.goto(f"{U}/browse?q=deck%3AFrench&mode={mode}")
        pg.check("input[name=ids] >> nth=0"); pg.select_option("#op", "add_tags")
        pg.screenshot(path=os.path.join(SHOTS, f"browse_{mode}.png"), full_page=True)
    pg.click("text=Find and replace")
    pg.emulate_media(color_scheme="dark")
    pg.screenshot(path=os.path.join(SHOTS, "browse_dark.png"), full_page=True)
    b.close()
srv.should_exit = True
print("screenshots saved")
