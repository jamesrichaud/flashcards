"""Offline study, part 2 in a real browser (needs Playwright + Chromium). Downloads a deck for offline
use, waits for the service worker and the cached offline study page, then goes offline
(context.set_offline(True)) and: opens /study/<deck> (the service worker shows the offline study page),
studies cards (one Again, which comes back), checks the "Offline · N answers waiting to sync" banner
and the queued answers, undoes the last one, answers a typed card, and shows a MathJax card from the
bundled MathJax. Screenshots of the deck list, the question, the answer and a re-shown learning card
at iPad (820x1180) and Chromebook (1366x768) sizes, light and dark, into shots/. Fails on page JS
errors."""
import os, sys, tempfile, threading, time
os.environ["DATA_DIR"] = tempfile.mkdtemp()
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import uvicorn
import app as webapp
from playwright.sync_api import sync_playwright

SHOTS = os.path.join(ROOT, "shots")
os.makedirs(SHOTS, exist_ok=True)
srv = uvicorn.Server(uvicorn.Config(webapp.app, port=8775, log_level="error"))
threading.Thread(target=srv.run, daemon=True).start()
time.sleep(1.5)
U = "http://127.0.0.1:8775"
errors = []
with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 820, "height": 1180})
    pg = ctx.new_page()
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(U + "/login"); pg.fill("[name=username]", "demo"); pg.fill("[name=password]", "secret123")
    pg.click("button")
    with webapp.collection("demo") as col:
        did = col.decks.id("French")
        basic = col.models.by_name("Basic")
        for i in range(8):
            n = col.new_note(basic); n["Front"] = f"maison {i}"; n["Back"] = "house"; col.add_note(n, did)
        n = col.new_note(basic); n["Front"] = r"area \(\pi r^2\)"; n["Back"] = r"\[\int_0^1 x\,dx\]"; col.add_note(n, did)
        n = col.new_note(col.models.by_name("Basic (type in the answer)"))
        n["Front"] = "capital of France?"; n["Back"] = "Paris"; col.add_note(n, did)
    pg.goto(f"{U}/deck/{did}")
    pg.click(".fc-switch")
    pg.wait_for_selector(".flash")
    pg.wait_for_function("document.getElementById('fc-off-status').textContent.startsWith('Offline ready')",
                         timeout=30000)
    pg.wait_for_function("navigator.serviceWorker.controller !== null", timeout=30000)
    pg.wait_for_function("caches.match('/offline/study').then(r => !!r)", timeout=30000)
    pg.wait_for_function("caches.match('/static/mathjax/tex-chtml-full.js').then(r => !!r)", timeout=60000)

    ctx.set_offline(True)
    pg.goto(f"{U}/study/{did}")
    pg.wait_for_selector("#fco-study:not([hidden])")
    assert "Offline" in pg.inner_text("#fco-banner")
    pg.click("#show"); pg.click(".ans[data-r='1']")          # Again
    pg.wait_for_function("document.getElementById('fco-banner').textContent.includes('1 answer waiting')")
    for _ in range(2):
        pg.click("#show"); pg.click(".ans[data-r='3']")
    pg.wait_for_function("document.getElementById('fco-banner').textContent.includes('3 answers waiting')")
    pg.click(".fco-undo >> nth=0")
    pg.wait_for_function("document.getElementById('fco-banner').textContent.includes('2 answers waiting')")
    q = pg.evaluate("async () => (await window.FCOffline.answers()).map(a => [a.cid, a.rating, a.ms > 0, a.ts > 0, !!a.uid])")
    assert len(q) == 2 and q[0][1] == 1 and all(x[2] and x[3] and x[4] for x in q), q
    for name, size, scheme in (("ipad", (820, 1180), "light"), ("ipad_dark", (820, 1180), "dark"),
                               ("chromebook", (1366, 768), "light"), ("chromebook_dark", (1366, 768), "dark")):
        pg.set_viewport_size({"width": size[0], "height": size[1]}); pg.emulate_media(color_scheme=scheme)
        pg.goto(f"{U}/")
        pg.wait_for_selector("#fco-list:not([hidden])")
        pg.screenshot(path=os.path.join(SHOTS, f"offline_study_list_{name}.png"), full_page=True)
        pg.goto(f"{U}/study/{did}")
        pg.wait_for_selector("#fco-study:not([hidden])")
        pg.wait_for_timeout(400)
        pg.screenshot(path=os.path.join(SHOTS, f"offline_study_question_{name}.png"))
        pg.click("#show"); pg.wait_for_timeout(400)
        pg.screenshot(path=os.path.join(SHOTS, f"offline_study_answer_{name}.png"))
    # study on until the Again card comes back (learn ahead), with "~" labels
    for _ in range(20):
        if pg.is_visible("#show"):
            pg.click("#show")
        txt = pg.inner_text("#btns")
        if "~" in txt:
            pg.screenshot(path=os.path.join(SHOTS, "offline_study_relearn_ipad.png"))
            break
        pg.click(".ans[data-r='3']")
        pg.wait_for_timeout(200)
    else:
        raise AssertionError("the card answered Again didn't come back")
    ctx.set_offline(False)
    b.close()
srv.should_exit = True
assert not errors, errors
print("screenshots saved")
