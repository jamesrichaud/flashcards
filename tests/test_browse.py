"""Browse upgrade: cards/notes mode, columns, sorting, bulk actions, find and replace."""
import html, os, re, sys, tempfile
os.environ["DATA_DIR"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fastapi.testclient import TestClient
import app as webapp

ok = lambda m: print("PASS", m)
c = TestClient(webapp.app)
c.post("/login", data={"username": "tester", "password": "secret123"})
c.post("/deck/new", data={"name": "Words"})
c.post("/deck/new", data={"name": "Other"})
with webapp.collection("tester") as col:
    did = col.decks.id_for_name("Words")
    other_did = col.decks.id_for_name("Other")
    rev = col.models.by_name("Basic (and reversed card)")["id"]
    basic = col.models.by_name("Basic")["id"]
c.post("/add", data={"ntid": rev, "did": did, "f0": "chien", "f1": "dog", "tags": "animal"})
for w, m in (("chat", "cat"), ("oiseau", "bird"), ("poisson", "fish")):
    c.post("/add", data={"ntid": basic, "did": did, "f0": w, "f1": m, "tags": "animal"})
with webapp.collection("tester") as col:
    nid = {w: col.find_notes(w)[0] for w in ("chien", "chat", "oiseau", "poisson")}
    cids = {w: list(col.card_ids_of_note(n)) for w, n in nid.items()}

def ids_on_page(text):
    return [int(x) for x in re.findall(r'name="ids" value="(\d+)"', text)]
def bulk(data, ids=(), mode="notes"):
    d = {"mode": mode, "q": "deck:Words", "back": "/browse?q=deck%3AWords", **data}
    return c.post("/browse/bulk", data={**d, "ids": [str(i) for i in ids]}, follow_redirects=False)
def flash():
    return re.search(r'<div class="flash[^"]*">(.*?)</div>', c.get("/browse").text).group(1)

# --- modes ---
r = c.get("/browse", params={"q": "deck:Words"})
assert "4 notes" in r.text and len(ids_on_page(r.text)) == 4, (re.findall(r"\d+ (?:notes|cards)", r.text), ids_on_page(r.text)); ok("notes mode is the default: one row per note")
row = re.search(rf'value="{nid["chien"]}".*?</tr>', r.text, re.S).group(0)
assert "Basic (and reversed card)" in row and "animal" in row and "chien" in row, row
ok("notes-mode rows come from the engine's note rows (note type, tags)")
r = c.get("/browse", params={"q": "deck:Words", "mode": "cards"})
assert "5 cards" in r.text
row = re.search(rf'value="{cids["chien"][1]}".*?</tr>', r.text, re.S).group(0)
assert "Card 2" in row and "chien" in row, row and len(ids_on_page(r.text)) == 5; ok("cards mode: one row per card")
r = c.get("/browse", params={"q": "deck:Words"})
assert "5 cards" in r.text; ok("chosen mode is remembered")
for col_label in ("Sort Field", "Card Type", "Deck", "Due", "Interval", "Ease", "Reviews", "Lapses"):
    assert f">{col_label}" in r.text, col_label
ok("cards mode has due, interval, ease, reviews and lapses columns")
r = c.get("/browse", params={"q": "deck:Words", "mode": "notes"})
for col_label in ("Note Type", "Avg. Interval", "Avg. Ease", "Tags"):
    assert f">{col_label}" in r.text, col_label
ok("notes mode uses the notes-mode labels (Avg. Ease etc.)")

# --- sorting ---
c.post("/card/%d/action" % cids["oiseau"][0], data={"action": "due", "arg": "5"})
c.post("/card/%d/action" % cids["chat"][0], data={"action": "due", "arg": "2"})
r = c.get("/browse", params={"q": "deck:Words is:review", "mode": "cards", "sort": "cardDue", "rev": "0"})
assert ids_on_page(r.text) == [cids["chat"][0], cids["oiseau"][0]]; ok("sort by due date")
assert "Due ▲" in r.text or "Due ▼" in r.text; ok("sorted column shows an arrow")
r = c.get("/browse", params={"q": "deck:Words is:review", "sort": "cardDue", "rev": "1"})
assert ids_on_page(r.text) == [cids["oiseau"][0], cids["chat"][0]]; ok("click again reverses the sort")
r = c.get("/browse", params={"q": "deck:Words", "sort": "noteFld", "rev": "0"})
order = ids_on_page(r.text)
with webapp.collection("tester") as col:
    words = [col.get_card(i).note().fields[0] for i in order]
assert words == sorted(words), words; ok("sort by sort field")
with webapp.collection("tester") as col:
    assert col.get_config("sortType") == "noteFld"; ok("sort stored in Anki's own config key")
r = c.get("/browse", params={"q": "deck:Words", "sort": "nonsense"}); assert r.status_code == 200
ok("unknown sort column ignored")

# --- column chooser ---
c.post("/browse/columns", data={"mode": "cards", "cols": ["noteFld", "cardLapses", "bogus"], "back": "/browse"})
with webapp.collection("tester") as col:
    assert col.get_config("activeCols") == ["noteFld", "cardLapses"]
heads = lambda t: re.findall(r'class="sort[^"]*" href="[^"]*">([^<▲▼]+)', t)
r = c.get("/browse", params={"mode": "cards"}); assert heads(r.text) == ["Sort Field ", "Lapses"] or \
    [h.strip() for h in heads(r.text)] == ["Sort Field", "Lapses"], heads(r.text)
ok("choose columns (unknown ones dropped)")
c.post("/browse/columns", data={"mode": "cards", "reset": "1", "back": "/browse"})
r = c.get("/browse"); assert "Interval" in [h.strip() for h in heads(r.text)]; ok("reset to default columns")

# --- bulk actions (notes mode) ---
c.get("/browse", params={"mode": "notes"})
r = bulk({"op": "deck", "deck": other_did}, [nid["chien"]])
assert r.status_code == 303 and r.headers["location"] == "/browse?q=deck%3AWords"
with webapp.collection("tester") as col:
    assert {col.get_card(i).did for i in cids["chien"]} == {other_did}
ok("change deck moves all cards of the selected notes")
bulk({"op": "add_tags", "tags": "fr  vocab"}, [nid["chat"], nid["oiseau"]])
with webapp.collection("tester") as col:
    assert col.find_notes("tag:vocab tag:fr") and len(col.find_notes("tag:vocab")) == 2
ok("add tags to many notes")
bulk({"op": "remove_tags", "tags": "vocab"}, [nid["chat"]])
with webapp.collection("tester") as col: assert list(col.find_notes("tag:vocab")) == [nid["oiseau"]]
ok("remove tags")
bulk({"op": "suspend"}, [nid["chat"], nid["poisson"]])
with webapp.collection("tester") as col: assert sorted(col.find_cards("is:suspended")) == sorted(cids["chat"] + cids["poisson"])
ok("suspend selected"); assert "2 cards suspended" in flash()
bulk({"op": "unsuspend"}, [nid["chat"], nid["poisson"]])
with webapp.collection("tester") as col: assert not col.find_cards("is:suspended")
ok("unsuspend selected")
bulk({"op": "flag", "flag": "4"}, [nid["poisson"]])
with webapp.collection("tester") as col: assert list(col.find_cards("flag:4")) == cids["poisson"]
ok("flag selected")
r = bulk({"op": "due", "days": "soon"}, [nid["poisson"]]); assert "Due date" in flash()
bulk({"op": "due", "days": "1"}, [nid["poisson"]])
with webapp.collection("tester") as col: assert list(col.find_cards("prop:due=1")) == cids["poisson"]
ok("set due date (and bad input rejected)")
bulk({"op": "reset"}, [nid["poisson"], nid["chat"], nid["oiseau"]])
with webapp.collection("tester") as col: assert not col.find_cards("-is:new")
ok("reset to new")

# --- reposition (cards mode) ---
bulk({"op": "reposition", "start": "100", "step": "10"}, [cids["chat"][0], cids["poisson"][0]], mode="cards")
with webapp.collection("tester") as col:
    dues = sorted(col.get_card(i).due for i in (cids["chat"][0], cids["poisson"][0]))
assert dues == [100, 110], dues; ok("reposition new cards with start and step")
assert "2 new cards repositioned" in flash()
c.post("/card/%d/action" % cids["oiseau"][0], data={"action": "due", "arg": "3"})
bulk({"op": "reposition", "start": "0", "step": "1"}, [cids["oiseau"][0]], mode="cards")
assert "Only new cards" in flash(); ok("reposition refuses review cards")

# --- find and replace ---
bulk({"do_replace": "1", "find": "cat", "replace": "kitty", "field": "Back"}, [nid["chat"]])
with webapp.collection("tester") as col: assert col.get_note(nid["chat"])["Back"] == "kitty"
assert "1 note updated" in flash(); ok("find and replace in one field")
bulk({"do_replace": "1", "find": "kitty", "replace": "X", "field": "Front"}, [nid["chat"]])
with webapp.collection("tester") as col: assert col.get_note(nid["chat"])["Back"] == "kitty"
ok("other fields untouched")
bulk({"do_replace": "1", "find": r"^(\w)\w*$", "replace": "${1}!", "regex": "1", "field": ""}, [nid["poisson"]])
with webapp.collection("tester") as col: assert col.get_note(nid["poisson"]).fields == ["p!", "f!"]
ok("regex replace across all fields")
bulk({"do_replace": "1", "find": "(", "regex": "1", "replace": ""}, [nid["poisson"]])
assert "Bad regular expression" in flash(); ok("bad regex reported")
bulk({"do_replace": "1", "find": "animal", "replace": "beast", "field": "__tags__", "all": "1"})
with webapp.collection("tester") as col:
    assert len(col.find_notes("tag:beast")) == 3 and len(col.find_notes("tag:animal")) == 1   # chien is in Other
ok("find and replace in tags, for every note in the search")
bulk({"do_replace": "1", "find": "DOG", "replace": "hound", "match_case": "1"}, [nid["chien"]])
with webapp.collection("tester") as col: assert col.get_note(nid["chien"])["Back"] == "dog"
bulk({"do_replace": "1", "find": "DOG", "replace": "hound"}, [nid["chien"]])
with webapp.collection("tester") as col: assert col.get_note(nid["chien"])["Back"] == "hound"
ok("match case on and off")

# --- select all in search, delete ---
bulk({"op": "flag", "flag": "2", "all": "1"}, mode="cards")
with webapp.collection("tester") as col: assert len(col.find_cards("flag:2")) == len(col.find_cards("deck:Words"))
ok("'All in this search' applies to every match, not just the page")
r = bulk({"op": "suspend"}); assert "Nothing selected" in flash(); ok("nothing selected is reported")
bulk({"op": "delete"}, [cids["chat"][0]], mode="cards")
with webapp.collection("tester") as col: assert not col.find_notes("chat") and col.note_count() == 3
ok("delete removes the notes of selected cards")
r = c.get("/browse", params={"q": "deck:Words", "mode": "cards"})
for s in ("Change deck", "Add tags", "Remove tags", "Reposition new cards", "Delete notes", "Find and replace",
          'id="selall"', "All 2 cards in this search"):
    assert s in r.text, s
ok("browse page shows checkboxes, action menu and find/replace")

# --- safety ---
r = bulk({"op": "suspend", "back": "https://evil.example/"}, [nid["poisson"]])
assert r.headers["location"] == "/browse"; ok("off-site back refused")
c.post("/settings/adduser", data={"username": "other", "pw": "password1"})
c2 = TestClient(webapp.app); c2.post("/login", data={"username": "other", "password": "password1"})
c2.post("/browse/bulk", data={"mode": "notes", "op": "delete", "ids": [str(nid["poisson"])], "back": "/browse"})
with webapp.collection("tester") as col: assert col.note_count() == 3
ok("another user's ids are not touched")
r = c.get("/browse", params={"q": "deck:("}); assert r.status_code == 200; ok("bad search shows an error, not a crash")
r = c.post("/browse/bulk", data={"op": "suspend"}, follow_redirects=False)
c3 = TestClient(webapp.app)
assert c3.post("/browse/bulk", data={"op": "delete", "all": "1"}, follow_redirects=False).headers["location"] == "/login"
ok("login required")
print("ALL BROWSE TESTS PASSED")
