"""Find Duplicates (Browse): field list, engine grouping, search filter, Browse/edit links, Tag Duplicates, undo."""
import html, os, re, sys, tempfile
os.environ["DATA_DIR"] = tempfile.mkdtemp()
os.environ["REMINDERS"] = "0"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from urllib.parse import urlsplit, parse_qs
from fastapi.testclient import TestClient
import app as webapp
from flashcards import find_dupes as fd

ok = lambda m: print("PASS", m)
c = TestClient(webapp.app)

r = c.get("/browse/dupes", follow_redirects=False)
assert r.status_code == 303 and r.headers["location"] == "/login"
ok("needs a login")

c.post("/login", data={"username": "tester", "password": "secret123"})
c.post("/deck/new", data={"name": "French"})
c.post("/deck/new", data={"name": "Spanish"})
with webapp.collection("tester") as col:
    fr, es = col.decks.id_for_name("French"), col.decks.id_for_name("Spanish")
    basic = col.models.by_name("Basic")["id"]
    rev = col.models.by_name("Basic (and reversed card)")["id"]
    cloze = col.models.by_name("Cloze")["id"]
for nt, did, f0, f1 in [(basic, fr, "chat", "cat"), (basic, fr, "<b>chat</b>", "cat 2"), (rev, es, "chat", "x"),
                        (basic, es, "perro", "dog"), (basic, es, "perro", "dog again"), (basic, fr, "unique", "u")]:
    c.post("/add", data={"ntid": nt, "did": did, "f0": f0, "f1": f1, "tags": ""})
c.post("/add", data={"ntid": cloze, "did": fr, "f0": "{{c1::x}}", "f1": "", "tags": ""})
with webapp.collection("tester") as col:
    for back in ("blank 1", "blank 2"):   # the Add page refuses a blank first field; the engine allows it
        n = col.new_note(col.models.get(basic)); n["Back"] = back; col.add_note(n, fr)
with webapp.collection("tester") as col:
    total_notes = col.note_count()
    chat = sorted(col.find_notes("front:*chat*"))
    perro = sorted(col.find_notes("front:perro"))
assert total_notes == 9 and len(chat) == 3 and len(perro) == 2, (total_notes, chat, perro)

def groups(text):
    """[(value, [nid...], browse search)] from the results page."""
    out = []
    for g in re.findall(r'<div class="box dgroup">(.*?)</div></div>', text, re.S):
        val = html.unescape(re.search(r'<b class="dval">(.*?)</b>', g, re.S).group(1))
        href = html.unescape(re.search(r'<a class="btn" href="([^"]+)">\d+ notes in Browse', g).group(1))
        nids = sorted(int(n) for n in re.findall(r'href="/edit/(\d+)\?', g))
        out.append((val, nids, parse_qs(urlsplit(href).query)["q"][0]))
    return out

def flash(r):
    m = re.search(r'<div class="flash( err)?">(.*?)</div>', r.text)
    return ("!" if m.group(1) else "") + html.unescape(m.group(2)) if m else ""

# --- Browse button and the empty form ---
r = c.get("/browse", params={"q": "deck:Spanish"})
m = re.search(r'href="(/browse/dupes\?[^"]+)"[^>]*>Find duplicates</a>', r.text)
assert m, "Browse has a Find duplicates button"
r = c.get(html.unescape(m.group(1)))
assert r.status_code == 200 and "<h2" in r.text and "Find Duplicates" in r.text
assert 'value="deck:Spanish"' in r.text, "Browse search pre-fills the filter"
assert "box dgroup" not in r.text, "nothing runs until Search"
opts = re.findall(r'<option value="([^"]*)"', r.text)
assert opts == sorted(opts, key=str.lower) and len(opts) == len({o.lower() for o in opts})
for f in ("Front", "Back", "Text", "Back Extra"):
    assert f in opts, f
assert opts.index("Back") < opts.index("Front")
ok("Browse links to Find duplicates; field list = every note type's fields, sorted, no repeats; search pre-filled")

# --- results ---
r = c.get("/browse/dupes", params={"field": "Front", "q": ""})
assert r.status_code == 200
g = groups(r.text)
assert [(v, n) for v, n, _ in g] == [("chat", chat), ("perro", perro)], g
assert "2</b> groups · 5 notes" in r.text
with webapp.collection("tester") as col:
    expect = {v: n for v, n in col.find_dupes("Front", "")}
    for v, nids, search in g:
        assert sorted(expect[v]) == nids
        assert sorted(col.find_notes(search)) == nids, "Browse link finds exactly the group"
assert "French · Basic" in r.text and "Spanish · Basic (and reversed card)" in r.text
assert "unique" not in r.text.split("box dgroup", 1)[1] and "blank" not in r.text
ok("groups match the engine's find_dupes (HTML ignored, across note types, blanks skipped); Browse links find each group")

r2 = c.get("/browse/dupes", params={"field": "front"})
assert len(groups(r2.text)) == 2
ok("field name is case-insensitive, like Anki")

r = c.get("/browse/dupes", params={"field": "Front", "q": "deck:Spanish"})
g = groups(r.text)
assert [(v, n) for v, n, _ in g] == [("perro", perro)], g
ok("optional search narrows the notes")

r = c.get("/browse/dupes", params={"field": "Back"})
assert "No duplicates found" in r.text and "box dgroup" not in r.text
r = c.get("/browse/dupes", params={"field": "Front", "q": "bad(("})
assert r.status_code == 200 and '<div class="flash err">' in r.text and "bracket" in r.text
r = c.get("/browse/dupes", params={"field": "Nope"})
assert r.status_code == 200 and "No note type has a field called" in r.text
r = c.get("/browse/dupes", params={"field": "<script>x</script>"})
assert "<script>x</script>" not in r.text
ok("no duplicates, bad search, unknown field and odd input are handled")

# last used field and search are remembered
c.get("/browse/dupes", params={"field": "Front", "q": "deck:French"})
r = c.get("/browse/dupes")
assert re.search(r'<option value="Front" selected>', r.text) and 'value="deck:French"' in r.text
ok("remembers the last field and filter")

# --- edit link comes back here ---
r = c.get("/browse/dupes", params={"field": "Front", "q": ""})
edit = html.unescape(re.search(r'href="(/edit/\d+\?back=[^"]+)"', r.text).group(1))
assert c.get(edit).status_code == 200
assert "/browse/dupes" in parse_qs(urlsplit(edit).query)["back"][0]
ok("each note links to its editor, which returns to the results")

# --- Tag Duplicates ---
r = c.post("/browse/dupes/tag", data={"field": "Front", "q": ""}, follow_redirects=False)
assert r.status_code == 303 and r.headers["location"].startswith("/browse/dupes?field=Front")
r = c.get(r.headers["location"])
assert "5" in flash(r) and not flash(r).startswith("!"), flash(r)
with webapp.collection("tester") as col:
    tagged = sorted(col.find_notes("tag:duplicate"))
    assert tagged == sorted(chat + perro), tagged
    assert col.undo_status().undo, col.undo_status()
ok("Tag Duplicates adds Anki's 'duplicate' tag to every note in every group")

c.post("/undo")
with webapp.collection("tester") as col:
    assert col.find_notes("tag:duplicate") == []
ok("tagging is one undoable step")

c.post("/browse/dupes/tag", data={"field": "Front", "q": "deck:Spanish"})
with webapp.collection("tester") as col:
    assert sorted(col.find_notes("tag:duplicate")) == perro
ok("Tag Duplicates respects the optional search")

r = c.post("/browse/dupes/tag", data={"field": "Back", "q": ""})
assert flash(r) == "No duplicates to tag."
r = c.post("/browse/dupes/tag", data={"field": "Front", "q": "bad(("})
assert flash(r).startswith("!")
ok("nothing to tag / bad search give a message")

# --- many groups: capped on the page, all tagged ---
with webapp.collection("tester") as col:
    m = col.models.by_name("Basic")
    for i in range(fd.MAX_GROUPS + 3):
        for _ in range(2):
            n = col.new_note(m); n["Front"] = f"w{i}"; n["Back"] = "b"; col.add_note(n, fr)
r = c.get("/browse/dupes", params={"field": "Front", "q": "front:w*"})
assert len(groups(r.text)) == fd.MAX_GROUPS and f"first {fd.MAX_GROUPS} groups of {fd.MAX_GROUPS + 3}" in r.text
c.post("/browse/dupes/tag", data={"field": "Front", "q": "front:w*"})
with webapp.collection("tester") as col:
    assert len(col.find_notes("tag:duplicate front:w*")) == 2 * (fd.MAX_GROUPS + 3)
ok("long result lists are capped on the page but Tag Duplicates tags all of them")

print("ALL FIND DUPLICATES TESTS PASSED")
