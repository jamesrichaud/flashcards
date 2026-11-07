"""Card actions: suspend/unsuspend, bury card/note, flags, mark, reset, set due date."""
import os, re, sys, tempfile
os.environ["DATA_DIR"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fastapi.testclient import TestClient
import app as webapp

ok = lambda m: print("PASS", m)
c = TestClient(webapp.app)
c.post("/login", data={"username": "tester", "password": "secret123"})
c.post("/deck/new", data={"name": "Actions"})
with webapp.collection("tester") as col:
    did = col.decks.id_for_name("Actions")
    rev = col.models.by_name("Basic (and reversed card)")["id"]
    basic = col.models.by_name("Basic")["id"]
c.post("/add", data={"ntid": rev, "did": did, "f0": "chien", "f1": "dog"})
for w in ("chat", "oiseau", "poisson"):
    c.post("/add", data={"ntid": basic, "did": did, "f0": w, "f1": "x"})
with webapp.collection("tester") as col:
    nid = col.find_notes("chien")[0]
    c1, c2 = sorted(col.card_ids_of_note(nid))
    other = col.find_cards("chat")[0]

AJAX = {"X-Requested-With": "fetch"}
def act(cid, action, arg="", ajax=True, back="/browse"):
    r = c.post(f"/card/{cid}/action", data={"action": action, "arg": arg, "back": back},
               headers=AJAX if ajax else {}, follow_redirects=ajax)
    return r.json() if ajax else r
def card(cid):
    with webapp.collection("tester") as col:
        return col.get_card(cid)

# --- study screen has the menu and keyboard shortcuts ---
r = c.get(f"/study/{did}")
for s in ("More", "Mark note", "Bury card", "Bury note", "Suspend card", "Suspend note", "Set due date",
          "Reset to new", "Turquoise", "Purple", "'-':'bury'", "'@':'suspend'", "'*':'mark'", "Digit[1-7]"):
    assert s in r.text, s
ok("study screen shows actions menu, 7 flags and - @ * Ctrl+1..7 shortcuts")

# --- suspend / unsuspend ---
j = act(c1, "suspend"); assert j["ok"] and j["state"] == "Suspended" and card(c1).queue == -1
ok("suspend card")
r = c.get(f"/study/{did}"); assert f'name="card_id" value="{c1}"' not in r.text
ok("suspended card leaves the study queue")
r = c.get("/browse", params={"q": "chien"}); assert "1 suspended" in r.text
j = act(c2, "suspend"); r = c.get("/browse", params={"q": "chien"})
assert 'class="susp"' in r.text and "Suspended" in r.text; ok("browse shows suspended state (partial and full)")
act(c1, "unsuspend"); act(c2, "unsuspend"); assert card(c1).queue == 0 and card(c2).queue == 0
ok("unsuspend cards")
j = act(c1, "suspend_note"); assert card(c1).queue == -1 and card(c2).queue == -1
act(c1, "unsuspend"); act(c2, "unsuspend"); ok("suspend note suspends all its cards")

# --- bury ---
act(c1, "bury"); assert card(c1).queue in (-2, -3) and card(c2).queue == 0; ok("bury card")
act(c1, "unbury"); assert card(c1).queue == 0; ok("unbury card")
act(c1, "bury_note"); assert card(c1).queue in (-2, -3) and card(c2).queue in (-2, -3); ok("bury note buries all its cards")
r = c.get("/browse", params={"q": "chien"}); assert "Buried" in r.text
act(c1, "unbury"); act(c2, "unbury")

# --- flags ---
for n in range(1, 8):
    j = act(c1, "flag", str(n)); assert j["flag"] == n and card(c1).user_flag() == n
ok("all 7 flags set (red..purple)")
j = act(c1, "flag", "7"); assert j["flag"] == 0 and card(c1).user_flag() == 0; ok("same flag again clears it, as in Anki")
act(c1, "flag", "1")
r = c.get("/browse", params={"q": "chien"}); assert 'class="flag1"' in r.text and "Red flag" in r.text
ok("browse shows flag colour")
with webapp.collection("tester") as col: assert col.find_cards("flag:1") == [c1]
ok("flag is searchable with Anki syntax flag:1")
assert not act(c1, "flag", "9")["ok"]; ok("bad flag rejected")

# --- mark ---
j = act(c1, "mark"); assert j["marked"]
with webapp.collection("tester") as col: assert col.get_note(nid).has_tag("marked")
r = c.get(f"/study/{did}")
ok("mark note adds the 'marked' tag")
j = act(c1, "mark"); assert not j["marked"]
with webapp.collection("tester") as col: assert not col.get_note(nid).has_tag("marked")
ok("mark again unmarks")

# --- set due date / reset ---
act(other, "due", "3"); cd = card(other)
with webapp.collection("tester") as col: today = col.sched.today
assert cd.queue == 2 and cd.due == today + 3; ok("set due date to 3 days makes it a review card due in 3 days")
assert not act(other, "due", "soon")["ok"] and card(other).due == today + 3; ok("invalid due date rejected")
j = act(other, "reset"); assert j["ok"] and card(other).queue == 0 and card(other).type == 0
ok("reset to new")

# --- undo of a card action from the study screen ---
r = c.get(f"/study/{did}"); cid = int(re.search(r'name="card_id" value="(\d+)"', r.text).group(1))
act(cid, "suspend"); r = c.get(f"/study/{did}"); assert "Undo Suspend" in r.text
c.post(f"/study/{did}/undo"); assert card(cid).queue == 0; ok("suspend can be undone from the study screen")

# --- non-ajax form posts (edit page) redirect back with a flash ---
back = f"/edit/{nid}?back=/browse"
r = act(c2, "suspend", ajax=False, back=back); assert r.status_code == 303 and r.headers["location"] == back
r = c.get(back); assert "Card suspended" in r.text and "Unsuspend" in r.text and "Card 2" in r.text
ok("edit page lists cards with actions; form post redirects back with message")
r = act(c2, "unsuspend", ajax=False, back="https://evil.example/"); assert r.headers["location"] == "/browse"
ok("off-site back URL refused")
r = c.get(back); assert "Mark note" in r.text and "Set due" in r.text and "Reset to new" in r.text
ok("edit page offers mark, set due and reset")

# --- other users cannot touch your cards ---
c.post("/settings/adduser", data={"username": "other", "pw": "password1"})
c2c = TestClient(webapp.app); c2c.post("/login", data={"username": "other", "password": "password1"})
j = c2c.post(f"/card/{c1}/action", data={"action": "suspend"}, headers=AJAX).json()
assert not j["ok"] and card(c1).queue == 0; ok("another user's card id is not found")
print("ALL CARD ACTION TESTS PASSED")
