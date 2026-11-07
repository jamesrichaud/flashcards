"""Image occlusion: create from an uploaded image, rectangles and ellipses, hide all / hide one,
edit masks later, cards rendered with Anki's own note type and drawn by the review script."""
import html, json, os, re, struct, sys, tempfile, zlib
os.environ["DATA_DIR"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fastapi.testclient import TestClient
import app as webapp
from flashcards import image_occlusion as io

ok = lambda m: print("PASS", m)
c = TestClient(webapp.app)


def png(w, h):
    raw = b"".join(b"\x00" + b"\x20\x80\xc0" * w for _ in range(h))
    def chunk(t, b): return struct.pack(">I", len(b)) + t + b + struct.pack(">I", zlib.crc32(t + b))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


IMG = png(200, 100)
FETCH = {"X-Requested-With": "fetch"}


def flash(r):
    m = re.search(r'<div class="flash( err)?">(.*?)</div>', r.text, re.S)
    return ("!" if m and m.group(1) else "") + html.unescape(m.group(2)) if m else ""


# --- pure helpers ---
assert io.fmt_num(0.1) == ".1" and io.fmt_num(0.12345) == ".1235" and io.fmt_num(0) == "0" and io.fmt_num(1) == "1"
assert io.fmt_num(12.5) == "12.5" and io.fmt_num(-0.25) == "-.25"
occ = io.build_occlusions([{"shape": "rect", "ordinal": 3, "left": .1, "top": .2, "width": .3, "height": .4},
                           {"shape": "ellipse", "ordinal": 7, "left": .5, "top": .5, "rx": .1, "ry": .2},
                           {"shape": "rect", "ordinal": 3, "left": .6, "top": .1, "width": .1, "height": .1}],
                          hide_all=True, renumber=True)
assert occ == ("{{c1::image-occlusion:rect:left=.1:top=.2:width=.3:height=.4:oi=1}}<br>"
               "{{c2::image-occlusion:ellipse:left=.5:top=.5:rx=.1:ry=.2:oi=1}}<br>"
               "{{c1::image-occlusion:rect:left=.6:top=.1:width=.1:height=.1:oi=1}}"), occ
occ = io.build_occlusions([{"shape": "rect", "ordinal": 4, "left": .1, "top": .2, "width": .3, "height": .4,
                            "extra": {"angle": "15", "bad key": "x", "fill": "a:b}}"}}], hide_all=False)
assert occ == "{{c4::image-occlusion:rect:left=.1:top=.2:width=.3:height=.4:angle=15:fill=a b}}", occ
poly = io.build_occlusions([{"shape": "polygon", "ordinal": 1, "points": [[.2, .2], [.4, .2], [.3, .5]]}], True)
assert poly == "{{c1::image-occlusion:polygon:left=.2:top=.2:points=.2,.2 .4,.2 .3,.5:oi=1}}", poly
for bad in ([], [{"shape": "rect", "ordinal": 1, "left": .1, "top": .1, "width": 0, "height": .1}],
            [{"shape": "star", "ordinal": 1}], [{"shape": "rect", "ordinal": "x"}],
            [{"shape": "rect", "ordinal": 1, "left": "nan", "top": .1, "width": .1, "height": .1}],
            [{"shape": "text", "ordinal": 1, "left": .1, "top": .1, "extra": {"text": "hi"}}], "nope"):
    try:
        io.build_occlusions(bad, True)
        raise AssertionError(f"accepted {bad!r}")
    except ValueError:
        pass
ok("masks are written in Anki's occlusion format (4 decimals, oi=1 for hide all, groups share a number)")

# --- login needed ---
assert c.get("/io/new", follow_redirects=False).status_code == 303
assert c.post("/io/new", data={}, follow_redirects=False).status_code == 303
assert c.get("/io/1", follow_redirects=False).status_code == 303
ok("image occlusion routes need login")
c.post("/login", data={"username": "tester", "password": "secret123"})

# --- the add page ---
r = c.get("/io/new")
assert r.status_code == 200, r.text
with webapp.collection("tester") as col:
    nt = io.io_notetype(col)
    assert nt and nt.get("originalStockKind") == io.IO_STOCK_KIND and [f["name"] for f in nt["flds"]][:4] == \
        ["Occlusion", "Image", "Header", "Back Extra"]
    ntid = nt["id"]
    deck = col.decks.id("Anatomy")
    basic = col.models.by_name("Basic")["id"]
for s in ('id="io-svg"', 'id="io-file"', 'id="io-paste"', 'data-tool="rect"', 'data-tool="ellipse"', 'data-tool="select"',
          'data-act="group"', 'data-act="ungroup"', 'data-act="delete"', 'data-act="undo"', 'name="mode"',
          'value="all" selected', "Hide All, Guess One", "Hide One, Guess One", 'name="header"', 'name="back_extra"',
          'name="tags"', f'value="{ntid}" selected', "pointerdown"):
    assert s in r.text, s
conf = json.loads(html.unescape(re.search(r'id="io-conf">(.*?)</script>', r.text, re.S).group(1)))
assert conf["adding"] is True and conf["msgs"]["noOcclusion"] == "You must make at least one occlusion."
ok("add page: image picker/paste, rectangle/ellipse/select tools, group, mode, header and back extra")

# Add redirects to the mask editor for Image Occlusion types, and links to it otherwise
r = c.get(f"/add?nt={ntid}&did={deck}", follow_redirects=False)
assert r.status_code == 303 and r.headers["location"] == f"/io/new?nt={ntid}&did={deck}"
r = c.get(f"/add?nt={basic}")
assert r.status_code == 200 and 'href="/io/new?did=' in r.text
ok("Add switches to the mask editor for Image Occlusion note types")

# --- adding ---
shapes = [{"shape": "rect", "ordinal": 1, "left": .1, "top": .2, "width": .3, "height": .4},
          {"shape": "ellipse", "ordinal": 2, "left": .5, "top": .5, "rx": .1, "ry": .2},
          {"shape": "rect", "ordinal": 2, "left": .7, "top": .1, "width": .1, "height": .1}]
form = {"ntid": ntid, "did": deck, "mode": "all", "header": "Heart", "back_extra": "Chambers", "tags": "bio  heart",
        "shapes": json.dumps(shapes)}
r = c.post("/io/new", data=form, files={"image": ("heart diagram.png", IMG, "image/png")}, headers=FETCH)
assert r.status_code == 200 and r.json()["ok"], r.text
assert r.json()["url"] == f"/io/new?nt={ntid}&did={deck}"
assert flash(c.get(r.json()["url"])) == "Added 2 cards."
with webapp.collection("tester") as col:
    nids = col.find_notes(f"mid:{ntid}")
    assert len(nids) == 1
    note = col.get_note(nids[0])
    nid = note.id
    assert note["Image"] == '<img src="heart diagram.png">', note["Image"]
    assert note["Header"] == "Heart" and note["Back Extra"] == "Chambers" and sorted(note.tags) == ["bio", "heart"]
    assert "oi=1" in note["Occlusion"] and "ellipse:left=.5:top=.5:rx=.1:ry=.2" in note["Occlusion"]
    assert os.path.exists(os.path.join(col.media.dir(), "heart diagram.png"))
    cards = sorted(note.cards(), key=lambda c: c.ord)
    assert [c.ord for c in cards] == [0, 1] and all(c.did == deck for c in cards)
    q, a = cards[1].question(), cards[1].answer()
    # card 2 (the ellipse and the small box): those are active, card 1's box is an inactive mask
    assert q.count('class="cloze"') == 2 and q.count('class="cloze-inactive"') == 1 and 'data-occludeInactive="1"' in q
    assert a.count('class="cloze-highlight"') == 2 and 'id="toggle"' in a and "Chambers" in a and "Heart" in q
    assert col.get_config("curDeck") == deck
ok("adding makes one card per mask group with the image in media, in the chosen deck, with header/back extra/tags")

# hide one, guess one: no oi
r = c.post("/io/new", data={**form, "mode": "one", "shapes": json.dumps(shapes[:1])},
           files={"image": ("heart diagram.png", IMG, "image/png")}, headers=FETCH)
assert r.json()["ok"]
with webapp.collection("tester") as col:
    n2 = col.get_note(max(col.find_notes(f"mid:{ntid}")))
    assert "oi=1" not in n2["Occlusion"] and n2["Image"] == '<img src="heart diagram.png">'   # same file reused
    assert len(n2.cards()) == 1
    resp = col.get_image_occlusion_note(n2.id)
    assert resp.note.occlude_inactive is False
ok("'Hide one, guess one' leaves other masks off (no oi), and the same picture is not stored twice")

# pasted pictures are named like Anki's
r = c.post("/io/new", data={**form, "pasted": "1"}, files={"image": ("image.png", png(10, 10), "image/png")}, headers=FETCH)
assert r.json()["ok"]
with webapp.collection("tester") as col:
    n3 = col.get_note(max(col.find_notes(f"mid:{ntid}")))
    assert re.fullmatch(r'<img src="paste-[0-9a-f]{40}\.png">', n3["Image"]), n3["Image"]
ok("pasted images are stored as paste-<sha1>.png")

# --- refusals ---
def refused(data, files, text, status=400):
    r = c.post("/io/new", data=data, files=files, headers=FETCH)
    assert r.status_code == status and not r.json()["ok"] and text in r.json()["msg"], (r.status_code, r.text)

with webapp.collection("tester") as col:
    count = col.note_count()
refused({**form, "shapes": "[]"}, {"image": ("a.png", IMG, "image/png")}, "at least one occlusion")
refused({**form, "shapes": "{bad"}, {"image": ("a.png", IMG, "image/png")}, "could not be read")
refused(form, None, "Choose an image")
refused(form, {"image": ("notes.txt", b"hello", "text/plain")}, "choose a picture")
refused(form, {"image": ("a.png", b"", "image/png")}, "empty")
refused({**form, "ntid": basic}, {"image": ("a.png", IMG, "image/png")}, "Image Occlusion note type")
with webapp.collection("tester") as col:
    fdid = col.decks.new_filtered("Filt")
    assert col.note_count() == count
refused({**form, "did": fdid}, {"image": ("a.png", IMG, "image/png")}, "normal deck")
# without fetch: plain redirect with the message
r = c.post("/io/new", data={**form, "shapes": "[]"}, files={"image": ("a.png", IMG, "image/png")}, follow_redirects=False)
assert r.status_code == 303 and flash(c.get(r.headers["location"])) == "!You must make at least one occlusion."
with webapp.collection("tester") as col:
    assert col.note_count() == count
ok("no masks, bad masks, no image, non-pictures, other note types and filtered decks are refused")

# --- editing ---
r = c.get(f"/edit/{nid}?back=/browse", follow_redirects=False)
assert r.status_code == 303 and r.headers["location"] == f"/io/{nid}?back=/browse"
r = c.get(f"/io/{nid}?back=/browse")
assert r.status_code == 200
conf = json.loads(html.unescape(re.search(r'id="io-conf">(.*?)</script>', r.text, re.S).group(1)))
assert conf["adding"] is False and conf["image"] == "/media/heart%20diagram.png" and conf["legacy"] is False
got = sorted((s["ordinal"], s["shape"], round(s["left"], 4)) for s in conf["shapes"])
assert got == [(1, "rect", .1), (2, "ellipse", .5), (2, "rect", .7)], got
assert 'value="all" selected' in r.text and ">Heart</textarea>" in r.text and 'value="bio heart"' in r.text
assert "Edit fields as text" in r.text and f'action="/card/' in r.text   # card actions panel
assert c.get(f"/edit/{nid}?raw=1").status_code == 200   # the plain field editor is still reachable
assert c.get("/media/heart%20diagram.png").content == IMG
ok("Edit opens the mask editor with the note's masks, mode, fields and the card panel")

# move a mask, delete the ellipse group's ellipse, add a new one: existing card numbers are kept
new = [{"shape": "rect", "ordinal": 1, "left": .15, "top": .2, "width": .3, "height": .4, "extra": {}},
       {"shape": "rect", "ordinal": 2, "left": .7, "top": .1, "width": .1, "height": .1, "extra": {}},
       {"shape": "ellipse", "ordinal": 3, "left": .4, "top": .6, "rx": .05, "ry": .05, "extra": {}}]
r = c.post(f"/io/{nid}?back=/browse", data={"mode": "one", "header": "Heart 2", "back_extra": "", "tags": "cardio",
                                            "shapes": json.dumps(new)}, headers=FETCH)
assert r.json()["ok"] and r.json()["url"] == "/browse", r.text
with webapp.collection("tester") as col:
    note = col.get_note(nid)
    assert note["Occlusion"] == ("{{c1::image-occlusion:rect:left=.15:top=.2:width=.3:height=.4}}<br>"
                                 "{{c2::image-occlusion:rect:left=.7:top=.1:width=.1:height=.1}}<br>"
                                 "{{c3::image-occlusion:ellipse:left=.4:top=.6:rx=.05:ry=.05}}"), note["Occlusion"]
    assert note["Header"] == "Heart 2" and note["Back Extra"] == "" and note.tags == ["cardio"]
    assert sorted(c.ord for c in note.cards()) == [0, 1, 2]
r = c.post(f"/io/{nid}?back=/browse", data={"mode": "all", "shapes": "[]"}, headers=FETCH)
assert not r.json()["ok"] and "at least one occlusion" in r.json()["msg"]
r = c.post(f"/io/{nid}", data={"mode": "all", "shapes": json.dumps(new)}, follow_redirects=False)
assert r.status_code == 303 and r.headers["location"] == "/browse"
ok("saving edits updates masks, mode, header and tags and keeps card numbers")

# a non-IO note on /io goes to the normal editor; a missing note goes back
with webapp.collection("tester") as col:
    bn = col.new_note(col.models.get(basic)); bn["Front"] = "q"; col.add_note(bn, 1)
r = c.get(f"/io/{bn.id}", follow_redirects=False)
assert r.status_code == 303 and r.headers["location"].startswith(f"/edit/{bn.id}?raw=1")
assert c.get(f"/edit/{bn.id}", follow_redirects=False).status_code == 200
r = c.get("/io/123456?back=/browse", follow_redirects=False)
assert r.status_code == 303 and r.headers["location"] == "/browse"
assert not c.post("/io/123456", data={"shapes": json.dumps(new)}, headers=FETCH).json()["ok"]
ok("other notes keep the normal editor; missing notes are handled")

# --- notes made by desktop Anki: polygons, text, pixel positions (Anki 23.10) ---
with webapp.collection("tester") as col:
    path = os.path.join(tempfile.mkdtemp(), "old.png")
    open(path, "wb").write(IMG)
    col.add_image_occlusion_note(ntid, path,
                                 "{{c1::image-occlusion:rect:left=20:top=10:width=60:height=30:oi=1}}"
                                 "{{c2::image-occlusion:polygon:left=.5:top=.5:points=0,0 .2,0 .1,.3:oi=1}}"
                                 "{{c2::image-occlusion:text:left=.1:top=.8:text=Aorta:scale=1:fs=.05}}",
                                 "", "", [])
    old = max(col.find_notes(f"mid:{ntid}"))
conf = json.loads(html.unescape(re.search(r'id="io-conf">(.*?)</script>', c.get(f"/io/{old}").text, re.S).group(1)))
assert conf["legacy"] is True
poly = next(s for s in conf["shapes"] if s["shape"] == "polygon")
assert poly["points"] == [[.5, .5], [.7, .5], [.6, .8]], poly
txt = next(s for s in conf["shapes"] if s["shape"] == "text")
assert txt["extra"]["text"] == "Aorta" and txt["ordinal"] == 2
again = io.build_occlusions(conf["shapes"], True)
assert "polygon:left=.5:top=.5:points=.5,.5 .7,.5 .6,.8:oi=1" in again and "text:left=.1:top=.8:text=Aorta" in again
ok("polygons, text labels and old pixel positions from desktop Anki load and save back")

# --- cards draw their masks ---
with webapp.collection("tester") as col:
    card = col.get_note(nid).cards()[0]
    doc = webapp.card_doc(card.question())
    assert "window.anki.imageOcclusion={setup:setup}" in doc and doc.index("imageOcclusion={setup") < doc.index("anki.imageOcclusion.setup();")
    assert "--active-shape-color" in doc and "#image-occlusion-canvas" in doc
    assert "imageOcclusion={setup" not in webapp.card_doc("<b>plain card</b>")
    col.decks.select(deck)
r = c.get(f"/study/{deck}")
assert r.status_code == 200 and "imageOcclusion={setup" in r.text and "image-occlusion-container" in r.text
ok("cards get Anki's imageOcclusion.setup() so the study screen draws the masks (and Toggle Masks works)")
print("ALL IMAGE OCCLUSION TESTS PASSED")
