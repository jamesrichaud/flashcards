"""Card info page: stats and review history from the engine's card_stats call."""
import os, re, sys, tempfile, html
os.environ["DATA_DIR"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fastapi.testclient import TestClient
import app as webapp

ok = lambda m: print("PASS", m)
c = TestClient(webapp.app)
c.post("/login", data={"username": "tester", "password": "secret123"})
c.post("/deck/new", data={"name": "Info"})
with webapp.collection("tester") as col:
    did = col.decks.id_for_name("Info")
    basic = col.models.by_name("Basic")["id"]
c.post("/add", data={"ntid": basic, "did": did, "f0": "maison", "f1": "house"})
c.post("/add", data={"ntid": basic, "did": did, "f0": "arbre", "f1": "tree"})
with webapp.collection("tester") as col:
    cid = col.find_cards("maison")[0]
    nid = col.get_card(cid).nid
    fresh = col.find_cards("arbre")[0]

def cells(text):
    """Label -> value from the stats table."""
    return {k: html.unescape(v) for k, v in re.findall(r"<tr><th>([^<]+)</th><td>([^<]*)</td></tr>", text)}

# --- a new card ---
r = c.get(f"/card/{fresh}/info")
assert r.status_code == 200 and "Card info" in r.text
v = cells(r.text)
for k in ("Added", "Position", "Reviews", "Lapses", "Card type", "Note type", "Deck", "Card ID", "Note ID"):
    assert k in v, k
assert v["Reviews"] == "0" and v["Deck"] == "Info" and v["Note type"] == "Basic" and v["Card type"] == "Card 1"
assert "First review" not in v and "not been reviewed yet" in r.text
ok("new card shows added, position, deck, note type, no history")

# --- review it a few times through the study screen ---
def study_once(rating):
    r = c.get(f"/study/{did}")
    shown = int(re.search(r'name="card_id" value="(\d+)"', r.text).group(1))
    c.post(f"/study/{did}/answer", data={"card_id": shown, "rating": rating, "ms": 4000})
    return shown
seen = []
for rating in (1, 3, 3, 3, 3, 3):
    seen.append(study_once(rating))
    with webapp.collection("tester") as col:
        if col.get_card(cid).queue == 2: break
assert cid in seen

with webapp.collection("tester") as col:
    s = col.card_stats_data(cid)
r = c.get(f"/card/{cid}/info")
v = cells(r.text)
for k in ("Added", "First review", "Latest review", "Interval", "Due", "Reviews", "Lapses",
          "Average time", "Total time", "Stability", "Difficulty", "Retrievability"):
    assert k in v, k
assert v["Reviews"] == str(s.reviews) and int(v["Reviews"]) >= 2
assert v["Average time"] == "4 seconds", v["Average time"]
assert v["Difficulty"].endswith("%") and v["Retrievability"].endswith("%")
assert "Ease" not in v   # FSRS on: stability/difficulty instead of ease
ok("reviewed card shows first/latest review, interval, due, times, FSRS stability/difficulty/retrievability")

hist = r.text.split("Review history", 1)[1]
assert hist.count("<tr><td") == s.reviews
assert ">Again<" in hist and ">Good<" in hist and ">Learn<" in hist
assert "<th>Stability</th>" in hist and "<th>Difficulty</th>" in hist
ok("review history table lists every review with type, rating, interval, time")

# --- SM-2: ease shown instead of FSRS values ---
c.post("/settings/fsrs")
with webapp.collection("tester") as col:
    assert not col.get_config("fsrs")
c.post(f"/card/{fresh}/action", data={"action": "due", "arg": "2"}, headers={"X-Requested-With": "fetch"})
r = c.get(f"/card/{fresh}/info"); v = cells(r.text)
assert "Ease" in v and v["Ease"].endswith("%") and "Stability" not in v, v
ok("SM-2 mode shows ease")
c.post("/settings/fsrs")

# --- suspended state in the due line; manual entries in history ---
c.post(f"/card/{fresh}/action", data={"action": "suspend"}, headers={"X-Requested-With": "fetch"})
v = cells(c.get(f"/card/{fresh}/info").text)
assert v["Due"].endswith("(suspended)"), v["Due"]
ok("suspended card's due date is marked (suspended)")

# --- links from study screen, edit page and browse ---
c.post("/add", data={"ntid": basic, "did": did, "f0": "fleur", "f1": "flower"})
r = c.get(f"/study/{did}")
assert "Card info" in r.text and "/info?back=/study/" in r.text and "e.key==='i'" in r.text
ok("study screen links to card info (menu + I key)")
r = c.get(f"/edit/{nid}?back=/browse")
assert f"/card/{cid}/info?back=" in r.text; ok("edit page Cards panel links to card info")
r = c.get("/browse", params={"q": "maison"})
m = re.search(rf'href="(/card/{cid}/info\?back=[^"]+)"', r.text); assert m, "browse info link"
r = c.get(html.unescape(m.group(1)))
assert 'href="/browse?q=maison"' in r.text; ok("browse links to card info, with a Back link to the search")

# --- safety ---
r = c.get(f"/card/{cid}/info", params={"back": "https://evil.example/"})
assert "evil.example" not in r.text; ok("off-site back URL ignored")
r = c.get("/card/123/info", follow_redirects=False)
assert r.status_code == 303; ok("unknown card redirects")
c.post("/settings/adduser", data={"username": "other", "pw": "password1"})
o = TestClient(webapp.app); o.post("/login", data={"username": "other", "password": "password1"})
r = o.get(f"/card/{cid}/info", follow_redirects=False); assert r.status_code == 303
ok("another user's card is not shown")
assert TestClient(webapp.app).get(f"/card/{cid}/info", follow_redirects=False).status_code == 303
ok("login required")

# --- span formatting ---
f = webapp.fmt_span
assert f(45) == "45 seconds" and f(60) == "1 minute" and f(86400) == "1 day" and f(86400 * 3) == "3 days"
assert f(86400 * 45) == "1.5 months" and f(86400 * 365) == "1 year"
ok("time spans formatted like Anki")
print("ALL CARD INFO TESTS PASSED")
