"""Note type manager: list, add from stock/clone, rename, delete, fields, card templates with
live preview, and changing the note type of existing notes."""
import html, json, os, re, sys, tempfile
os.environ["DATA_DIR"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fastapi.testclient import TestClient
import app as webapp

ok = lambda m: print("PASS", m)
c = TestClient(webapp.app)
c.post("/login", data={"username": "tester", "password": "secret123"})


def flash(r):
    m = re.search(r'<div class="flash( err)?">(.*?)</div>', r.text, re.S)
    return ("!" if m and m.group(1) else "") + html.unescape(m.group(2)) if m else ""


def nt_id(name):
    with webapp.collection("tester") as col:
        return col.models.id_for_name(name)


def nt(name):
    with webapp.collection("tester") as col:
        return col.models.get(col.models.id_for_name(name))


def add_note(ntname, *vals, tags=""):
    with webapp.collection("tester") as col:
        m = col.models.by_name(ntname)
        note = col.new_note(m)
        for i, v in enumerate(vals):
            note.fields[i] = v
        note.tags = tags.split()
        col.add_note(note, 1)
        return note.id


# --- links and list ---
r = c.get("/settings")
assert 'href="/notetypes"' in r.text
r = c.get("/add")
assert re.search(r'href="/notetypes/\d+/cards"', r.text)
r = c.get("/notetypes")
assert r.status_code == 200
for name in ("Basic", "Basic (and reversed card)", "Basic (optional reversed card)", "Basic (type in the answer)",
             "Cloze", "Image Occlusion"):
    assert html.escape(name) in r.text, name
    assert f"Add: {html.escape(name)}" in r.text, name
assert "Clone: Basic" in r.text
ok("settings and add page link to the note type manager; list shows all types and stock choices")

# --- add from stock and clone ---
r = c.post("/notetypes/add", data={"base": "stock:4", "name": "My Cloze"})
assert r.status_code == 200 and "Fields for My Cloze" in r.text, r.text[:500]
m = nt("My Cloze")
assert m and m["type"] == 1 and [f["name"] for f in m["flds"]] == ["Text", "Back Extra"]
r = c.post("/notetypes/add", data={"base": "stock:5", "name": ""})
assert "Image Occlusion-" in flash(r) or "Image Occlusion" in flash(r)
with webapp.collection("tester") as col:
    ios = [x for x in col.models.all_names_and_ids() if x.name.startswith("Image Occlusion")]
    assert len(ios) == 2 and len({x.name for x in ios}) == 2, ios   # duplicate names are made unique
    io_new = [col.models.get(x.id) for x in ios if x.name != "Image Occlusion"][0]
    assert io_new["originalStockKind"] == 6   # Anki's image occlusion kind, so real Anki recognises it
r = c.post("/notetypes/add", data={"base": f"clone:{nt_id('Basic')}", "name": "Vocab"})
v = nt("Vocab")
assert v and [f["name"] for f in v["flds"]] == ["Front", "Back"] and v["id"] != nt_id("Basic")
r = c.post("/notetypes/add", data={"base": "nonsense"})
assert flash(r).startswith("!")
ok("add from every stock type (incl. Image Occlusion) and clone an existing one; names made unique")

# --- rename and delete ---
r = c.post(f"/notetypes/{v['id']}/rename", data={"name": "Vocabulary"})
assert "Renamed" in flash(r) and nt("Vocabulary")
r = c.post(f"/notetypes/{v['id']}/rename", data={"name": "Basic"})
assert nt_id("Basic") != v["id"]
with webapp.collection("tester") as col:
    vname = col.models.get(v["id"])["name"]
assert vname != "Basic" and vname.startswith("Basic"), vname    # not two types with the same name
r = c.post(f"/notetypes/{v['id']}/rename", data={"name": "Vocabulary"})
gone = add_note("Vocabulary", "x", "y")
r = c.post(f"/notetypes/{v['id']}/delete")
assert "Deleted" in flash(r)
with webapp.collection("tester") as col:
    assert not col.models.get(v["id"])
    assert not col.find_notes(f"nid:{gone}")
ok("rename (unique names) and delete a note type with its notes")

# --- fields ---
v = nt_id("My Cloze")
n1 = add_note("My Cloze", "{{c1::Paris}} is in {{c2::France}}", "extra")
fid = lambda: [f["name"] for f in nt("My Cloze")["flds"]]
r = c.get(f"/notetypes/{v}/fields")
assert r.status_code == 200 and "Text" in r.text and "Back Extra" in r.text and "sort field" in r.text
r = c.post(f"/notetypes/{v}/fields", data={"op": "add", "name": "Source"})
assert "Added field" in flash(r) and fid() == ["Text", "Back Extra", "Source"]
r = c.post(f"/notetypes/{v}/fields", data={"op": "add", "name": "source"})
assert "already used" in flash(r) and len(fid()) == 3
r = c.post(f"/notetypes/{v}/fields", data={"op": "add", "name": '#{{Bad:"}}'})
assert fid()[-1] == "Bad", fid()
r = c.post(f"/notetypes/{v}/fields", data={"op": "delete", "idx": "3"})
assert fid() == ["Text", "Back Extra", "Source"]
r = c.post(f"/notetypes/{v}/fields", data={"op": "rename", "idx": "1", "name": "Notes"})
assert fid() == ["Text", "Notes", "Source"]
m = nt("My Cloze")
assert "{{Notes}}" in m["tmpls"][0]["afmt"] and "Back Extra" not in m["tmpls"][0]["afmt"]   # templates follow
with webapp.collection("tester") as col:
    assert col.get_note(n1)["Notes"] == "extra"
r = c.post(f"/notetypes/{v}/fields", data={"op": "move", "idx": "2", "to": "0"})
assert fid() == ["Source", "Text", "Notes"]
with webapp.collection("tester") as col:
    note = col.get_note(n1)
    assert note["Text"].startswith("{{c1::Paris}}") and note["Notes"] == "extra"   # content moved with the field
r = c.post(f"/notetypes/{v}/fields", data={"op": "options", "idx": "1", "font": "Georgia", "size": "28",
                                            "description": "The sentence", "sort": "1", "rtl": "1", "sticky": "1"})
m = nt("My Cloze")
f = m["flds"][1]
assert (f["font"], f["size"], f["description"], f["rtl"], f["sticky"]) == ("Georgia", 28, "The sentence", True, True)
assert m["sortf"] == 1
r = c.post(f"/notetypes/{v}/fields", data={"op": "options", "idx": "1", "font": "", "size": "9999"})
f = nt("My Cloze")["flds"][1]
assert f["font"] == "Arial" and f["size"] == 300 and not f["rtl"]
r = c.post(f"/notetypes/{nt_id('Basic')}/fields", data={"op": "delete", "idx": "9"})
assert flash(r).startswith("!")
one = nt_id("Basic (type in the answer)")
c.post(f"/notetypes/{one}/fields", data={"op": "delete", "idx": "1"})
r = c.post(f"/notetypes/{one}/fields", data={"op": "delete", "idx": "0"})
assert "at least one field" in flash(r) and len(nt("Basic (type in the answer)")["flds"]) == 1
ok("fields: add, rename (templates updated), reorder (content kept), delete, sort field, font/size/options, name rules")

# --- card templates: page, live preview, save, errors ---
b = nt_id("Basic")
bn = add_note("Basic", "What is <b>2+2</b>?", "four")
r = c.get(f"/notetypes/{b}/cards")
assert r.status_code == 200
assert "Front template" in r.text and "Back template" in r.text and "Styling" in r.text
assert "{{Front}}" in html.unescape(r.text)
src = html.unescape(re.search(r'id="pq"[^>]*srcdoc="([^"]*)"', r.text).group(1))
assert "<b>2+2</b>" in src and ".card {" in src.replace(".card{", ".card {")
r = c.post(f"/notetypes/{b}/preview", data={"ord": "0", "qfmt": "Q: {{Front}}!", "afmt": "{{FrontSide}}<hr id=answer>A: {{Back}}",
                                            "css": ".card{color:red}", "nid": "0"})
j = r.json()
assert "Q: What is <b>2+2</b>?!" in j["q"] and "A: four" in j["a"] and "color:red" in j["q"]
assert nt("Basic")["tmpls"][0]["qfmt"] == "{{Front}}"    # preview doesn't save
r = c.post(f"/notetypes/{b}/preview", data={"ord": "0", "qfmt": "{{Nope}}", "afmt": "", "css": ""})
assert "no field called" in r.json()["q"]
# blank note when the type has no notes: fields shown as (Field)
r = c.post(f"/notetypes/{nt_id('Basic (and reversed card)')}/preview",
           data={"ord": "1", "qfmt": "{{Back}}", "afmt": "{{Front}}", "css": ""})
assert "(Back)" in r.json()["q"] and "(Front)" in r.json()["a"]
# type-in answer box shows in the preview
r = c.post(f"/notetypes/{b}/preview", data={"ord": "0", "qfmt": "{{Front}} {{type:Back}}", "afmt": "{{type:Back}}", "css": ""})
assert 'id="typeans"' in r.json()["q"] and "[[type:" not in r.json()["a"]
r = c.post(f"/notetypes/{b}/cards", data={"ord": "0", "qfmt": "Q: {{Front}}", "afmt": "{{FrontSide}}<hr id=answer>{{Back}}",
                                          "css": ".card { font-size: 30px; }"})
assert flash(r) == "Saved."
m = nt("Basic")
assert m["tmpls"][0]["qfmt"] == "Q: {{Front}}" and "30px" in m["css"]
with webapp.collection("tester") as col:
    assert "Q: What is" in col.get_note(bn).cards()[0].question()
r = c.post(f"/notetypes/{b}/cards", data={"ord": "0", "qfmt": "no fields here", "afmt": "{{Back}}", "css": ".x{}"})
assert flash(r).startswith("!") and "field replacement" in flash(r)
assert "no fields here" in r.text and ".x{}" in r.text      # what was typed is kept
assert nt("Basic")["tmpls"][0]["qfmt"] == "Q: {{Front}}"      # nothing saved
r = c.get(f"/notetypes/{b}/cards?nid={bn}")
assert "the note “What is 2+2 ?”" in r.text
ok("template editor: shows templates and CSS, live preview (note or blank), type-in box, save, errors keep edits")

# --- card types: add, rename, move, delete ---
r = c.post(f"/notetypes/{b}/cards/op", data={"ord": "0", "op": "add"})
m = nt("Basic")
assert len(m["tmpls"]) == 2 and m["tmpls"][1]["name"] == "Card 2" and m["tmpls"][1]["qfmt"].startswith("Q: {{Front}}")
assert "ord=1" in str(r.url)
with webapp.collection("tester") as col:
    assert len(col.get_note(bn).cards()) == 2     # engine generated the new card
c.post(f"/notetypes/{b}/cards/op", data={"ord": "1", "op": "rename", "name": "Reverse"})
r = c.post(f"/notetypes/{b}/cards/op", data={"ord": "1", "op": "rename", "name": "Card 1"})
assert "already used" in flash(r)
c.post(f"/notetypes/{b}/cards/op", data={"ord": "1", "op": "move", "to": "0"})
assert [t["name"] for t in nt("Basic")["tmpls"]] == ["Reverse", "Card 1"]
r = c.post(f"/notetypes/{b}/cards/op", data={"ord": "0", "op": "delete"})
assert [t["name"] for t in nt("Basic")["tmpls"]] == ["Card 1"]
with webapp.collection("tester") as col:
    assert len(col.get_note(bn).cards()) == 1
r = c.post(f"/notetypes/{b}/cards/op", data={"ord": "0", "op": "delete"})
assert "At least one card type" in flash(r)
r = c.post(f"/notetypes/{v}/cards/op", data={"ord": "0", "op": "add"})
assert "Cloze" in flash(r) and len(nt("My Cloze")["tmpls"]) == 1
r = c.get(f"/notetypes/{v}/cards")
assert "Cloze 1" in r.text and "Cloze 2" in r.text
r = c.post(f"/notetypes/{v}/preview", data={"ord": "0", "cloze": "2", "qfmt": "{{cloze:Text}}", "afmt": "{{cloze:Text}}", "css": ""})
shown = webapp.strip_html(r.json()["q"])
assert "Paris" in shown and "France" not in shown and "[...]" in shown, shown
ok("card types: add (copies current), rename, reorder, delete (cards follow); cloze has one type, preview per cloze")

# --- change note type ---
n2 = add_note("Basic", "dog", "chien", tags="animals")
n3 = add_note("Basic", "cat", "chat", tags="animals")
rev = nt_id("Basic (and reversed card)")
r = c.get(f"/edit/{n2}")
assert f"/notetypes/change?nids={n2}" in r.text
r = c.get(f"/notetypes/change?nids={n2},{n3}&new={rev}")
assert r.status_code == 200 and "2 notes" in r.text and "Takes content from" in r.text and "Card 2" in r.text
r = c.post("/notetypes/change", data={"nids": f"{n2},{n3}", "old": str(b), "new": str(rev),
                                      "f0": "1", "f1": "0", "t0": "0", "t1": "-1", "back": "/browse"})
assert "Changed 2 notes" in flash(r), flash(r)
with webapp.collection("tester") as col:
    note = col.get_note(n2)
    assert note.mid == rev and note["Front"] == "chien" and note["Back"] == "dog"   # swapped as mapped
    assert len(note.cards()) == 2 and "animals" in note.tags
# mixed note types are refused
r = c.get(f"/notetypes/change?nids={n1},{bn}")
assert "only one note type" in flash(r)
# from Browse: bulk action opens the page; "all in search" passes the search
r = c.post("/browse/bulk", data={"mode": "notes", "q": "tag:animals", "all": "1", "op": "notetype", "back": "/browse?q=tag:animals"},
           follow_redirects=False)
assert r.status_code == 303 and r.headers["location"].startswith("/notetypes/change?q=tag%3Aanimals")
r = c.get(r.headers["location"])
assert "2 notes" in r.text
r = c.post("/browse/bulk", data={"mode": "cards", "q": "", "ids": [str(c_) for c_ in []], "op": "notetype", "back": "/browse"})
assert "Nothing selected" in flash(r)
# to and from cloze
r = c.post("/notetypes/change", data={"q": "tag:animals", "old": str(rev), "new": str(v), "f0": "-1", "f1": "0", "f2": "1"})
assert "Changed 2 notes" in flash(r), flash(r)
with webapp.collection("tester") as col:
    note = col.get_note(n3)
    assert note.mid == v and note["Text"] == "chat" and note["Notes"] == "cat" and note["Source"] == ""
ok("change note type: mapping page, fields/cards remapped by the engine, mixed types refused, Browse entry, to cloze")

# --- login required, unknown ids ---
anon = TestClient(webapp.app)
for url in ("/notetypes", f"/notetypes/{b}/fields", f"/notetypes/{b}/cards", "/notetypes/change?nids=1"):
    r = anon.get(url, follow_redirects=False)
    assert r.status_code in (302, 303, 307) or "login" in r.text.lower(), url
assert anon.post(f"/notetypes/{b}/preview", data={}).status_code == 401
r = c.get("/notetypes/123/fields")
assert "Note types" in r.text
ok("login required; unknown note type goes back to the list")
print("all note type tests passed")
