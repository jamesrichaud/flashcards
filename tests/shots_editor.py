"""Screenshots of the rich editor (Add and Edit) at iPad size into shots/ (needs Playwright).
Also drives the editor: bold, cloze, preview, duplicate warning; fails on page JS errors."""
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
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 820, "height": 1180})
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.on("dialog", lambda d: d.accept())
    pg.goto(U + "/login"); pg.fill("[name=username]", "demo"); pg.fill("[name=password]", "secret123")
    pg.click("button")
    with webapp.collection("demo") as col:
        basic, cloze = col.models.by_name("Basic")["id"], col.models.by_name("Cloze")["id"]
        n = col.new_note(col.models.by_name("Basic")); n["Front"], n["Back"] = "chien", "dog"; col.add_note(n, 1)
        nid = n.id
    # bold + duplicate warning on a Basic note
    pg.goto(f"{U}/add?nt={basic}")
    rich = pg.locator(".fld >> nth=0").locator(".rich")
    rich.click(); pg.keyboard.type("chien")
    pg.wait_for_timeout(800)
    assert pg.locator("#ed-dupe a").count() == 1, "duplicate warning missing"
    back = pg.locator(".fld >> nth=1").locator(".rich")
    back.click(); pg.keyboard.type("dog ")
    pg.click("[data-cmd=bold]"); pg.keyboard.type("bold")
    assert "<b>bold</b>" in pg.input_value("#f1"), pg.input_value("#f1")
    pg.click("[data-act=preview]"); pg.wait_for_timeout(1000)
    assert "chien" in pg.frame_locator("#pvq").locator("body").inner_text()
    pg.screenshot(path=os.path.join(SHOTS, "editor_add.png"), full_page=True)
    # cloze buttons and MathJax wrapping
    pg.goto(f"{U}/add?nt={cloze}")
    rich = pg.locator(".fld >> nth=0").locator(".rich")
    rich.click(); pg.keyboard.type("Paris is in France")
    pg.keyboard.press("Shift+Home")
    pg.keyboard.press("Control+Shift+KeyC")
    pg.keyboard.press("End"); pg.keyboard.type(" and ")
    pg.keyboard.press("Control+Shift+Alt+KeyC"); pg.keyboard.type("x")
    v = pg.input_value("#f0")
    assert "{{c1::Paris is in France}}" in v and "{{c1::x}}" in v, v
    pg.click("[data-act=mj-inline]"); pg.keyboard.type("y^2")
    assert "\\(y^2\\)" in pg.input_value("#f0"), pg.input_value("#f0")
    pg.click(".fld >> nth=0 >> .htmlbtn")
    pg.screenshot(path=os.path.join(SHOTS, "editor_cloze.png"), full_page=True)
    # edit page, dark mode
    pg.emulate_media(color_scheme="dark")
    pg.goto(f"{U}/edit/{nid}?back=/browse")
    pg.screenshot(path=os.path.join(SHOTS, "editor_edit_dark.png"), full_page=True)
    b.close()
    assert not errors, errors
srv.should_exit = True
print("screenshots saved")
