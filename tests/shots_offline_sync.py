"""Offline study, part 3 in a real browser (needs Playwright + Chromium). Downloads a deck for offline
use, goes offline (context.set_offline(True)), studies 5 cards on the offline study page including an
Again, adds a note on the offline Add screen, then comes back online (context.set_offline(False)):
the page sends the queue, and the test checks the review history on the server (5 reviews, at the
times they were given offline, Again first), the added note, that nothing is left waiting, and that
a second "online" event sends nothing twice. Screenshots of the offline Add screen and the "saved"
message at iPad (820x1180) and Chromebook (1366x768) sizes, light and dark, into shots/. Fails on page
JS errors."""
import os, sys, tempfile, threading, time
os.environ["DATA_DIR"] = tempfile.mkdtemp()
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import uvicorn
import app as webapp
from playwright.sync_api import sync_playwright

SHOTS = os.path.join(ROOT, "shots")
os.makedirs(SHOTS, exist_ok=True)
srv = uvicorn.Server(uvicorn.Config(webapp.app, port=8776, log_level="error"))
threading.Thread(target=srv.run, daemon=True).start()
time.sleep(1.5)
U = "http://127.0.0.1:8776"
errors = []


def ready_offline(pg, did):
    pg.goto(f"{U}/deck/{did}")
    pg.wait_for_function("document.getElementById('fc-off-status').textContent.startsWith('Offline ready')",
                         timeout=30000)
    pg.wait_for_function("navigator.serviceWorker.controller !== null", timeout=30000)
    pg.wait_for_function("caches.match('/offline/study').then(r => !!r)", timeout=30000)


with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 820, "height": 1180})
    pg = ctx.new_page()
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(U + "/login"); pg.fill("[name=username]", "demo"); pg.fill("[name=password]", "secret123")
    pg.click("button")
    with webapp.collection("demo") as col:
        did = col.decks.id("Italian")
        basic = col.models.by_name("Basic")
        for i in range(8):
            n = col.new_note(basic); n["Front"] = f"casa {i}"; n["Back"] = "house"; col.add_note(n, did)
    pg.goto(f"{U}/deck/{did}")
    pg.click(".fc-switch")
    pg.wait_for_selector(".flash")
    ready_offline(pg, did)

    # ---- offline: 5 answers (the first is Again) and a note added ----
    ctx.set_offline(True)
    pg.goto(f"{U}/study/{did}")
    pg.wait_for_selector("#fco-study:not([hidden])")
    pg.click("#show"); pg.click(".ans[data-r='1']")
    for _ in range(4):
        pg.wait_for_selector("#show:visible")
        pg.click("#show"); pg.click(".ans[data-r='3']")
    pg.wait_for_function("document.getElementById('fco-banner').textContent.includes('5 answers waiting')")
    queued = pg.evaluate("async () => (await window.FCOffline.answers()).map(a => [a.cid, a.rating, a.ts])")
    assert len(queued) == 5 and queued[0][1] == 1, queued
    pg.click("#fco-add-link")
    pg.wait_for_selector("#fco-add:not([hidden])")
    pg.fill("#fco-f0", "added offline <b>")
    pg.fill("#fco-f1", "line 1\nline 2")
    pg.fill("#fco-tags", "offline")
    pg.click("#fco-add-btn")
    pg.wait_for_function("document.getElementById('fco-banner').textContent.includes('1 note waiting')")
    for name, size, scheme in (("ipad", (820, 1180), "light"), ("ipad_dark", (820, 1180), "dark"),
                               ("chromebook", (1366, 768), "light"), ("chromebook_dark", (1366, 768), "dark")):
        pg.set_viewport_size({"width": size[0], "height": size[1]}); pg.emulate_media(color_scheme=scheme)
        pg.wait_for_timeout(200)
        pg.screenshot(path=os.path.join(SHOTS, f"offline_add_{name}.png"), full_page=True)
    pg.set_viewport_size({"width": 820, "height": 1180}); pg.emulate_media(color_scheme="light")
    with webapp.collection("demo") as col:
        assert col.db.scalar("select count() from revlog") == 0

    # ---- back online: the page sends everything ----
    ctx.set_offline(False)
    pg.wait_for_function("document.getElementById('fco-flash').textContent.includes('5 offline answers were saved')",
                         timeout=30000)
    msg = pg.inner_text("#fco-flash")
    assert "1 note added offline was saved" in msg, msg
    for name, size, scheme in (("ipad", (820, 1180), "light"), ("chromebook_dark", (1366, 768), "dark")):
        pg.set_viewport_size({"width": size[0], "height": size[1]}); pg.emulate_media(color_scheme=scheme)
        pg.wait_for_timeout(200)
        pg.screenshot(path=os.path.join(SHOTS, f"offline_synced_{name}.png"), full_page=True)
    with webapp.collection("demo") as col:
        rows = col.db.all("select id, cid, ease from revlog order by id")
        assert [[r[1], r[2]] for r in rows] == [[q[0], q[1]] for q in sorted(queued, key=lambda q: q[2])], (rows, queued)
        assert [r[0] for r in rows] == sorted(q[2] for q in queued), "reviews keep the time they were given offline"
        nid = col.find_notes("tag:offline")
        assert len(nid) == 1
        note = col.get_note(nid[0])
        assert note["Front"] == "added offline &lt;b&gt;" and note["Back"] == "line 1<br>line 2", note.fields
    w = pg.evaluate("async () => window.FCOffline.waiting()")
    assert w == {"answers": 0, "adds": 0}, w
    # another 'online' event sends nothing twice
    pg.evaluate("window.dispatchEvent(new Event('online'))")
    pg.wait_for_timeout(1500)
    with webapp.collection("demo") as col:
        assert col.db.scalar("select count() from revlog") == 5 and len(col.find_notes("tag:offline")) == 1
    # the copy is refreshed after the sync: the synced answers are cleared
    pg.wait_for_function("window.FCOffline.answers().then(a => a.length === 0)", timeout=30000)

    # ---- the same from a normal page: answers queued offline, then the deck list opened online ----
    ready_offline(pg, did)
    ctx.set_offline(True)
    pg.goto(f"{U}/study/{did}")
    pg.wait_for_selector("#fco-study:not([hidden])")
    pg.click("#show"); pg.click(".ans[data-r='3']")
    pg.wait_for_function("document.getElementById('fco-banner').textContent.includes('1 answer waiting')")
    ctx.set_offline(False)
    pg.goto(f"{U}/")
    pg.wait_for_selector("#fc-synced", timeout=30000)
    assert "1 offline answer was saved" in pg.inner_text("#fc-synced")
    pg.screenshot(path=os.path.join(SHOTS, "offline_synced_decklist_ipad.png"))
    with webapp.collection("demo") as col:
        assert col.db.scalar("select count() from revlog") == 6
    b.close()
srv.should_exit = True
assert not errors, errors
print("offline sync checked in Chromium; screenshots saved")
