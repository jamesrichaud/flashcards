"""iPad/Chromebook polish screenshots into shots/ (needs Playwright), plus checks a browser can do:
every visible button/link/input on the main pages is at least 44px tall (checkbox and radio rows
are measured by their label), tap zones answer Again/Good, a swipe does nothing, the error and
offline pages render. Fails on page JS errors."""
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
errors, small = [], []
MEASURE = """() => [...document.querySelectorAll('button,a,input,select,textarea,summary')].filter(e => {
  const r = e.getBoundingClientRect(), s = getComputedStyle(e);
  return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && !e.closest('#fc-offline,[hidden]');
}).map(e => {
  let t = e; if (e.type === 'checkbox' || e.type === 'radio' || e.type === 'hidden') t = e.closest('label') || e;
  if (e.tagName === 'A' && e.closest('p,li,.muted,.hint') && !e.classList.contains('btn')) return null; // links in text
  const r = t.getBoundingClientRect(); return r.height < 43.5 ? `${e.tagName}.${e.className} "${(e.textContent||e.name||'').trim().slice(0,30)}" ${Math.round(r.height)}px` : null;
}).filter(Boolean)"""

with sync_playwright() as p:
    b = p.chromium.launch()
    for w, h, tag in ((820, 1180, "ipad"), (1366, 768, "chromebook")):
        ctx = b.new_context(viewport={"width": w, "height": h}, has_touch=True)
        pg = ctx.new_page()
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(U + "/login"); pg.fill("[name=username]", "demo"); pg.fill("[name=password]", "secret123")
        pg.click("button")
        with webapp.collection("demo") as col:
            if not col.find_cards(""):
                for i in range(4):
                    n = col.new_note(col.models.by_name("Basic")); n["Front"] = f"front {i}"; n["Back"] = f"back {i}"
                    col.add_note(n, 1)
        for path in ("/", "/deck/1", "/browse", "/add", "/settings", "/preferences", "/stats", "/import", "/sync"):
            pg.goto(U + path)
            small += [f"{tag} {path}: {s}" for s in pg.evaluate(MEASURE)]
        pg.goto(U + "/no/such/page"); pg.screenshot(path=os.path.join(SHOTS, f"touch_404_{tag}.png"))
        pg.goto(U + "/offline"); pg.screenshot(path=os.path.join(SHOTS, f"touch_offline_{tag}.png"))
        ctx.close()
    # Tap zones on the iPad.
    ctx = b.new_context(viewport={"width": 820, "height": 1180}, has_touch=True)
    pg = ctx.new_page(); pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(U + "/login"); pg.fill("[name=username]", "demo"); pg.fill("[name=password]", "secret123"); pg.click("button")
    pg.goto(U + "/preferences"); pg.check("input[name=tap_zones]"); pg.click("button.primary"); pg.wait_for_selector(".flash")
    pg.goto(U + "/study/1"); pg.wait_for_timeout(500)
    box = pg.locator("#frame").bounding_box()
    # A swipe (pointer moves) must do nothing.
    pg.mouse.move(box["x"] + 100, box["y"] + 100); pg.mouse.down(); pg.mouse.move(box["x"] + 400, box["y"] + 110); pg.mouse.up()
    pg.wait_for_timeout(300); assert pg.is_visible("#show"), "swipe showed the answer"
    pg.touchscreen.tap(box["x"] + box["width"] / 2, box["y"] + 100); pg.wait_for_timeout(600)
    assert pg.is_visible("#btns"), "tap did not show the answer"
    pg.screenshot(path=os.path.join(SHOTS, "touch_study_tapzones.png"))
    with webapp.collection("demo") as col:
        before = col.db.scalar("select count() from revlog where ease=3")
    pg.touchscreen.tap(box["x"] + box["width"] * 0.8, box["y"] + 100); pg.wait_for_load_state(); pg.wait_for_timeout(600)
    with webapp.collection("demo") as col:
        assert col.db.scalar("select count() from revlog where ease=3") == before + 1, "right-half tap is not Good"
    ctx.close()
    b.close()
srv.should_exit = True
assert not errors, errors
assert not small, "\n".join(small)
print("screenshots saved")
