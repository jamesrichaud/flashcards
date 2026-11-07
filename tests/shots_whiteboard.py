"""Whiteboard screenshots into shots/ (needs Playwright + Chromium) and real-browser pointer tests:
mouse drawing puts ink on the canvas, a still click goes through to the card (tap zones show the
answer), pen pointer events (CDP Input.dispatchTouchEvent can't make pens, so synthetic
PointerEvents are dispatched) draw and turn fingers into scrolling, undo/clear empty the canvas,
toggle state is remembered after a reload. iPad 820x1180 light and dark. Fails on page JS errors."""
import os, sys, tempfile, threading, time
os.environ["DATA_DIR"] = tempfile.mkdtemp()
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import uvicorn
import app as webapp
from playwright.sync_api import sync_playwright

SHOTS = os.path.join(ROOT, "shots")
os.makedirs(SHOTS, exist_ok=True)
srv = uvicorn.Server(uvicorn.Config(webapp.app, port=8772, log_level="error"))
threading.Thread(target=srv.run, daemon=True).start()
time.sleep(1.5)
U = "http://127.0.0.1:8772"
errors = []
INK = """() => { const c = document.getElementById('wb'), d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
  let n = 0; for (let i = 3; i < d.length; i += 4) if (d[i]) n++; return n; }"""
PEN = """([type, kind, x, y, id]) => { const c = document.getElementById('wb'), r = c.getBoundingClientRect();
  c.dispatchEvent(new PointerEvent(type, {bubbles: true, cancelable: true, pointerId: id, pointerType: kind,
    clientX: r.left + x, clientY: r.top + y, pressure: 0.7, isPrimary: true, width: 1, height: 1})); }"""

with sync_playwright() as p:
    b = p.chromium.launch()
    for scheme in ("light", "dark"):
        ctx = b.new_context(viewport={"width": 820, "height": 1180}, has_touch=True, color_scheme=scheme)
        pg = ctx.new_page(); pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(U + "/login"); pg.fill("[name=username]", "demo"); pg.fill("[name=password]", "secret123")
        pg.click("button")
        with webapp.collection("demo") as col:
            if not col.find_cards(""):
                for i in range(6):
                    n = col.new_note(col.models.by_name("Basic")); n["Front"] = f"Write the word {i}"; n["Back"] = f"word {i}"
                    col.add_note(n, 1)
                col.set_config("flashcardsTapZones", True)
        pg.goto(U + "/study/1"); pg.wait_for_timeout(500)
        if not pg.is_visible("#wbbar"):
            pg.click("#wbtoggle"); pg.wait_for_timeout(300)
        assert pg.is_visible("#wb") and pg.is_visible("#wbbar")
        box = pg.locator("#wb").bounding_box(); fb = pg.locator("#frame").bounding_box()
        assert abs(box["width"] - fb["width"]) < 1 and abs(box["height"] - fb["height"]) < 1, (box, fb)
        # Mouse drawing.
        pg.mouse.move(box["x"] + 60, box["y"] + 200); pg.mouse.down()
        for i in range(20):
            pg.mouse.move(box["x"] + 60 + i * 15, box["y"] + 200 + (i % 5) * 8)
        pg.mouse.up()
        assert pg.evaluate(INK) > 100, "mouse drag drew nothing"
        pg.click("[data-wbc=red]"); pg.click("[data-wbw='8']")
        for i, (pid, kind) in enumerate(((11, "pen"),)):
            pg.evaluate(PEN, ["pointerdown", kind, 100, 300, pid])
            for k in range(15):
                pg.evaluate(PEN, ["pointermove", kind, 100 + k * 12, 300 + k * 6, pid])
            pg.evaluate(PEN, ["pointerup", kind, 280, 390, pid])
        assert pg.evaluate("WB.penMode()") and pg.evaluate("WB.strokes().length") == 2
        pg.screenshot(path=os.path.join(SHOTS, f"whiteboard_study_{scheme}.png"))
        # After a pen, a finger drag doesn't draw.
        pg.evaluate(PEN, ["pointerdown", "touch", 300, 100, 21])
        pg.evaluate(PEN, ["pointermove", "touch", 300, 60, 21]); pg.evaluate(PEN, ["pointerup", "touch", 300, 60, 21])
        assert pg.evaluate("WB.strokes().length") == 2, "finger drew after pen use"
        # A still tap goes through: tap zones show the answer, drawing stays.
        # (Touches within 0.5 s of the pen lifting are ignored on purpose: palm rejection.)
        pg.wait_for_timeout(600)
        pg.evaluate(PEN, ["pointerdown", "touch", 200, 100, 22]); pg.evaluate(PEN, ["pointerup", "touch", 200, 100, 22])
        pg.wait_for_timeout(500)
        assert pg.is_visible("#btns"), "tap through the whiteboard didn't show the answer"
        assert pg.evaluate("WB.strokes().length") == 2 and pg.evaluate(INK) > 100
        pg.screenshot(path=os.path.join(SHOTS, f"whiteboard_answer_{scheme}.png"))
        pg.click("#wbundo"); assert pg.evaluate("WB.strokes().length") == 1
        pg.click("#wbclear"); assert pg.evaluate(INK) == 0
        # Answer buttons still work; the next card starts clean and the toggle is remembered.
        pg.click(".ans >> nth=2"); pg.wait_for_load_state(); pg.wait_for_timeout(500)
        assert pg.is_visible("#wbbar") and pg.evaluate("WB.strokes().length") == 0
        pg.click("#wbtoggle"); pg.wait_for_timeout(300)
        assert not pg.is_visible("#wb")
        pg.reload(); pg.wait_for_timeout(500)
        assert not pg.is_visible("#wbbar")
        pg.click("#wbtoggle"); pg.wait_for_timeout(300)   # leave it on for the next colour scheme
        ctx.close()
    b.close()
srv.should_exit = True
assert not errors, errors
print("screenshots saved")
