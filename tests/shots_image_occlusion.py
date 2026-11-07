"""Screenshots of the image occlusion editor and an occlusion card at iPad size into shots/ (needs
Playwright). Drives the editor: pick an image, draw a rectangle and an ellipse, group, add, then edit
and study; fails on page JS errors or if the masks are not drawn on the card."""
import os, struct, sys, tempfile, threading, time, zlib
os.environ["DATA_DIR"] = tempfile.mkdtemp()
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import uvicorn
import app as webapp
from playwright.sync_api import sync_playwright


def png(w, h):
    raw = b"".join(b"\x00" + b"".join(bytes((x % 256, y % 256, 160)) for x in range(w)) for y in range(h))
    def chunk(t, b): return struct.pack(">I", len(b)) + t + b + struct.pack(">I", zlib.crc32(t + b))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


SHOTS = os.path.join(ROOT, "shots")
os.makedirs(SHOTS, exist_ok=True)
img_path = os.path.join(tempfile.mkdtemp(), "diagram.png")
open(img_path, "wb").write(png(600, 400))
srv = uvicorn.Server(uvicorn.Config(webapp.app, port=8772, log_level="error"))
threading.Thread(target=srv.run, daemon=True).start()
time.sleep(1.5)
U = "http://127.0.0.1:8772"


def drag(pg, x0, y0, x1, y1):
    box = pg.locator("#io-svg").bounding_box()
    pg.mouse.move(box["x"] + box["width"] * x0, box["y"] + box["height"] * y0)
    pg.mouse.down()
    pg.mouse.move(box["x"] + box["width"] * x1, box["y"] + box["height"] * y1, steps=5)
    pg.mouse.up()


with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 820, "height": 1180})
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.on("dialog", lambda d: d.accept())
    pg.goto(U + "/login"); pg.fill("[name=username]", "demo"); pg.fill("[name=password]", "secret123")
    pg.click("button")
    pg.goto(U + "/io/new")
    pg.screenshot(path=os.path.join(SHOTS, "io_pick.png"), full_page=True)
    pg.set_input_files("#io-file", img_path)
    pg.wait_for_selector("#io-editor:not([hidden])")
    drag(pg, .1, .1, .4, .3)
    pg.click("[data-tool=ellipse]")
    drag(pg, .5, .5, .8, .9)
    pg.click("[data-tool=rect]")
    drag(pg, .6, .1, .9, .3)
    assert pg.locator("#io-svg .shp").count() == 3
    assert pg.inner_text("#io-count") == "3 cards", pg.inner_text("#io-count")
    pg.click("[data-tool=select]")
    pg.locator("#io-svg ellipse").click()
    pg.locator("#io-svg rect.shp >> nth=1").click(modifiers=["Shift"])
    pg.click("[data-act=group]")
    assert pg.inner_text("#io-count") == "2 cards"
    pg.fill("#io-header", "Parts of the diagram")
    pg.screenshot(path=os.path.join(SHOTS, "io_add.png"), full_page=True)
    pg.click("#io-save")
    pg.wait_for_url("**/io/new?nt=*")
    assert "Added 2 cards." in pg.inner_text(".flash")
    with webapp.collection("demo") as col:
        nid = col.find_notes("Header:*")[0]
        did = col.get_note(nid).cards()[0].did
    pg.goto(f"{U}/edit/{nid}?back=/browse")
    pg.wait_for_selector("#io-editor:not([hidden])")
    assert pg.locator("#io-svg .shp").count() == 3
    pg.emulate_media(color_scheme="dark")
    pg.screenshot(path=os.path.join(SHOTS, "io_edit_dark.png"), full_page=True)
    pg.emulate_media(color_scheme="light")
    pg.goto(f"{U}/study/{did}")
    pg.wait_for_timeout(800)
    frame = pg.frame_locator("#frame")
    # the masks are painted: the canvas over the image is not blank
    painted = frame.locator("#image-occlusion-canvas").evaluate(
        "c=>{const d=c.getContext('2d').getImageData(0,0,c.width,c.height).data;let n=0;"
        "for(let i=3;i<d.length;i+=4)if(d[i])n++;return n;}")
    assert painted > 1000, painted
    pg.screenshot(path=os.path.join(SHOTS, "io_study_front.png"), full_page=True)
    pg.click("#show"); pg.wait_for_timeout(800)
    pg.screenshot(path=os.path.join(SHOTS, "io_study_back.png"), full_page=True)
    b.close()
    assert not errors, errors
srv.should_exit = True
print("screenshots saved")
