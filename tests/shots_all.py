"""Screenshots of every page at iPad (820x1180) and Chromebook (1366x768) sizes into shots/all/,
light and dark. Fails on page JavaScript errors and on pages wider than the screen (sideways
scrolling). Needs Playwright + Chromium:  python3 tests/shots_all.py"""
import json, os, re, struct, sys, tempfile, threading, time, zlib
os.environ["DATA_DIR"] = tempfile.mkdtemp()
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import uvicorn
import app as webapp
from flashcards import image_occlusion as io
from playwright.sync_api import sync_playwright

SHOTS = os.path.join(ROOT, "shots", "all")
os.makedirs(SHOTS, exist_ok=True)
SIZES = {"ipad": (820, 1180), "chromebook": (1366, 768)}


def png(w, h):
    raw = b"".join(b"\x00" + b"\x20\x80\xc0" * w for _ in range(h))
    def chunk(t, b): return struct.pack(">I", len(b)) + t + b + struct.pack(">I", zlib.crc32(t + b))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


srv = uvicorn.Server(uvicorn.Config(webapp.app, port=8770, log_level="error"))
threading.Thread(target=srv.run, daemon=True).start()
time.sleep(1.5)
U = "http://127.0.0.1:8770"
errors, wide = [], []

with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context()
    pg = ctx.new_page()
    pg.on("pageerror", lambda e: errors.append(f"{pg.url}: {e}"))
    pg.set_viewport_size({"width": 820, "height": 1180})
    pg.goto(U + "/login")
    pg.screenshot(path=os.path.join(SHOTS, "login_ipad.png"))
    pg.fill("[name=username]", "demo"); pg.fill("[name=password]", "secret123"); pg.click("button")

    with webapp.collection("demo") as col:
        col.media.write_data("pic.png", png(120, 60))
        bio, cells, fr = col.decks.id("Biology"), col.decks.id("Biology::Cells"), col.decks.id("French")
        for i in range(8):
            n = col.new_note(col.models.by_name("Basic"))
            n["Front"] = f"Question {i}" + (' <img src="pic.png">' if i == 0 else ""); n["Back"] = f"Answer {i}"
            n.tags = ["demo"]
            col.add_note(n, cells if i % 2 else bio)
        n = col.new_note(col.models.by_name("Cloze")); n["Text"] = "{{c1::DNA}} has {{c2::two}} strands"
        col.add_note(n, bio)
        n = col.new_note(col.models.by_name("Basic (type in the answer)")); n["Front"] = "chien"; n["Back"] = "dog"
        col.add_note(n, fr)
        ntid = io.io_notetype(col)["id"]
        basic = col.models.by_name("Basic")["id"]
        nid = col.find_notes("")[0]
        cid = col.find_cards("")[0]
    pg.request.post(U + "/io/new", headers={"X-Requested-With": "fetch"}, multipart={
        "ntid": str(ntid), "did": str(bio), "mode": "all", "header": "Heart", "back_extra": "", "tags": "",
        "shapes": json.dumps([{"shape": "rect", "ordinal": 1, "left": .1, "top": .2, "width": .3, "height": .4}]),
        "image": {"name": "heart.png", "mimeType": "image/png", "buffer": png(400, 200)}})
    for _ in range(4):  # some history
        pg.goto(f"{U}/study/{bio}")
        if pg.locator("#show").count():
            pg.keyboard.press("Space"); time.sleep(0.2); pg.keyboard.press("3"); time.sleep(0.4)
    with webapp.collection("demo") as col:
        io_nid = col.find_notes(f"mid:{ntid}")[0]

    pages = {
        "decks": "/", "deck_overview": f"/deck/{bio}", "deck_options": f"/deck/{bio}/options",
        "custom_study": f"/deck/{bio}/custom", "filtered_new": "/filtered/new", "study": f"/study/{bio}",
        "add": "/add", "edit": f"/edit/{nid}", "io_new": "/io/new", "io_edit": f"/io/{io_nid}",
        "browse": "/browse", "browse_notes": "/browse?mode=notes", "card_info": f"/card/{cid}/info",
        "stats": "/stats", "notetypes": "/notetypes", "fields": f"/notetypes/{basic}/fields",
        "templates": f"/notetypes/{basic}/cards", "change_notetype": "/notetypes/change",
        "import": "/import", "export": "/export", "settings": "/settings", "preferences": "/preferences",
        "backups": "/backups", "check_database": "/check-database", "empty_cards": "/empty-cards",
        "check_media": "/check-media", "sync": "/sync", "addons": "/addons", "offline": "/offline",
        "not_found": "/no-such-page",
    }
    for size, (w, h) in SIZES.items():
        pg.set_viewport_size({"width": w, "height": h})
        for scheme in ("light", "dark"):
            pg.emulate_media(color_scheme=scheme)
            for name, url in pages.items():
                pg.goto(U + url); time.sleep(0.4)
                if name == "study" and pg.locator("#show").count():
                    pg.screenshot(path=os.path.join(SHOTS, f"study_question_{size}_{scheme}.png"))
                    pg.keyboard.press("Space"); time.sleep(0.3)
                over = pg.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
                if over > 1:
                    wide.append(f"{name} at {size}: {over}px too wide")
                pg.screenshot(path=os.path.join(SHOTS, f"{name}_{size}_{scheme}.png"), full_page=True)
    b.close()
srv.should_exit = True
assert not errors, "\n".join(errors)
assert not wide, "\n".join(wide)
print(f"screenshots saved in {SHOTS}")
