"""Screenshots of the Statistics page at iPad size into shots/ (needs Playwright). Fails on page JS errors."""
import os, sys, tempfile, threading, time
os.environ["DATA_DIR"] = tempfile.mkdtemp()
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import random
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
        rnd = random.Random(1)
        did = col.decks.id("Spanish")
        for i in range(120):
            n = col.new_note(col.models.by_name("Basic")); n["Front"] = f"word {i}"; n["Back"] = "x"
            col.add_note(n, did)
        cids = col.find_cards(f"did:{did}")
        col.sched.set_due_date(cids[:80], "1-60")
        cutoff = col.sched.day_cutoff
        j = 0
        for day in range(300):
            if rnd.random() < 0.3:
                continue
            for _ in range(rnd.randint(5, 40)):
                j += 1
                rid = (cutoff - 86400 * day - rnd.randint(3600, 80000)) * 1000 + j
                col.db.execute("insert into revlog (id,cid,usn,ease,ivl,lastIvl,factor,time,type) "
                               "values (?,?,?,?,?,?,?,?,?)", rid, rnd.choice(cids), -1, rnd.choice([1, 3, 3, 3, 4]),
                               5, 2, 2500, rnd.randint(2000, 20000), rnd.choice([0, 1, 1, 1, 2]))
        col.sched.suspend_cards(cids[100:105])
    pg.goto(U + f"/stats?did={did}&range=3m")
    pg.screenshot(path=os.path.join(SHOTS, "stats.png"), full_page=True)
    # Tapping a bar shows its details under the chart.
    pg.click("section:has(h3:text-is('Future Due')) rect.hit:visible >> nth=3")
    assert pg.inner_text("section:has(h3:text-is('Future Due')) .tip").strip(), "tap shows no details"
    pg.goto(U + f"/stats?did={did}&range=1y")
    pg.click("text=Time")
    pg.screenshot(path=os.path.join(SHOTS, "stats_year_time.png"), full_page=True)
    pg.emulate_media(color_scheme="dark")
    pg.reload()
    pg.screenshot(path=os.path.join(SHOTS, "stats_dark.png"), full_page=True)
    b.close()
srv.should_exit = True
assert not errors, errors
print("screenshots saved")
