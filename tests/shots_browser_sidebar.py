"""Screenshots of the Browse sidebar into shots/ (needs Playwright and a Chromium that starts).
iPad (820x1180: slide-out panel, opened) and Chromebook (1366x768: beside the list), light and dark.
Also clicks a tag, opens/closes a deck, and fails on page JS errors."""
import os, sys, tempfile, threading, time
os.environ["DATA_DIR"] = tempfile.mkdtemp()
os.environ["REMINDERS"] = "0"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import uvicorn
import app as webapp
from playwright.sync_api import sync_playwright

SHOTS = os.path.join(ROOT, "shots")
os.makedirs(SHOTS, exist_ok=True)
srv = uvicorn.Server(uvicorn.Config(webapp.app, port=8771, log_level="error"))
threading.Thread(target=srv.run, daemon=True).start()
time.sleep(1.5)
U = "http://127.0.0.1:8771"
errors = []
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1366, "height": 768})
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(U + "/login"); pg.fill("[name=username]", "demo"); pg.fill("[name=password]", "secret123")
    pg.click("button")
    with webapp.collection("demo") as col:
        did = col.decks.id("French::Verbs")
        for f, tags in (("aller", ["lang::fr::verb"]), ("être", ["lang::fr::verb", "top"]), ("maison", ["lang::fr::noun"])):
            n = col.new_note(col.models.by_name("Basic")); n["Front"] = f; n["Back"] = "x"; n.tags = tags
            col.add_note(n, did)
        col.set_config("savedFilters", {"French verbs": "deck:French tag:lang::fr::verb"})
    for scheme in ("light", "dark"):
        pg.emulate_media(color_scheme=scheme)
        pg.goto(f"{U}/browse?q=deck%3AFrench")
        pg.screenshot(path=os.path.join(SHOTS, f"browse_sidebar_chromebook_{scheme}.png"))
    pg.click("a.sbl:text-is('lang')")
    assert "tag%3Alang" in pg.url, pg.url
    pg.click(".sbt[data-tag='lang']")
    pg.wait_for_timeout(300)
    pg.reload()
    assert pg.locator(".sbt[data-tag='lang']").get_attribute("aria-expanded") == "true"
    pg.set_viewport_size({"width": 820, "height": 1180})
    for scheme in ("light", "dark"):
        pg.emulate_media(color_scheme=scheme)
        pg.goto(f"{U}/browse?q=deck%3AFrench")
        assert not pg.locator(".sb").is_visible()
        pg.click(".sbopen"); pg.wait_for_timeout(300)
        pg.click(".sbm[data-name='lang::fr']")
        pg.screenshot(path=os.path.join(SHOTS, f"browse_sidebar_ipad_{scheme}.png"))
    b.close()
srv.should_exit = True
assert not errors, errors
print("screenshots saved")
