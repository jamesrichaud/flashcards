"""Bringing notes in from Google Docs on an iPad: no-file message, Paste your notes, Word (.docx) files."""
import io, os, re, sys, tempfile, zipfile
os.environ["DATA_DIR"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fastapi.testclient import TestClient
from anki.collection import CsvMetadata
import app as webapp
from flashcards.import_export import lines_to_tsv, docx_text

ok = lambda m: print("PASS", m)
c = TestClient(webapp.app)
c.post("/login", data={"username": "friend", "password": "secret123"})
flash = lambda r: " ".join(re.findall(r'class="flash[^"]*">([^<]*)', r.text))

# 1. Nothing arrives (what an iPad sends when a Google Doc is picked): a clear message, never "Error 422"
for path in ("/import", "/import/csv"):
    r = c.post(path, data={})
    assert r.status_code == 200 and "Google Doc isn&#x27;t a real file" in r.text or "Google Doc isn't a real file" in r.text, path
    r = c.post(path, files={"file": ("", b"", "application/octet-stream")})
    assert "real file" in r.text, path
ok("no file / empty pick: friendly message about Google Docs on iPad, no error page")

# 2. Splitting pasted lines
tsv = lines_to_tsv("el perro - the dog\r\n• la casa – the house\n\nMitochondria: powerhouse of the cell\n"
                   "2 + 2 = 4\nA\tB\tC\njust a line\n")
assert tsv.splitlines() == ["el perro\tthe dog", "la casa\tthe house", "Mitochondria\tpowerhouse of the cell",
                            "2 + 2\t4", "A\tB\tC", "just a line"], tsv
ok("pasted lines split on tab, ' - ', en dash, ': ', ' = '; bullets and blank lines dropped")

def finish(token, n_cols=2):
    with webapp.collection("friend") as col:
        basic = col.models.by_name("Basic")["id"]; deck = col.decks.id("From Docs")
    form = {"sent": "1", "go": "1", "delimiter": str(CsvMetadata.Delimiter.TAB), "notetype": str(basic),
            "deck": str(deck), "map0": "1", "map1": "2", "tags_column": "0", "global_tags": "", "updated_tags": "",
            "dupe": str(CsvMetadata.DupeResolution.DUPLICATE), "scope": str(CsvMetadata.MatchScope.NOTETYPE)}
    return c.post(f"/import/csv/{token}", data=form)

def notes():
    with webapp.collection("friend") as col:
        return {col.get_note(n)["Front"]: col.get_note(n)["Back"] for n in col.find_notes('"deck:From Docs"')}

# 3. Paste box -> usual options page -> cards
r = c.get("/import"); assert "Paste your notes" in r.text and 'action="/import/paste"' in r.text
r = c.post("/import/paste", data={"text": "el perro - the dog\nla casa - the house\nMitochondria: powerhouse of the cell"})
token = re.search(r'action="/import/csv/([0-9a-f]+)"', r.text).group(1)
assert "el perro" in r.text and "Pasted notes" in r.text
r = finish(token)
assert "3 new notes" in flash(r), flash(r)
assert notes() == {"el perro": "the dog", "la casa": "the house", "Mitochondria": "powerhouse of the cell"}
r = c.post("/import/paste", data={"text": "  \n \n"}); assert "nothing to import" in r.text
ok("Paste your notes: 3 cards made through the normal options page; empty paste explained")

# 4. Word file with a table (what Google Docs makes with 'Save as Word')
def docx(body):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                   f"<w:body>{body}</w:body></w:document>")
    return buf.getvalue()
P = lambda t: f"<w:p><w:r><w:t>{t}</w:t></w:r></w:p>"
row = lambda a, b: f"<w:tr><w:tc>{P(a)}</w:tc><w:tc>{P(b)}</w:tc></w:tr>"
data = docx(P("Biology week 3") + f"<w:tbl>{row('Nucleus', 'holds the DNA')}{row('Ribosome', 'makes proteins')}</w:tbl>"
            + P("Osmosis - water moving across a membrane"))
assert docx_text(data).splitlines() == ["Biology week 3", "Nucleus\tholds the DNA", "Ribosome\tmakes proteins",
                                        "Osmosis - water moving across a membrane"]
r = c.post("/import/csv", files={"file": ("Bio notes.docx", data, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")})
token = re.search(r'action="/import/csv/([0-9a-f]+)"', r.text).group(1)
assert "Bio notes.txt" in r.text
r = finish(token)
n = notes()
assert n["Nucleus"] == "holds the DNA" and n["Ribosome"] == "makes proteins" and n["Osmosis"] == "water moving across a membrane"
ok("Word file: table rows and 'a - b' lines become cards")
r = c.post("/import/csv", files={"file": ("broken.docx", b"not a zip", "application/octet-stream")})
assert "couldn" in r.text and "Paste your notes" in r.text
ok("a broken Word file gets a helpful message")
print("ALL PASTE IMPORT TESTS PASSED")
