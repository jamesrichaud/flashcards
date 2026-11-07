"""The add-ons shipped with the app (bundled_addons/): listed, off by default, and each one works."""
import base64, os, re, sys, tempfile
os.environ["DATA_DIR"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fastapi.testclient import TestClient
import app as webapp

ok = lambda m: print("PASS", m)
c = TestClient(webapp.app)
c.post("/login", data={"username": "friend", "password": "secret123"})
BUNDLED = ["ankiconnect", "edit_field_review", "popup_dictionary", "read_aloud", "speed_focus", "study_streaks"]

# 1. Listed and off by default
r = c.get("/addons")
for name in ["AnkiConnect", "Edit Field During Review", "Pop-up Dictionary", "Read Aloud", "Speed Focus Mode",
             "Study Streaks"]:
    assert name in r.text, name
assert "· <b class=\"rev\">On</b>" not in r.text
ok("all 6 bundled add-ons are listed and start switched off")

# Some cards to study
with webapp.collection("friend") as col:
    did = col.decks.id("Spanish")
    for q, a in [("el perro", "the dog"), ("la casa", "the house"), ("comer", "to eat")]:
        n = col.new_note(col.models.by_name("Basic")); n["Front"] = q; n["Back"] = a; col.add_note(n, did)
    nid = col.find_notes("perro")[0]

def study_html():
    return c.get(f"/study/{did}").text

base = study_html()
for marker in ["efr-btn", "sfm-bar", "ra-btn", "pd-pop", "day streak"]:
    assert marker not in base, marker
ok("study screen has none of the add-ons while they are off")

# 2. Turn them all on
for a in BUNDLED:
    r = c.post(f"/addons/{a}/enable")
    assert "failed" not in r.text, (a, re.findall(r'class="flash[^"]*">([^<]*)', r.text))
r = c.get("/addons"); assert r.text.count('<b class="rev">On</b>') >= 6
ok("all 6 add-ons turn on and load without errors")

# 3. Edit Field During Review
s = study_html(); assert "efr-btn" in s and "/addon/edit_field_review/note/" in s
cid = int(re.search(r'name="card_id" value="(\d+)"', s).group(1))
with webapp.collection("friend") as col: cur_nid = col.get_card(cid).nid
j = c.get(f"/addon/edit_field_review/note/{cur_nid}").json()
assert [f["name"] for f in j["fields"]] == ["Front", "Back"]
r = c.post(f"/addon/edit_field_review/note/{cur_nid}", json={"fields": {"Back": "FIXED", "Nope": "x"}})
assert r.json()["ok"]
with webapp.collection("friend") as col: assert col.get_note(cur_nid)["Back"] == "FIXED"
r = c.post(f"/addon/edit_field_review/note/{cur_nid}", data={"fields": "x"}); assert r.status_code == 400
ok("Edit Field During Review: loads fields, saves an edit, rejects non-JSON posts")

# 4. Speed Focus: off until times are set, then injects the timer
assert "secs=" not in study_html()
c.post("/addon/speed_focus/", data={"show_after": 5, "answer_after": 8, "action": 3})
s = study_html(); assert "secs=5.0" in s and "act=3" in s
ok("Speed Focus: timer appears only once times are set")

# 5. Read Aloud
s = study_html(); assert "ra-btn" in s and '"lang": "en-GB"' in s.replace('\\"', '"')
c.post("/addon/read_aloud/", data={"lang": "es-ES", "rate": 0.9, "auto_q": "1"})
s = study_html().replace('\\"', '"'); assert '"lang": "es-ES"' in s and '"auto_q": true' in s
c.post("/addon/read_aloud/", data={"lang": "xx-EVIL", "rate": 9})
with webapp.collection("friend") as col:
    cfg = col.get_config("addon_read_aloud"); assert cfg["lang"] == "en-GB" and cfg["rate"] == 2.0
ok("Read Aloud: button on cards, settings saved, bad values cleaned")

# 6. Pop-up Dictionary
assert "dictionaryapi.dev" in study_html(); ok("Pop-up Dictionary: script on cards")

# 7. Study Streaks
r = c.get("/addon/study_streaks/"); assert "day streak" in r.text and "Level 1" in r.text
for _ in range(3):
    s = study_html(); m = re.search(r'name="card_id" value="(\d+)"', s)
    if not m: break
    c.post(f"/study/{did}/answer", data={"card_id": m.group(1), "rating": 3, "ms": 2000})
r = c.get("/addon/study_streaks/"); assert "🔥 1" in r.text and "🏅 First steps" in r.text
ok("Study Streaks: streak, level and badges from real reviews")

# 8. AnkiConnect
API = "/addon/ankiconnect/"
anon = TestClient(webapp.app)            # another app: no login cookie
assert anon.post(API, json={"action": "version", "version": 6}).json() == {"result": 6, "error": None}
r = anon.post(API, json={"action": "deckNames", "version": 6}).json(); assert r["error"] == "valid api key must be provided"
r = anon.post(API, json={"action": "deckNames", "version": 6, "key": "wrong"}).json(); assert r["result"] is None
assert anon.get("/addon/ankiconnect/settings", follow_redirects=False).status_code == 303
ok("AnkiConnect: no key or a wrong key gets nothing; settings page needs a login")
KEY_RE = r'won\'t be shown again\):</p><input readonly value="([^"]+)"'
r = c.post("/addon/ankiconnect/settings/new"); key = re.search(KEY_RE, r.text).group(1)
assert not re.search(KEY_RE, c.get("/addon/ankiconnect/settings").text)   # shown once only
assert key not in open(os.path.join(webapp.DATA, "ankiconnect_keys.json")).read()
call = lambda action, **p: anon.post(API, json={"action": action, "version": 6, "key": key, "params": p}).json()
assert "Spanish" in call("deckNames")["result"]
assert call("modelFieldNames", modelName="Basic")["result"] == ["Front", "Back"]
pic = base64.b64encode(b"\x89PNG\r\n\x1a\nfake").decode()
r = call("addNote", note={"deckName": "Spanish::Yomitan", "modelName": "Basic", "fields": {"Front": "el gato", "Back": "the cat"},
                          "tags": ["yomitan"], "picture": [{"filename": "cat.png", "data": pic, "fields": ["Back"]}]})
assert isinstance(r["result"], int), r
dup = call("addNote", note={"deckName": "Spanish", "modelName": "Basic", "fields": {"Front": "el gato", "Back": "x"}})
assert "duplicate" in dup["error"]
assert call("canAddNotes", notes=[{"deckName": "Spanish", "modelName": "Basic", "fields": {"Front": "el gato", "Back": ""}},
                                  {"deckName": "Spanish", "modelName": "Basic", "fields": {"Front": "nuevo", "Back": ""}}])["result"] == [False, True]
info = call("notesInfo", notes=[r["result"]])["result"][0]
assert info["tags"] == ["yomitan"] and 'src="cat.png"' in info["fields"]["Back"]["value"]
assert len(call("findNotes", query="tag:yomitan")["result"]) == 1
assert call("storeMediaFile", filename="../../evil.txt", data=base64.b64encode(b"x").decode())["result"] == "evil.txt"
assert "not supported" in call("storeMediaFile", filename="a.txt", url="http://example.com/x")["error"]
assert "unsupported action" in call("guiBrowse", query="x")["error"]
m = anon.post(API, json={"action": "multi", "version": 6, "key": key,
                         "params": {"actions": [{"action": "version", "version": 6}, {"action": "deckNames", "version": 6}]}}).json()
assert m["result"][0]["result"] == 6 and "Spanish" in m["result"][1]["result"]
assert anon.options(API).headers["access-control-allow-origin"] == "*"
ok("AnkiConnect: decks, fields, addNote with picture, duplicates, canAddNotes, notesInfo, findNotes, multi, CORS")

# second person: their key only sees their own cards
c.post("/settings/adduser", data={"username": "sam", "pw": "password1"})
c2 = TestClient(webapp.app); c2.post("/login", data={"username": "sam", "password": "password1"})
key2 = re.search(KEY_RE, c2.post("/addon/ankiconnect/settings/new").text).group(1)
r = anon.post(API, json={"action": "findNotes", "version": 6, "key": key2, "params": {"query": ""}}).json()
assert r["result"] == []
assert call("findNotes", query="tag:yomitan")["result"]          # first key still works
c.post("/addon/ankiconnect/settings/revoke")
assert call("deckNames")["error"] == "valid api key must be provided"
ok("AnkiConnect: each key is tied to one person; removing a key stops it working")

# 9. Turning off removes them again
for a in BUNDLED: c.post(f"/addons/{a}/disable")
s = study_html()
for marker in ["efr-btn", "sfm-bar", "ra-btn", "dictionaryapi.dev"]:
    assert marker not in s, marker
assert c.get("/addon/speed_focus/").status_code == 404
ok("turning add-ons off removes them from cards and their pages")
print("ALL BUNDLED ADD-ON TESTS PASSED")
