"""Step 2: end-to-end test of the web app, acting like a real user."""
import os, re, sys, tempfile, base64
os.environ["DATA_DIR"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fastapi.testclient import TestClient
from anki.collection import Collection, ExportAnkiPackageOptions
import app as webapp

ok = lambda m: print("PASS", m)
c = TestClient(webapp.app)

# --- build a realistic shared deck (.apkg) with an image, like one from AnkiWeb ---
tmp = tempfile.mkdtemp()
src = Collection(os.path.join(tmp, "src.anki2"))
png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")
src.media.write_data("flag.png", png)
d = src.decks.id("Geography::Europe")
for q, a in [("France", "Paris"), ("Spain", "Madrid"), ("Italy", "Rome")]:
    n = src.new_note(src.models.by_name("Basic")); n["Front"] = f'Capital of {q}? <img src="flag.png">'; n["Back"] = a
    src.add_note(n, d)
apkg = os.path.join(tmp, "geo.apkg")
src.export_anki_package(out_path=apkg, options=ExportAnkiPackageOptions(
    with_scheduling=False, with_deck_configs=True, with_media=True, legacy=False), limit=None)
src.close()

# 1. Not logged in -> redirected to login
r = c.get("/", follow_redirects=False); assert r.status_code == 303 and r.headers["location"] == "/login"
ok("logged-out users are sent to login")

# 2. First visit creates the account
r = c.post("/login", data={"username": "Friend", "password": "secret123"})
assert "Decks" in r.text and "Log out" in r.text; ok("first account created and logged in")

# 3. Create deck, add Basic + Cloze cards
c.post("/deck/new", data={"name": "Biology"})
with webapp.collection("friend") as col:
    bio = col.decks.id_for_name("Biology"); basic = col.models.by_name("Basic")["id"]
    cloze = col.models.by_name("Cloze")["id"]
r = c.post("/add", data={"ntid": basic, "did": bio, "f0": "Powerhouse of the cell?", "f1": "Mitochondria", "tags": "cells"})
assert "Added 1 card" in r.text
r = c.post("/add", data={"ntid": cloze, "did": bio, "f0": "{{c1::DNA}} is made of {{c2::nucleotides}}", "f1": ""})
assert "Added 2 card" in r.text; ok("added basic (1 card) and cloze (2 cards) through the Add page")
r = c.post("/add", data={"ntid": basic, "did": bio, "f0": "", "f1": "x"}); assert "Could not add" in r.text
ok("empty card is rejected with a message")

# 4. Import the shared deck
r = c.post("/import", files={"file": ("geo.apkg", open(apkg, "rb"), "application/octet-stream")})
assert "Imported 3 new notes" in r.text and "Europe" in r.text; ok("imported .apkg (3 notes, nested deck shown)")
r = c.post("/import", files={"file": ("x.txt", b"hi", "text/plain")}); assert "choose an .apkg" in r.text
ok("non-.apkg file rejected")

# 5. Image from imported deck is served
assert c.get("/media/flag.png").content == png; ok("imported image is served")
assert c.get("/media/../collection.anki2").status_code == 404; ok("media path traversal blocked")

# 6. Deck list shows counts
r = c.get("/"); assert re.search(r"Biology</a></td>\s*<td class=\"n new\">3", r.text); ok("deck list shows 3 new in Biology")

r = c.get(f"/deck/{bio}"); assert "Study now" in r.text and "Deck options" in r.text
ok("deck overview page shows Study now and options")

# 7. Study: question, then answer with each button
r = c.get(f"/study/{bio}"); assert "Show answer" in r.text and "Again" in r.text
card_id = int(re.search(r'name="card_id" value="(\d+)"', r.text).group(1))
labels = re.findall(r"<small>(.*?)</small>", r.text); assert len(labels) == 4
ok(f"study page shows card + interval labels {labels}")
r = c.post(f"/study/{bio}/answer", data={"card_id": card_id, "rating": 3, "ms": 4200})
assert "Undo" in r.text; ok("answered Good; undo is offered")
r2 = c.post(f"/study/{bio}/answer", data={"card_id": card_id, "rating": 3, "ms": 10})
with webapp.collection("friend") as col:
    assert col.db.scalar("select count() from revlog") == 1
ok("double-tap on the same card is ignored")
r = c.post(f"/study/{bio}/undo")
with webapp.collection("friend") as col:
    assert col.db.scalar("select count() from revlog") == 0
ok("undo removes the review")
for rating in (1, 2, 3, 4, 3, 3, 3, 3, 3, 3):
    r = c.get(f"/study/{bio}", follow_redirects=False)
    if r.status_code == 303: break
    cid = int(re.search(r'name="card_id" value="(\d+)"', r.text).group(1))
    c.post(f"/study/{bio}/answer", data={"card_id": cid, "rating": rating, "ms": 3000})
with webapp.collection("friend") as col:
    n = col.db.scalar("select count() from revlog"); t = col.db.scalar("select sum(time) from revlog")
    assert n >= 3 and t >= 3000 * 3
ok(f"studied through deck: {n} reviews logged with real timings")

# 8. Cloze renders with blank and answer
r = c.get(f"/study/{bio}")
with webapp.collection("friend") as col:
    cz = col.get_card(col.find_cards("DNA")[0])
    assert "[...]" in cz.question() and "DNA" in cz.answer()
ok("cloze card shows [...] then reveals answer")

# 9. Browse + search (Anki syntax) + edit
r = c.get("/browse", params={"q": "tag:cells"}); assert "1 notes" in r.text and "Powerhouse" in r.text
r = c.get("/browse", params={"q": "deck:Geography*"}); assert "3 notes" in r.text
ok("browse search with Anki syntax (tag:, deck:)")
nid = int(re.search(r'/edit/(\d+)', c.get("/browse", params={"q": "tag:cells"}).text).group(1))
r = c.post(f"/edit/{nid}?back=/browse", data={"did": bio, "f0": "Powerhouse of the cell?", "f1": "The mitochondria", "tags": "cells bio"})
assert "Saved" in r.text
with webapp.collection("friend") as col:
    assert col.get_note(nid)["Back"] == "The mitochondria" and "bio" in col.get_note(nid).tags
ok("edit note saves fields and tags")

# 10. Deck options
r = c.post(f"/deck/{bio}/options", data={"new_per_day": 7, "rev_per_day": 150, "retention": 0.85})
with webapp.collection("friend") as col:
    conf = col.decks.config_dict_for_deck_id(bio)
    assert conf["new"]["perDay"] == 7 and conf["rev"]["perDay"] == 150 and abs(conf["desiredRetention"] - 0.85) < 1e-6
ok("deck options save (new/day, reviews/day, retention)")

# 11. Export deck and re-import into a fresh collection
r = c.get(f"/deck/{bio}/export"); assert r.status_code == 200 and len(r.content) > 1000
p = os.path.join(tmp, "out.apkg"); open(p, "wb").write(r.content)
from anki.collection import ImportAnkiPackageRequest
chk = Collection(os.path.join(tmp, "chk.anki2")); chk.import_anki_package(ImportAnkiPackageRequest(package_path=p))
assert chk.card_count() == 3; chk.close(); ok("exported .apkg re-imports with 3 cards")

# 12. Stats, settings
r = c.get("/stats"); assert "Studied" in r.text and "Total cards" in r.text; ok("stats page renders")
c.post("/settings/fsrs");
with webapp.collection("friend") as col: assert col.get_config("fsrs") is False
c.post("/settings/fsrs"); ok("FSRS toggle works")

# 13. Second user is isolated
c.post("/settings/adduser", data={"username": "sam", "pw": "password1"})
c2 = TestClient(webapp.app)
assert "Wrong" in c2.post("/login", data={"username": "sam", "password": "nope"}).text; ok("wrong password rejected")
r = c2.post("/login", data={"username": "sam", "password": "password1"})
assert "Biology" not in r.text and "No decks yet" in r.text or "Default" in r.text
assert c2.get("/media/flag.png").status_code == 404; ok("second user sees none of the first user's cards or media")

# 14. Delete deck
c.post(f"/deck/{bio}/delete")
with webapp.collection("friend") as col: assert not col.decks.id_for_name("Biology")
ok("delete deck")
with webapp.collection("friend") as col: geo = col.decks.id_for_name("Geography::Europe")
assert "Study now" in c.get(f"/deck/{geo}").text
for p in ["/", "/add", "/browse", "/import", "/stats", "/settings", "/manifest.json"]:
    assert c.get(p).status_code == 200, p
ok("all pages return 200")
print("ALL APP TESTS PASSED")
