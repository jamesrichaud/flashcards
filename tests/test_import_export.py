"""Import/export: .apkg options, text (CSV/TSV/TXT) import with mapping, .colpkg replace, exports."""
import html, io, os, re, sys, tempfile, time, zipfile
os.environ["DATA_DIR"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fastapi.testclient import TestClient
import app as webapp
from anki.collection import Collection, ExportAnkiPackageOptions, CsvMetadata
from anki import import_export_pb2 as pb
from anki.scheduler.v3 import CardAnswer

Update = pb.ImportAnkiPackageUpdateCondition
ok = lambda m: print("PASS", m)
c = TestClient(webapp.app)
c.post("/login", data={"username": "tester", "password": "secret123"})
tmp = tempfile.mkdtemp()


def flash(r):
    m = re.search(r'<div class="flash( err)?">(.*?)</div>', r.text, re.S)
    return ("!" if m and m.group(1) else "") + html.unescape(m.group(2)) if m else ""


def count(search=""):
    with webapp.collection("tester") as col:
        return len(col.find_notes(search))


def notes(search=""):
    with webapp.collection("tester") as col:
        return {col.get_note(n).fields[0]: col.get_note(n) for n in col.find_notes(search)}


# ---------- .apkg with options ----------
src = Collection(os.path.join(tmp, "src.anki2"))
d = src.decks.id("Shared")
for q, a in [("one", "1"), ("two", "2")]:
    n = src.new_note(src.models.by_name("Basic")); n["Front"], n["Back"] = q, a; src.add_note(n, d)
src.decks.select(d)
qc = src.sched.get_queued_cards().cards[0]
card = src.get_card(qc.card.id); card.start_timer()
src.sched.answer_card(src.sched.build_answer(card=card, states=qc.states, rating=CardAnswer.EASY))
apkg = os.path.join(tmp, "shared.apkg")
src.export_anki_package(out_path=apkg, options=ExportAnkiPackageOptions(
    with_scheduling=True, with_deck_configs=True, with_media=True, legacy=False), limit=None)

r = c.get("/import")
for s in ["Import a deck", "Import a text file", "Import any learning progress", "Merge note types", "Update notes",
          'accept=".apkg,.colpkg,.zip"', "/export"]:
    assert s in r.text, s
ok("import page offers packages (with Anki's options) and text files, and links to export")

r = c.post("/import", data={"opts": "1"}, files={"file": ("shared.apkg", open(apkg, "rb"))})
assert "Imported 2 new notes" in flash(r), flash(r)
with webapp.collection("tester") as col:
    assert all(col.get_card(cid).type == 0 for cid in col.find_cards("deck:Shared")), "progress should not be imported"
ok(".apkg without 'Import any learning progress' brings cards in as new")

with webapp.collection("tester") as col:
    col.remove_notes(col.find_notes("deck:Shared"))
r = c.post("/import", data={"opts": "1", "with_scheduling": "1"}, files={"file": ("shared.apkg", open(apkg, "rb"))})
with webapp.collection("tester") as col:
    assert sorted(col.get_card(cid).type for cid in col.find_cards("deck:Shared")) == [0, 2]
ok(".apkg with 'Import any learning progress' keeps the review")

# change a note in the source and re-import with update = never / always
time.sleep(1.1)  # note times are in seconds
nid = src.find_notes("one")[0]; n = src.get_note(nid); n["Back"] = "uno"; src.update_note(n)
src.export_anki_package(out_path=apkg, options=ExportAnkiPackageOptions(
    with_scheduling=False, with_deck_configs=False, with_media=True, legacy=False), limit=None)
never = str(Update.IMPORT_ANKI_PACKAGE_UPDATE_CONDITION_NEVER)
r = c.post("/import", data={"opts": "1", "update_notes": never}, files={"file": ("shared.apkg", open(apkg, "rb"))})
assert notes()["one"]["Back"] == "1" and "2 skipped" in flash(r), flash(r)
ok("update notes: Never leaves the existing note alone")
always = str(Update.IMPORT_ANKI_PACKAGE_UPDATE_CONDITION_ALWAYS)
r = c.post("/import", data={"opts": "1", "update_notes": always}, files={"file": ("shared.apkg", open(apkg, "rb"))})
assert notes()["one"]["Back"] == "uno" and "updated" in flash(r), flash(r)
ok("update notes: Always updates the existing note")
r = c.get("/import")
assert "Last import: shared.apkg" in r.text and "Updated" in r.text and "nid:" in r.text
ok("import log with per-note status is shown on the Import page")
src.close()

r = c.post("/import", files={"file": ("bad.apkg", b"not a zip")})
assert flash(r).startswith("!Import failed"), flash(r)
ok("a broken .apkg shows an error")

# ---------- text import ----------
csv = b"front,back,tags\nhola,hello,spanish greet\n\"uno, dos\",\"<b>one, two</b>\",spanish\n"
r = c.post("/import/csv", files={"file": ("words.csv", csv, "text/csv")})
token = re.search(r'action="/import/csv/([0-9a-f]+)"', r.text).group(1)
assert "Import words.csv" in r.text and "uno, dos" in r.text and 'name="map0"' in r.text and "Preview" in r.text
assert re.search(r'<select name="delimiter"[^>]*>.*?<option value="%d" selected>' % CsvMetadata.Delimiter.COMMA, r.text, re.S)
ok("text file: options page guesses the comma separator and previews rows")

r = c.get(f"/import/csv/{token}", params={"delimiter": str(CsvMetadata.Delimiter.TAB), "sent": "1"})
assert "Column 2" not in r.text
r = c.get(f"/import/csv/{token}", params={"delimiter": str(CsvMetadata.Delimiter.COMMA), "sent": "1"})
assert "Column 3" in r.text
ok("changing the separator re-reads the columns")

with webapp.collection("tester") as col:
    basic = col.models.by_name("Basic")["id"]; cloze = col.models.by_name("Cloze")["id"]
    vocab = col.decks.id("Vocab")
r = c.get(f"/import/csv/{token}", params={"notetype": str(cloze), "sent": "1"})
assert ">Text</label>" in r.text and ">Back Extra</label>" in r.text
ok("choosing another note type shows its fields for mapping")

form = {"sent": "1", "go": "1", "delimiter": str(CsvMetadata.Delimiter.COMMA), "notetype": str(basic), "deck": str(vocab),
        "map0": "0", "map1": "1", "tags_column": "3", "global_tags": "", "updated_tags": "",
        "dupe": str(CsvMetadata.DupeResolution.UPDATE), "scope": str(CsvMetadata.MatchScope.NOTETYPE)}
r = c.post(f"/import/csv/{token}", data=form)
assert "The first field of the note type must be mapped." in r.text and "Import words.csv" in r.text
ok("first field must be mapped; the form stays open")

form.update(map0="2", map1="1", global_tags="imported", html="1")
r = c.post(f"/import/csv/{token}", data=form)
assert "Imported words.csv: 3 new notes" in flash(r), flash(r)
ns = notes("deck:Vocab")
assert "<b>one, two</b>" in ns, list(ns)
assert ns["hello"]["Back"] == "hola" and ns["<b>one, two</b>"]["Back"] == "uno, dos"
assert sorted(ns["hello"].tags) == ["greet", "imported", "spanish"]
assert "Last import: words.csv" in r.text
ok("swapped mapping, tags column, extra tags, chosen deck and HTML all applied")
assert c.get(f"/import/csv/{token}", follow_redirects=False).status_code == 303
ok("the upload is removed after importing")

csv2 = b"hello\tHOLA\nnew\tnuevo\n"


def upload(data, name="more.txt"):
    r = c.post("/import/csv", files={"file": (name, data, "text/plain")}, follow_redirects=False)
    return r.headers["location"].rsplit("/", 1)[1]


base = {"sent": "1", "go": "1", "delimiter": str(CsvMetadata.Delimiter.TAB), "notetype": str(basic), "deck": str(vocab),
        "map0": "1", "map1": "2", "tags_column": "0", "global_tags": "", "updated_tags": "fixed",
        "scope": str(CsvMetadata.MatchScope.NOTETYPE)}
t = upload(csv2)
r = c.post(f"/import/csv/{t}", data=dict(base, dupe=str(CsvMetadata.DupeResolution.PRESERVE)))
assert "1 new note, 1 skipped" in flash(r), flash(r)
assert notes("deck:Vocab")["hello"]["Back"] == "hola"
ok("existing notes: Preserve skips the match")
t = upload(csv2)
r = c.post(f"/import/csv/{t}", data=dict(base, dupe=str(CsvMetadata.DupeResolution.UPDATE)))
assert "1 updated" in flash(r), flash(r)
n = notes("deck:Vocab")["hello"]; assert n["Back"] == "HOLA" and "fixed" in n.tags
ok("existing notes: Update changes the match and adds 'tag updated notes'")
t = upload(csv2)
r = c.post(f"/import/csv/{t}", data=dict(base, dupe=str(CsvMetadata.DupeResolution.DUPLICATE)))
with webapp.collection("tester") as col:
    assert len(col.find_notes('"deck:Vocab" front:hello')) == 2
ok("existing notes: Duplicate adds another copy")

t = upload(b"a\tb\n<b>x</b>\ty\n")
r = c.post(f"/import/csv/{t}", data=dict(base, delimiter=str(CsvMetadata.Delimiter.TAB)))  # html box unticked
assert notes("deck:Vocab")["&lt;b&gt;x&lt;/b&gt;"]["Back"] == "y"
ok("with HTML off, markup is imported as text")

t = upload(b"#separator:Semicolon\n#html:false\n#deck:From File\n#tags:hdr\nq1;a1\n")
r = c.get(f"/import/csv/{t}")
assert "(set by the file)" in r.text and "From File (new deck)" in r.text
r = c.post(f"/import/csv/{t}", data={"sent": "1", "go": "1", "notetype": str(basic), "deck": "name", "map0": "1",
                                     "map1": "2", "dupe": "0", "scope": "0"})
assert "1 new note" in flash(r), flash(r)
assert "hdr" in notes('"deck:From File"')["q1"].tags
ok("file headers (separator, html, new deck, tags) are honoured")

r = c.post("/import/csv", files={"file": ("bad.txt", b"\xff\xfe\x00bad\x00", "text/plain")})
assert flash(r).startswith("!"), r.text[:300]
ok("a file that isn't UTF-8 is refused with a message")
r = c.post("/import/csv", files={"file": ("deck.apkg", b"x")})
assert "choose a .csv" in flash(r)
r = c.post("/import", files={"file": ("x.csv", b"a,b")})
assert "choose an .apkg" in flash(r)
ok("each form only takes its own file types")

t = upload(b"a\tb\n")
other = TestClient(webapp.app); other.post("/login", data={"username": "other", "password": "secret123"})
r = other.get(f"/import/csv/{t}", follow_redirects=False)
assert r.status_code == 303
r = other.post(f"/import/csv/{t}", data=base, follow_redirects=False); assert r.status_code == 303
assert c.get(f"/import/csv/{t}").status_code == 200
c.get(f"/import/csv/{t}/cancel")
assert c.get(f"/import/csv/{t}", follow_redirects=False).status_code == 303
ok("uploads belong to their user; cancel removes them")

# ---------- export ----------
r = c.get("/export")
for s in ["Anki Collection Package", "Anki Deck Package", "Notes in Plain Text", "Cards in Plain Text",
          "Include scheduling information", "Include media", "Support older Anki versions", "Include tags"]:
    assert s in r.text, s
ok("export page lists Anki's four formats and their options")

r = c.post("/export", data={"fmt": "notes", "scope": str(vocab), "html": "1", "tags": "1", "deck": "1"})
assert r.status_code == 200 and "Vocab.txt" in r.headers["content-disposition"]
txt = r.text
assert "#separator:tab" in txt and "#deck column:1" in txt and "hello\tHOLA" in txt and "fixed" in txt
assert "one\n" not in txt and "Shared" not in txt
ok("notes as text: one deck, with deck name, tags and HTML")

r = c.post("/export", data={"fmt": "cards", "scope": "0"})
assert r.status_code == 200 and "uno" in r.text and "All%20Decks.txt" in r.headers["content-disposition"]
ok("cards as text, all decks")

r = c.get("/browse?q=tag:greet")
assert 'href="/export?q=tag%3Agreet"' in r.text
r = c.get("/export?q=tag:greet")
assert "Selected Notes (1 note from Browse)" in r.text
r = c.post("/export", data={"fmt": "notes", "scope": "search", "q": "tag:greet", "html": "1"})
assert r.text.strip().splitlines()[-1].startswith("hello") and len(r.text.strip().splitlines()) == 3
ok("Browse links to export of the current search")

r = c.post("/export", data={"fmt": "apkg", "scope": str(vocab), "scheduling": "1", "media": "1"})
assert r.status_code == 200 and r.content[:2] == b"PK" and "Vocab.apkg" in r.headers["content-disposition"]
chk = Collection(os.path.join(tmp, "chk.anki2"))
from anki.collection import ImportAnkiPackageRequest
p = os.path.join(tmp, "vocab.apkg"); open(p, "wb").write(r.content)
chk.import_anki_package(ImportAnkiPackageRequest(package_path=p)); assert chk.note_count() == count("deck:Vocab"); chk.close()
ok("deck package of one deck re-imports with the same notes")
with webapp.collection("tester") as col:
    sid = col.decks.id_for_name("Shared")
assert f'href="/export?did={sid}"' in c.get(f"/deck/{sid}").text
assert 'value="%d" selected' % sid in c.get(f"/export?did={sid}").text
ok("deck overview links to export with the deck chosen")

r = c.post("/export", data={"fmt": "colpkg", "media": "1"})
assert r.status_code == 200 and re.search(r"collection-[\d@%-]+\.colpkg", r.headers["content-disposition"])
colpkg = r.content
assert "collection.anki21b" in zipfile.ZipFile(io.BytesIO(colpkg)).namelist()
before = count()
r = c.get("/")
assert "Vocab" in r.text
with webapp.collection("tester") as col:
    n = col.new_note(col.models.by_name("Basic")); n["Front"] = "after export"; col.add_note(n, 1)
ok("collection package exported; the collection stays usable afterwards")
r = c.post("/export", data={"fmt": "bogus"}); assert flash(r).startswith("!")

# ---------- .colpkg import (replace everything) ----------
r = c.post("/import", files={"file": ("backup.colpkg", colpkg)})
assert "Replace your whole collection?" in r.text and "This will delete your existing collection" in r.text
token = re.search(r'action="/import/colpkg/([0-9a-f]+)"', r.text).group(1)
assert count() == before + 1
ok(".colpkg asks for confirmation first")
r = c.post(f"/import/colpkg/{token}", data={})
assert "Tick the box" in flash(r) and count() == before + 1
ok("nothing is replaced without ticking the confirm box")
r = other.post(f"/import/colpkg/{token}", data={"confirm": "1"}, follow_redirects=False)
assert r.status_code == 303 and count() == before + 1
r = c.post(f"/import/colpkg/{token}", data={"confirm": "1"})
assert "Collection replaced" in flash(r), flash(r)
ns = notes()
assert count() == before and "after export" not in ns and "hello" in ns
backups = os.listdir(os.path.join(os.environ["DATA_DIR"], "users", "tester", "backups"))
assert any(b.endswith(".colpkg") for b in backups), backups
ok("confirmed .colpkg replaces the collection, after saving a backup")
assert c.get("/").status_code == 200 and c.get("/browse").status_code == 200
with webapp.collection("tester") as col:
    col.decks.select(vocab); assert col.sched.counts()[0] > 0
ok("the replaced collection works (deck list, browse, scheduler)")

r = c.post("/import", files={"file": ("junk.colpkg", b"junk")})
token = re.search(r'action="/import/colpkg/([0-9a-f]+)"', r.text).group(1)
r = c.post(f"/import/colpkg/{token}", data={"confirm": "1"})
assert flash(r).startswith("!Import failed, your collection was not changed") and count() == before
ok("a broken .colpkg leaves the collection as it was")

r = c.post("/import", files={"file": ("backup.colpkg", colpkg)})
token = re.search(r'action="/import/colpkg/([0-9a-f]+)"', r.text).group(1)
c.get(f"/import/colpkg/{token}/cancel")
assert c.get(f"/import/colpkg/{token}", follow_redirects=False).status_code == 303
ok("cancel discards the uploaded .colpkg")

for url in ["/import", "/export", f"/export?did={vocab}", "/export?q=deck:Vocab"]:
    assert c.get(url).status_code == 200, url
assert TestClient(webapp.app).get("/export", follow_redirects=False).status_code == 303
ok("pages load; logged-out users are sent to login")
print("ALL IMPORT/EXPORT TESTS PASSED")
