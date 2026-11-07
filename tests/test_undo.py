"""Undo / redo everywhere through the engine, with header buttons, keys and a toast."""
import os, re, sys, tempfile
os.environ["DATA_DIR"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fastapi.testclient import TestClient
import app as webapp

ok = lambda m: print("PASS", m)
c = TestClient(webapp.app)
AJAX = {"X-Requested-With": "fetch"}

# Logged out: no undo buttons or script, and the routes ask for a login.
r = c.get("/login")
assert 'id="fc-toast"' not in r.text and 'action="/undo"' not in r.text
r = c.post("/undo", follow_redirects=False)
assert r.status_code == 303 and r.headers["location"] == "/login"
ok("logged out: no undo UI, routes need login")

c.post("/login", data={"username": "tester", "password": "secret123"})
r = c.post("/undo", headers=AJAX).json()
assert r["ok"] is False and r["msg"] == "Nothing to undo", r
r = c.post("/redo", headers=AJAX).json()
assert r["ok"] is False and r["msg"] == "Nothing to redo", r
ok("empty undo/redo answers with the engine's 'Nothing to undo/redo'")

# Add a deck and a note, then undo the note through the global route.
c.post("/deck/new", data={"name": "Undo"})
with webapp.collection("tester") as col:
    did = col.decks.id_for_name("Undo")
    basic = col.models.by_name("Basic")["id"]
c.get("/browse")   # first visit saves the default columns (a config write clears redo, as in Anki)
c.post("/add", data={"ntid": basic, "did": did, "f0": "maison", "f1": "house"})
with webapp.collection("tester") as col:
    assert col.find_notes("maison")
    assert col.undo_status().undo == "Add Note"

r = c.get("/")
assert 'action="/undo"' in r.text and 'title="Undo Add Note (Ctrl+Z)"' in r.text
assert 'action="/redo"' not in r.text
assert 'id="fc-toast"' in r.text and "fcUndoKey" in r.text
ok("header shows Undo with the engine's label; no Redo yet; toast + key script present")

r = c.post("/undo", headers=AJAX).json()
assert r == {"ok": True, "msg": "Add Note undone", "undo": r["undo"], "redo": "Add Note"}, r
with webapp.collection("tester") as col:
    assert not col.find_notes("maison")
r = c.get("/browse")
assert 'title="Redo Add Note (Ctrl+Shift+Z)"' in r.text
ok("undo removes the note and reports 'Add Note undone'; Redo appears")

r = c.post("/redo", headers=AJAX).json()
assert r["ok"] and r["msg"] == "Add Note redone" and r["undo"] == "Add Note" and not r["redo"], r
with webapp.collection("tester") as col:
    assert col.find_notes("maison")
ok("redo brings the note back")

# Without JavaScript: the form posts, a flash says what happened, and we go back.
r = c.post("/undo", data={"back": "/browse?q=deck:Undo"}, follow_redirects=False)
assert r.status_code == 303 and r.headers["location"] == "/browse?q=deck:Undo"
r = c.get("/browse?q=deck:Undo")
assert '<div class="flash">Add Note undone</div>' in r.text
c.post("/redo")
ok("form post: flash message and redirect back")

# The back address falls back to the Referer, and never leaves the site.
r = c.post("/undo", headers={"referer": "http://testserver/stats?x=1"}, follow_redirects=False)
assert r.headers["location"] == "/stats?x=1"
c.post("/redo")
r = c.post("/undo", data={"back": "//evil.example/"}, follow_redirects=False)
assert r.headers["location"] == "/"
c.post("/redo")
ok("back from Referer; off-site back is refused")

r = c.post("/redo", follow_redirects=False)
c.get("/")   # consume flash
r = c.post("/redo", follow_redirects=False)
assert r.headers["location"] == "/"
r = c.get("/")
assert '<div class="flash err">Nothing to redo</div>' in r.text
ok("nothing to redo shows an error flash")

# Undo works for other actions too: answering a card, suspending, deleting a deck.
with webapp.collection("tester") as col:
    cid = col.find_cards("maison")[0]
c.get(f"/study/{did}")
c.post(f"/study/{did}/answer", data={"card_id": cid, "rating": 3, "ms": 1000})
with webapp.collection("tester") as col:
    assert col.get_card(cid).reps == 1
r = c.post("/undo", headers=AJAX).json()
assert r["ok"] and r["msg"] == "Answer Card undone", r
with webapp.collection("tester") as col:
    assert col.get_card(cid).reps == 0
ok("answer card undone from anywhere")

c.post(f"/card/{cid}/action", data={"action": "suspend"})
r = c.post("/undo", headers=AJAX).json()
assert r["ok"] and "undone" in r["msg"] and "Suspend" in r["msg"], r
with webapp.collection("tester") as col:
    assert col.get_card(cid).queue != -1
ok("suspend undone")

c.post(f"/deck/{did}/delete")
with webapp.collection("tester") as col:
    assert col.decks.id_for_name("Undo") is None
r = c.post("/undo", headers=AJAX).json()
assert r["ok"] and "Delete Deck" in r["msg"], r
with webapp.collection("tester") as col:
    assert col.decks.id_for_name("Undo") == did and col.find_notes("maison")
ok("deleted deck and its note come back")

# Status endpoint
r = c.get("/undo/status").json()
assert set(r) == {"undo", "redo"} and r["undo"]
ok("status endpoint")

# Pages that use Ctrl+Z themselves opt out of the global keys; the study screen reloads cleanly.
MARK = "<span data-noundokey hidden>"
assert MARK in c.get("/add").text
with webapp.collection("tester") as col:
    nid = col.find_notes("maison")[0]
assert MARK in c.get(f"/edit/{nid}").text
assert MARK in c.get("/io/new").text
assert MARK not in c.get("/browse").text
s = c.get(f"/study/{did}").text
assert f"window.FC_AFTER_UNDO='/study/{did}'" in s and "fcUndoKey(e)" in s
ok("editor/IO pages opt out of global keys; study page forwards keys from the card frame")

# Labels are escaped in the header.
with webapp.collection("tester") as col:
    col.add_custom_undo_entry('<b>"x"</b>')
r = c.get("/")
assert '<b>"x"</b>' not in r.text and "&lt;b&gt;&quot;x&quot;&lt;/b&gt;" in r.text
ok("labels escaped")
print("ALL UNDO TESTS PASSED")
