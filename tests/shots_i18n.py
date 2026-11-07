"""Screenshots of the main pages in other languages at iPad size into shots/ (needs Playwright).
Long words (German, Irish, Polish) are where layouts break, so those get the most pages.
Fails on page JS errors."""
import os, sys, tempfile, threading, time
os.environ["DATA_DIR"] = tempfile.mkdtemp()
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
    pg = b.new_page(viewport={"width": 820, "height": 1180})
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(U + "/login"); pg.fill("[name=username]", "demo"); pg.fill("[name=password]", "secret123")
    pg.click("button")
    with webapp.collection("demo") as col:
        did = col.decks.id("Français")
        for q, a in [("maison", "house"), ("chien", "dog")]:
            n = col.new_note(col.models.by_name("Basic")); n["Front"] = q; n["Back"] = a
            col.add_note(n, did)
    for code in ("de", "ga", "pl", "es", "fr", "it", "pt"):
        webapp.set_user_lang("demo", code)
        webapp.reopen_collection("demo")
        pages = [("decks", "/"), ("study", f"/study/{did}"), ("settings", "/settings"), ("preferences", "/preferences")]
        if code in ("de", "ga", "pl"):
            pages += [("browse", "/browse"), ("add", "/add"), ("options", f"/deck/{did}/options"), ("stats", "/stats")]
        for name, url in pages:
            pg.goto(U + url)
            if name == "study":
                pg.click("#show")
            pg.screenshot(path=os.path.join(SHOTS, f"i18n_{code}_{name}.png"), full_page=name != "study")
    b.close()
srv.should_exit = True
assert not errors, errors
print("screenshots saved")
