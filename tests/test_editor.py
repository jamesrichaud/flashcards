"""Rich editor for Add/Edit: toolbar, media upload, duplicate/cloze check, preview, sticky fields,
MathJax in cards."""
import html, json, os, re, sys, tempfile
os.environ["DATA_DIR"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from urllib.parse import unquote
from fastapi.testclient import TestClient
import app as webapp

ok = lambda m: print("PASS", m)
c = TestClient(webapp.app)

# --- login needed ---
assert c.get("/add", follow_redirects=False).status_code == 303
for url in ("/media/upload", "/note/check", "/note/preview", "/note/sticky"):
    assert c.post(url, data={}).status_code == 401, url
ok("editor routes need login")

c.post("/login", data={"username": "tester", "password": "secret123"})
PNG = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                    "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082")


def flash(r):
    m = re.search(r'<div class="flash( err)?">(.*?)</div>', r.text, re.S)
    return ("!" if m and m.group(1) else "") + html.unescape(m.group(2)) if m else ""


def model(name):
    with webapp.collection("tester") as col:
        return col.models.by_name(name)


basic, cloze, rev = model("Basic")["id"], model("Cloze")["id"], model("Basic (and reversed card)")["id"]

# --- the page ---
r = c.get(f"/add?nt={basic}")
assert r.status_code == 200
assert 'id="noteform"' in r.text and 'role="toolbar"' in r.text
for cmd in ("bold", "italic", "underline", "subscript", "superscript", "foreColor", "hiliteColor",
            "insertUnorderedList", "insertOrderedList", "removeFormat"):
    assert f'data-cmd="{cmd}"' in r.text, cmd
for act in ("cloze", "cloze-same", "mj-inline", "mj-block", "attach", "record", "preview"):
    assert f'data-act="{act}"' in r.text, act
assert "Cloze deletion (new card) (Ctrl+Shift+C)" in r.text and "Cloze deletion (same card) (Ctrl+Shift+Alt+C)" in r.text
assert '<textarea name="f0"' in r.text and '<textarea name="f1"' in r.text   # works without JavaScript too
assert r.text.count('class="fb pin"') == 2 and r.text.count("htmlbtn") >= 2
conf = json.loads(html.unescape(re.search(r'id="ed-conf">(.*?)</script>', r.text, re.S).group(1)))
assert conf["ntid"] == basic and conf["adding"] is True and conf["cloze"] is False
assert json.loads(re.search(r'id="ed-conf">(.*?)</script>', c.get(f"/add?nt={cloze}").text, re.S).group(1))["cloze"]
assert '<script>' in r.text and "execCommand" in r.text
ok("add page has the formatting toolbar, per-field HTML editor and sticky pins, and plain textareas underneath")

# field options from the note type: font, size, RTL, HTML editor by default, collapsed, description
with webapp.collection("tester") as col:
    m = col.models.by_name("Basic")
    m["flds"][1].update(font="Georgia", size=28, rtl=True, plainText=True, collapsed=True, description="The answer")
    col.models.update_dict(m)
r = c.get(f"/add?nt={basic}")
fld1 = re.search(r'<div class="fld collapsed" data-name="Back"[^>]*>', r.text, re.S)
assert fld1, "Back field should start collapsed"
assert 'data-font="Georgia"' in fld1.group(0) and 'data-size="28"' in fld1.group(0)
assert 'data-rtl="1"' in fld1.group(0) and 'data-plain="1"' in fld1.group(0)
assert 'placeholder="The answer"' in r.text
with webapp.collection("tester") as col:
    m = col.models.by_name("Basic")
    m["flds"][1].update(font="Arial", size=20, rtl=False, plainText=False, collapsed=False, description="")
    col.models.update_dict(m)
ok("field font, size, RTL, HTML-editor-by-default, collapsed and description come from the note type")

# --- media upload: paste, attach, record ---
r = c.post("/media/upload", files={"file": ("dot.png", PNG, "image/png")})
assert r.status_code == 200, r.text
j = r.json()
assert j["html"] == f'<img src="{j["name"]}">' and j["name"].startswith("dot")
with webapp.collection("tester") as col:
    folder = col.media.dir()
assert open(os.path.join(folder, j["name"]), "rb").read() == PNG
r = c.post("/media/upload", files={"file": ("image.png", PNG, "image/png")}, data={"pasted": "1"})
assert re.fullmatch(r"paste-[0-9a-f]{40}\.png", r.json()["name"]), r.json()
r = c.post("/media/upload", files={"file": ("rec-20261107-010203.webm", b"\x1aE\xdf\xa3fake", "audio/webm;codecs=opus")})
assert r.json()["html"] == "[sound:rec-20261107-010203.webm]"
r = c.post("/media/upload", files={"file": ("clip.mp4", b"fakevideo", "video/mp4")})
assert r.json()["html"] == "[sound:clip.mp4]"
r = c.post("/media/upload", files={"file": ("blob", PNG, "image/jpeg")})
assert r.json()["name"].endswith(".jpg")
r = c.post("/media/upload", files={"file": ("a b&c.png", PNG, "image/png")})
assert r.json()["html"] == '<img src="a b&amp;c.png">'
assert c.get("/media/" + "a%20b%26c.png").content == PNG
r = c.post("/media/upload", files={"file": ("notes.exe", b"MZ", "application/octet-stream")})
assert r.status_code == 415
r = c.post("/media/upload", files={"file": ("empty.png", b"", "image/png")})
assert r.status_code == 400
r = c.post("/media/upload", files={"file": ("../../evil.png", PNG, "image/png")})
assert r.status_code == 200 and "/" not in r.json()["name"] and ".." not in r.json()["name"]
assert not os.path.exists(os.path.join(os.path.dirname(folder), "evil.png"))
ok("upload saves media and returns <img> for pictures, [sound:] for audio/video/recordings; pasted names use the checksum")

# --- duplicate and cloze check ---
r = c.post("/add", data={"ntid": basic, "did": 1, "f0": "<b>chat</b>", "f1": "cat", "tags": "animals"})
assert flash(r) == "Added 1 card(s).", flash(r)
with webapp.collection("tester") as col:
    nid = col.find_notes('"chat"')[0]
j = c.post("/note/check", data={"ntid": basic, "f0": "<b>chat</b>", "f1": "x"}).json()
assert j["state"] == "duplicate" and j["msg"] == "Show Duplicates", j
q = unquote(j["dupes"].split("q=", 1)[1].replace("+", " "))
assert q.startswith(f"dupe:{basic},"), q
r = c.get(j["dupes"])
assert "/edit/%d" % nid in r.text, "Show Duplicates search should find the note"
assert c.post("/note/check", data={"ntid": basic, "f0": "chat", "f1": ""}).json()["state"] == "duplicate"
assert c.post("/note/check", data={"nid": nid, "f0": "<b>chat</b>", "f1": "cat"}).json()["state"] == "normal"
assert c.post("/note/check", data={"ntid": basic, "f0": "chien"}).json()["state"] == "normal"
assert c.post("/note/check", data={"ntid": basic, "f0": ""}).json()["state"] == "empty"
j = c.post("/note/check", data={"ntid": basic, "f0": "{{c1::x}}"}).json()
assert j["state"] == "notetype_not_cloze" and j["msg"] == "Cloze deletion can only be used on cloze note types."
assert c.post("/note/check", data={"ntid": cloze, "f0": "no cloze"}).json()["state"] == "missing_cloze"
assert c.post("/note/check", data={"ntid": cloze, "f0": "x", "f1": "{{c1::y}}"}).json()["state"] == "field_not_cloze"
assert c.post("/note/check", data={"ntid": cloze, "f0": "{{c1::y}}"}).json()["state"] == "normal"
assert c.post("/note/check", data={"ntid": 999}).status_code == 404
ok("check finds duplicates (not the note itself) with a dupe: search, and reports empty/cloze problems in Anki's words")

# duplicates can still be added, as in Anki
r = c.post("/add", data={"ntid": basic, "did": 1, "f0": "<b>chat</b>", "f1": "cat again"})
assert flash(r) == "Added 1 card(s)."

# --- add: refused input comes back; missing cloze asks to confirm ---
r = c.post("/add", data={"ntid": basic, "did": 1, "f0": "{{c1::Paris}}", "f1": "kept back", "tags": "geo"})
assert flash(r).startswith("!Could not add: Cloze deletion can only be used on cloze")
assert "kept back" in r.text and "{{c1::Paris}}" in r.text and 'value="geo"' in r.text
assert "kept back" not in c.get(f"/add?nt={basic}").text   # the draft is used once
r = c.post("/add", data={"ntid": cloze, "did": 1, "f0": "Paris is in France"})
assert "Could not add: You have a cloze note type but have not made any cloze deletions" in flash(r)
assert 'name="confirm_cloze" value="1"' in r.text and "Paris is in France" in r.text
r = c.post("/add", data={"ntid": cloze, "did": 1, "f0": "Paris is in France", "confirm_cloze": "1"})
assert flash(r) == "Added 1 card(s).", flash(r)
r = c.post("/add", data={"ntid": basic, "did": 1, "f0": "<br>", "f1": "x"})
assert flash(r) == "!Could not add: The first field is empty."
ok("refused adds keep the typed fields and tags; a cloze note without clozes needs 'Add anyway'")

# --- tags stay, sticky fields stay after Add ---
r = c.post("/add", data={"ntid": basic, "did": 1, "f0": "un", "f1": "one", "tags": "french numbers"})
assert 'value="french numbers"' in r.text and ">un</textarea>" not in r.text
r = c.post("/note/sticky", data={"ntid": basic, "idx": 1})
assert r.json() == {"sticky": True}
assert model("Basic")["flds"][1]["sticky"] is True
r = c.post("/add", data={"ntid": basic, "did": 1, "f0": "deux", "f1": "<i>number</i>", "tags": "french"})
assert flash(r) == "Added 1 card(s)."
assert "&lt;i&gt;number&lt;/i&gt;</textarea>" in r.text and ">deux</textarea>" not in r.text
assert re.search(r'class="fb pin"[^>]*aria-pressed="true"', r.text)
assert c.post("/note/sticky", data={"ntid": basic, "idx": 1}).json() == {"sticky": False}
assert "number</textarea>" not in c.get(f"/add?nt={basic}").text
assert c.post("/note/sticky", data={"ntid": basic, "idx": 9}).status_code == 404
assert c.post("/note/sticky", data={"ntid": "x", "idx": 0}).status_code == 404
ok("tags are kept after Add, sticky (pinned) fields keep their content, and the pin is saved in the note type")

# --- preview ---
j = c.post("/note/preview", data={"ntid": rev, "f0": "maison", "f1": "house"}).json()
assert [x["name"] for x in j["cards"]] == ["Card 1", "Card 2"]
assert "maison" in j["cards"][0]["q"] and "house" in j["cards"][0]["a"] and "house" in j["cards"][1]["q"]
assert j["cards"][0]["q"].startswith("<!doctype html>")
j = c.post("/note/preview", data={"ntid": cloze, "f0": "{{c1::Paris}} is in {{c3::France}}"}).json()
assert [x["name"] for x in j["cards"]] == ["Cloze 1", "Cloze 3"]
assert "[...]" in j["cards"][0]["q"] and "France" in j["cards"][0]["q"] and "Paris" in j["cards"][0]["a"]
opt = model("Basic (optional reversed card)")["id"]
j = c.post("/note/preview", data={"ntid": opt, "f0": "a", "f1": "b", "f2": ""}).json()
assert [x["name"] for x in j["cards"]] == ["Card 1"]       # no reverse card without "Add Reverse"
j = c.post("/note/preview", data={"ntid": opt, "f0": "a", "f1": "b", "f2": "y"}).json()
assert len(j["cards"]) == 2
j = c.post("/note/preview", data={"nid": nid, "f0": "changed", "f1": "cat"}).json()
assert "changed" in j["cards"][0]["q"]
with webapp.collection("tester") as col:
    assert col.get_note(nid).fields[0] == "<b>chat</b>"   # the preview saves nothing
j = c.post("/note/preview", data={"ntid": basic, "f0": "x [sound:a.mp3]", "f1": "y"}).json()
assert "replay-button" in j["cards"][0]["q"] and "[sound:" not in j["cards"][0]["q"]
typ = model("Basic (type in the answer)")["id"]
j = c.post("/note/preview", data={"ntid": typ, "f0": "chien", "f1": "dog"}).json()
assert 'id="typeans"' in j["cards"][0]["q"] and "[[type:" not in j["cards"][0]["q"]
ok("preview renders each card the note would make (cloze numbers, optional reverse, audio, type-in) without saving")

# --- MathJax ---
j = c.post("/note/preview", data={"ntid": basic, "f0": r"Solve \(x^2 = 4\)", "f1": r"\[x = \pm 2\]"}).json()
assert "mathjax@3" in j["cards"][0]["q"] and r"\(x^2 = 4\)" in j["cards"][0]["q"]
assert '"\\\\["' in j["cards"][0]["q"]                 # display maths uses \[ \], as in Anki
assert "mathjax" not in c.post("/note/preview", data={"ntid": basic, "f0": "[x] (y)", "f1": "z"}).json()["cards"][0]["q"]
assert "fc-resize" in webapp.card_doc(r"\(a\)")
c.post("/deck/new", data={"name": "Maths"})
with webapp.collection("tester") as col:
    did = col.decks.id_for_name("Maths")
c.post("/add", data={"ntid": basic, "did": did, "f0": r"\(\sqrt{9}\)", "f1": "3"})
r = c.get(f"/study/{did}")
assert "mathjax@3" in r.text and "fc-resize" in r.text
ok("cards with \\( \\) or \\[ \\] load MathJax (study screen and previews) and resize when typeset")

# --- edit page ---
r = c.get(f"/edit/{nid}?back=/browse")
assert r.status_code == 200 and 'role="toolbar"' in r.text and 'class="fb pin"' not in r.text
conf = json.loads(re.search(r'id="ed-conf">(.*?)</script>', r.text, re.S).group(1))
assert conf["nid"] == nid and conf["adding"] is False
assert '<input type="hidden" name="nid" value="%d">' % nid in r.text
assert "&lt;b&gt;chat&lt;/b&gt;</textarea>" in r.text
assert 'select disabled aria-label="Note type"' in r.text
assert "Change note type" in r.text and "Card info" in r.text
img = c.post("/media/upload", files={"file": ("pic.png", PNG, "image/png")}).json()["html"]
r = c.post(f"/edit/{nid}?back=/browse", data={"ntid": basic, "did": 1, "nid": nid, "f0": "<b>chat</b>" + img,
                                               "f1": "<ul><li>cat</li></ul>", "tags": "animals"})
assert flash(r) == "Saved."
with webapp.collection("tester") as col:
    n = col.get_note(nid)
    assert n.fields == ['<b>chat</b><img src="pic.png">', "<ul><li>cat</li></ul>"]
r = c.get("/edit/123456789?back=/browse")
assert flash(r) == "!That note no longer exists."
ok("edit page uses the same editor (no sticky pins), saves formatted HTML and media, handles a missing note")

# --- the no-JavaScript file field still works ---
r = c.post("/add", data={"ntid": basic, "did": 1, "f0": "old way", "f1": "x"},
           files={"upload": ("old.png", PNG, "image/png")})
assert flash(r) == "Added 1 card(s)."
with webapp.collection("tester") as col:
    assert col.get_note(col.find_notes('"old way*"')[0]).fields[0].startswith('old way<img src="old')
ok("the plain file field (for browsers without JavaScript) still attaches a picture")

print("ALL EDITOR TESTS PASSED")
