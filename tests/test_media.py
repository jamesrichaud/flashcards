"""Media: Check Media (missing/unused files, Tag Missing, Delete Unused, trash) and serving media files."""
import html, os, re, sys, tempfile
os.environ["DATA_DIR"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fastapi.testclient import TestClient
import app as webapp

ok = lambda m: print("PASS", m)
c = TestClient(webapp.app)
c.post("/login", data={"username": "medi", "password": "secret123"})


def flash(r):
    m = re.search(r'<div class="flash( err)?">(.*?)</div>', r.text, re.S)
    return ("!" if m and m.group(1) else "") + html.unescape(m.group(2)) if m else ""


PNG = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                    "1f15c4890000000d49444154789c6360f8cf00000301010018dd8db00000000049454e44ae426082")
MP3 = b"ID3" + bytes(2000)
with webapp.collection("medi") as col:
    for name, data in [("used.png", PNG), ("song.mp3", MP3), ("clip.webm", b"\x1aE\xdf\xa3" + bytes(5000)),
                       ("old1.png", PNG + b"1"), ("old2.jpg", b"jpg"), ("café.png", PNG + b"2"),
                       ("_style.css", b"body{}")]:
        assert col.media.write_data(name, data) == name
    basic = col.models.by_name("Basic")
    basic["css"] += '\n@import "_style.css";'
    col.models.update_dict(basic)
    n = col.new_note(basic); n["Front"] = '<img src="used.png"> [sound:song.mp3] [sound:clip.webm]'
    n["Back"] = '<img src="café.png">'; col.add_note(n, 1)
    m = col.new_note(basic); m["Front"] = 'Lost <img src="gone.png">'; m["Back"] = "[sound:gone.mp3]"; col.add_note(m, 1)
    lost_nid = m.id
    media_dir = col.media.dir()

# ---------- settings links to it ----------
assert 'href="/check-media"' in c.get("/settings").text; ok("settings page links to Check media")
assert c.get("/check-media", follow_redirects=False).status_code == 200
c2 = TestClient(webapp.app)
assert c2.get("/check-media", follow_redirects=False).status_code == 303
assert c2.post("/check-media/delete", data={"all": "1"}, follow_redirects=False).status_code == 303
assert os.path.exists(os.path.join(media_dir, "old1.png")); ok("check media needs a login")

# ---------- report ----------
r = c.get("/check-media")
t = r.text
assert "Check Media" in t
for f in ["gone.png", "gone.mp3", "old1.png", "old2.jpg"]:
    assert f in t, f
assert 'value="old1.png"' in t and 'value="old2.jpg"' in t and 'value="used.png"' not in t
assert 'value="song.mp3"' not in t and 'value="_style.css"' not in t
assert "café.png" not in t.split('id="unused"')[1].split("</form>")[0]
assert "Tag Missing" in t and f"nid%3A{lost_nid}" in t
assert '<div style="font-size:28px;font-weight:600">2</div><div class="muted">Missing files' in t
assert '<div style="font-size:28px;font-weight:600">2</div><div class="muted">Unused files' in t
assert "Full report" in t and "⁨" not in t
ok("report lists missing and unused files (not files used by cards or templates) with a Browse link")

# ---------- Tag Missing ----------
r = c.post("/check-media/tag")
assert "1" in flash(r) and not flash(r).startswith("!"), flash(r)
with webapp.collection("medi") as col:
    assert col.get_note(lost_nid).has_tag("missing-media")
    assert len(col.find_notes("tag:missing-media")) == 1
ok("Tag Missing adds Anki's missing-media tag to notes with missing files")

# ---------- Delete ticked ----------
r = c.post("/check-media/delete", data={"all": "0", "f": ["old1.png", "used.png", "../collection.anki2"]})
assert "1 file" in flash(r), flash(r)
assert not os.path.exists(os.path.join(media_dir, "old1.png"))
assert os.path.exists(os.path.join(media_dir, "used.png")) and os.path.exists(os.path.join(media_dir, "old2.jpg"))
ok("Delete ticked trashes only ticked files that are really unused")
r = c.post("/check-media/delete", data={"all": "0"}); assert flash(r).startswith("!"); ok("nothing ticked is refused")

r = c.get("/check-media")
assert 'id="trash"' in r.text and "Restore Deleted" in r.text and "Empty Trash" in r.text
ok("trash section appears once files are deleted")

# ---------- Restore ----------
r = c.post("/check-media/restore-trash")
assert flash(r) and not flash(r).startswith("!")
assert os.path.exists(os.path.join(media_dir, "old1.png")); ok("Restore Deleted brings files back")

# ---------- Delete all unused, then empty trash ----------
r = c.post("/check-media/delete", data={"all": "1"})
assert "2 files" in flash(r), flash(r)
for f in ["old1.png", "old2.jpg"]:
    assert not os.path.exists(os.path.join(media_dir, f))
for f in ["used.png", "song.mp3", "clip.webm", "café.png", "_style.css"]:
    assert os.path.exists(os.path.join(media_dir, f)), f
ok("Delete Unused (all) trashes every unused file and keeps used ones")
r = c.post("/check-media/empty-trash")
assert flash(r) and not flash(r).startswith("!")
r = c.get("/check-media"); assert 'id="trash"' not in r.text and 'id="unused"' not in r.text
c.post("/check-media/restore-trash")
assert not os.path.exists(os.path.join(media_dir, "old1.png")); ok("Empty Trash removes the files for good")

# ---------- nothing wrong ----------
with webapp.collection("medi") as col:
    col.remove_notes([lost_nid])
r = c.get("/check-media"); assert "All media files are in use and none are missing." in r.text
assert 'id="missing"' not in r.text; ok("clean collection says all is well")

# ---------- serving ----------
r = c.get("/media/used.png"); assert r.content == PNG and r.headers["content-type"] == "image/png"
assert r.headers["x-content-type-options"] == "nosniff"
r = c.get("/media/song.mp3"); assert r.headers["content-type"] == "audio/mpeg" and r.headers.get("accept-ranges") == "bytes"
r = c.get("/media/song.mp3", headers={"Range": "bytes=0-9"})
assert r.status_code == 206 and r.content == MP3[:10] and r.headers["content-range"] == f"bytes 0-9/{len(MP3)}"
ok("audio is served as audio/mpeg with Range support (Safari needs 206)")
r = c.get("/media/clip.webm"); assert r.headers["content-type"] == "video/webm"
r = c.get("/media/_style.css"); assert r.headers["content-type"].startswith("text/css")
ok("video and stylesheet types are right")
r = c.get("/media/caf%C3%A9.png"); assert r.status_code == 200 and r.content == PNG + b"2"
r = c.get("/media/cafe%CC%81.png"); assert r.status_code == 200 and r.content == PNG + b"2"
ok("unicode file names are served, also when the browser sends a decomposed (NFD) name")
with webapp.collection("medi") as col:
    col.media.write_data("evil.html", b"<script>alert(1)</script>")
r = c.get("/media/evil.html"); assert r.headers.get("content-security-policy") == "sandbox"
ok("HTML files from shared decks are sandboxed")
assert c.get("/media/nothere.png").status_code == 404
assert c.get("/media/..%2Fcollection.anki2").status_code == 404
assert c2.get("/media/used.png").status_code == 401; ok("missing files 404, traversal blocked, login needed")

# ---------- media appear in cards ----------
with webapp.collection("medi") as col:
    did = col.decks.id("Media deck")
    n = col.new_note(col.models.by_name("Basic")); n["Front"] = '<img src="used.png"> [sound:song.mp3] [sound:clip.webm]'
    n["Back"] = "b"; col.add_note(n, did)
r = c.get(f"/study/{did}")
assert "/media/song.mp3" in r.text and "/media/clip.webm" in r.text
# the card document is embedded as a JSON string (quotes escaped) and loaded into the card frame
assert 'base href=\\"/media/\\"' in r.text and 'img src=\\"used.png\\"' in r.text
ok("study screen shows images (via the /media/ base) and plays sound and video from /media/")
