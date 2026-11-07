"""Screenshots of the second set of bundled add-ons (overview stats, tag filter, keyboard shortcuts,
Browse's Copy fields panel, simulator) at iPad (820x1180) and Chromebook (1366x768) sizes, light and
dark, into shots/ (needs Playwright + Chromium). Fails on page JS errors."""
import os, sys, tempfile, threading, time
os.environ["DATA_DIR"] = tempfile.mkdtemp()
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import uvicorn
from fastapi.testclient import TestClient
import app as webapp
from playwright.sync_api import sync_playwright

SHOTS = os.path.join(ROOT, "shots")
os.makedirs(SHOTS, exist_ok=True)
c = TestClient(webapp.app)
c.post("/login", data={"username": "demo", "password": "secret123"})
for aid in ("keyboard_shortcuts", "overview_stats", "copy_fields", "tag_filter", "simulator"):
    c.post(f"/addons/{aid}/enable")
with webapp.collection("demo") as col:
    did = col.decks.id("Spanish")
    for i in range(40):
        n = col.new_note(col.models.by_name("Basic")); n["Front"] = f"palabra {i}"; n["Back"] = f"word {i}"
        n.tags = [["food::fruit", "food::veg", "verbs::ar", "verbs::er", "grammar"][i % 5]]
        col.add_note(n, did)
    col.sched.set_due_date(list(col.find_cards("deck:Spanish"))[:15], "0-20!")
c.post("/addon/keyboard_shortcuts/", data={"bury": "B", "undo": "U"})

srv = uvicorn.Server(uvicorn.Config(webapp.app, port=8772, log_level="error"))
threading.Thread(target=srv.run, daemon=True).start()
time.sleep(1.5)
U = "http://127.0.0.1:8772"
PAGES = {"addon-overview": f"/deck/{did}", "addon-keys": "/addon/keyboard_shortcuts/",
         "addon-simulator": f"/addon/simulator/?did={did}&run=1", "addon-copyfields": "/browse?q=deck:Spanish"}
errors = []
with sync_playwright() as p:
    b = p.chromium.launch()
    for size, (w, hgt) in {"ipad": (820, 1180), "chromebook": (1366, 768)}.items():
        for scheme in ("light", "dark"):
            pg = b.new_page(viewport={"width": w, "height": hgt}, color_scheme=scheme)
            pg.on("pageerror", lambda e: errors.append(str(e)))
            pg.goto(U + "/login"); pg.fill("[name=username]", "demo"); pg.fill("[name=password]", "secret123")
            pg.click("button")
            for name, url in PAGES.items():
                pg.goto(U + url)
                if name == "addon-copyfields":
                    pg.click("details.acf summary")
                pg.screenshot(path=os.path.join(SHOTS, f"{name}-{size}{'-dark' if scheme == 'dark' else ''}.png"),
                              full_page=True)
            pg.close()
    b.close()
srv.should_exit = True
assert not errors, errors
print("shots saved")
