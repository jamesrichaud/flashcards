"""Screenshots of the Check Media page at iPad size into shots/ (needs Playwright). Fails on page JS errors."""
import os, sys, tempfile, threading, time
os.environ["DATA_DIR"] = tempfile.mkdtemp()
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import uvicorn
import app as webapp
from playwright.sync_api import sync_playwright

PNG = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                    "1f15c4890000000d49444154789c6360f8cf00000301010018dd8db00000000049454e44ae426082")
SHOTS = os.path.join(ROOT, "shots")
os.makedirs(SHOTS, exist_ok=True)
srv = uvicorn.Server(uvicorn.Config(webapp.app, port=8767, log_level="error"))
threading.Thread(target=srv.run, daemon=True).start()
time.sleep(1.5)
U = "http://127.0.0.1:8767"
errors = []
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 820, "height": 1180})
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(U + "/login"); pg.fill("[name=username]", "demo"); pg.fill("[name=password]", "secret123")
    pg.click("button")
    with webapp.collection("demo") as col:
        for i in range(4):
            col.media.write_data(f"holiday-{i}.png", PNG + bytes([i]))
        col.media.write_data("old-recording.mp3", b"ID3" + bytes(100))
        n = col.new_note(col.models.by_name("Basic")); n["Front"] = 'Where? <img src="map.png">'
        n["Back"] = "[sound:answer.mp3]"; col.add_note(n, 1)
    pg.goto(U + "/check-media")
    pg.screenshot(path=os.path.join(SHOTS, "check_media.png"), full_page=True)
    pg.on("dialog", lambda d: d.accept())
    pg.click("text=Delete ticked")
    pg.wait_for_load_state()
    pg.screenshot(path=os.path.join(SHOTS, "check_media_deleted.png"), full_page=True)
    pg.emulate_media(color_scheme="dark")
    pg.reload()
    pg.screenshot(path=os.path.join(SHOTS, "check_media_dark.png"), full_page=True)
    b.close()
srv.should_exit = True
assert not errors, errors
print("screenshots saved")
