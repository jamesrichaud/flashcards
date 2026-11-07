"""Study reminders screenshots into shots/ (needs Playwright + Chromium): the Reminders page at
iPad 820x1180 in light and dark, on http://127.0.0.1 (a secure context, so the buttons are live)
with a device in the list and cards due. Fails on page JS errors."""
import os, sys, tempfile, threading, time
os.environ["DATA_DIR"] = tempfile.mkdtemp()
os.environ["REMINDERS"] = "0"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import uvicorn
import app as webapp
from flashcards import reminders
from playwright.sync_api import sync_playwright

SHOTS = os.path.join(ROOT, "shots")
os.makedirs(SHOTS, exist_ok=True)
srv = uvicorn.Server(uvicorn.Config(webapp.app, port=8773, log_level="error"))
threading.Thread(target=srv.run, daemon=True).start()
time.sleep(1.5)
U = "http://127.0.0.1:8773"
errors = []

with sync_playwright() as p:
    b = p.chromium.launch()
    for scheme in ("light", "dark"):
        ctx = b.new_context(viewport={"width": 820, "height": 1180}, has_touch=True, color_scheme=scheme)
        pg = ctx.new_page(); pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(U + "/login"); pg.fill("[name=username]", "demo"); pg.fill("[name=password]", "secret123")
        pg.click("button")
        with webapp.collection("demo") as col:
            if not col.find_cards(""):
                for i in range(5):
                    n = col.new_note(col.models.by_name("Basic")); n["Front"] = f"q{i}"; n["Back"] = "a"
                    col.add_note(n, 1)
        reminders.update("demo", lambda c: c.update(on=True, time="19:30", days=[0, 1, 2, 3, 4], subscriptions=[
            {"endpoint": "https://web.push.apple.com/x", "keys": {"p256dh": "k", "auth": "a"}, "origin": U,
             "ua": "Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) Safari/604.1", "added": "2026-11-07"}]))
        pg.goto(U + "/reminders"); pg.wait_for_timeout(800)
        assert pg.is_visible("#rmon") or pg.is_visible("#rmoff") or pg.is_visible("#rmstate")
        pg.screenshot(path=os.path.join(SHOTS, f"reminders-{scheme}.png"), full_page=True)
        assert pg.evaluate("document.documentElement.scrollWidth <= innerWidth")
        ctx.close()
    b.close()
assert not errors, errors
print("screenshots in shots/reminders-*.png")
