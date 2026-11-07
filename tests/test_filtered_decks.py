"""Filtered decks: create, edit, rebuild, empty; search, limit, order, reschedule, second filter."""
import html, os, re, sys, tempfile
os.environ["DATA_DIR"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fastapi.testclient import TestClient
import app as webapp
from anki import decks_pb2

Order = decks_pb2.Deck.Filtered.SearchTerm.Order
ok = lambda m: print("PASS", m)
c = TestClient(webapp.app)
c.post("/login", data={"username": "tester", "password": "secret123"})


def flash(r):
    m = re.search(r'<div class="flash( err)?">(.*?)</div>', r.text, re.S)
    return ("!" if m and m.group(1) else "") + html.unescape(m.group(2)) if m else ""


def deck(name, n, tag=""):
    c.post("/deck/new", data={"name": name})
    with webapp.collection("tester") as col:
        did = col.decks.id_for_name(name)
        nt = col.models.by_name("Basic")
        for i in range(n):
            note = col.new_note(nt)
            note["Front"], note["Back"] = f"{name} q{i}", "a"
            note.tags = [tag] if tag else []
            col.add_note(note, did)
        return did


def cards_in(did):
    with webapp.collection("tester") as col:
        return len(col.find_cards(f"did:{did}"))


def form(**over):
    data = {"did": "0", "name": "Cram", "search1": "deck:French", "limit1": "100", "order1": str(Order.RANDOM),
            "resched": "1", "preview_again_secs": "60", "preview_hard_secs": "600", "preview_good_secs": "0"}
    data.update(over)
    return {k: v for k, v in data.items() if v is not None}


fr = deck("French", 12, "verb")
sp = deck("Spanish", 5)

# --- links ---
r = c.get("/")
assert 'href="/filtered/new"' in r.text
r = c.get("/browse?q=deck:French")
assert 'href="/filtered/new?search=deck%3AFrench"' in r.text
ok("deck list and Browse link to Create filtered deck")

# --- new form: engine defaults, current deck, search from Browse ---
r = c.get(f"/filtered/new?did={fr}")
assert r.status_code == 200
for text in ("Build Deck", "Search", "Limit to", "cards selected by", "Enable second filter", "Filter 2",
             "Reschedule cards based on my answers in this deck", "Create/update this deck even if empty",
             "Oldest seen first", "Relative overdueness", "Random", "Delay for"):
    assert text in html.unescape(r.text), text
assert 'value="deck:French is:due"' in r.text  # engine default for the current deck
assert 'name="resched" value="1" style="width:22px;height:22px" checked' in r.text
r = c.get("/filtered/new?search=tag:verb")
assert 'value="tag:verb"' in r.text
ok("new filtered deck form shows Anki's options and engine defaults")

# --- create ---
r = c.post("/filtered/save", data=form(limit1="5", order1=str(Order.ADDED)), follow_redirects=True)
assert flash(r) == "Deck built: 5 cards.", flash(r)
with webapp.collection("tester") as col:
    cram = col.decks.id_for_name("Cram")
    d = col.decks.get(cram)
    assert d["dyn"] and d["resched"]
    fd = col.sched.get_or_create_filtered_deck(cram)
    assert len(fd.config.search_terms) == 1
    t = fd.config.search_terms[0]
    assert (t.search, t.limit, t.order) == ("deck:French", 5, Order.ADDED)
    # order added: first five notes
    fronts = sorted(col.get_card(cid).note()["Front"] for cid in col.find_cards(f"did:{cram}"))
    assert fronts == [f"French q{i}" for i in range(5)], fronts
assert "Cram" in r.text and f'href="/deck/{cram}/filtered"' in r.text
assert f'action="/deck/{cram}/rebuild"' in r.text and f'action="/deck/{cram}/empty"' in r.text
ok("create filtered deck with search, limit and order")

# --- edit page shows saved values ---
r = c.get(f"/deck/{cram}/filtered")
assert r.status_code == 200
assert 'value="deck:French"' in r.text and 'name="limit1" value="5"' in r.text
assert f'<option value="{Order.ADDED}" selected>' in r.text
assert "Rebuild" in r.text and f'name="did" value="{cram}"' in r.text
# a normal deck's "filtered" page goes to its options
r = c.get(f"/deck/{fr}/filtered", follow_redirects=False)
assert r.status_code == 303 and r.headers["location"] == f"/deck/{fr}/options"
ok("edit page shows the saved settings")

# --- edit: second filter, no reschedule, preview delays, rename ---
r = c.post("/filtered/save", data=form(did=str(cram), name="Cram 2", search1="deck:French", limit1="3",
                                         second="1", search2="deck:Spanish", limit2="2", order2=str(Order.DUE),
                                         resched=None, preview_again_secs="30", preview_hard_secs="300",
                                         preview_good_secs="900"), follow_redirects=True)
assert flash(r) == "Deck built: 5 cards.", flash(r)
with webapp.collection("tester") as col:
    assert col.decks.id_for_name("Cram 2") == cram
    fd = col.sched.get_or_create_filtered_deck(cram)
    assert [(t.search, t.limit, t.order) for t in fd.config.search_terms] == \
        [("deck:French", 3, Order.RANDOM), ("deck:Spanish", 2, Order.DUE)]
    assert not fd.config.reschedule
    assert (fd.config.preview_again_secs, fd.config.preview_hard_secs, fd.config.preview_good_secs) == (30, 300, 900)
    homes = [col.decks.name(col.get_card(cid).odid) for cid in col.find_cards(f"did:{cram}")]
    assert homes.count("French") == 3 and homes.count("Spanish") == 2, homes
r = c.get(f"/deck/{cram}/filtered")
assert 'name="second" value="1" style="width:22px;height:22px" checked' in r.text
assert 'name="resched" value="1" style="width:22px;height:22px">' in r.text
assert 'name="preview_hard_secs" value="300"' in r.text
ok("edit: second filter, no rescheduling with preview delays, rename")

# turning the second filter off again
c.post("/filtered/save", data=form(did=str(cram), name="Cram 2", limit1="4"))
with webapp.collection("tester") as col:
    fd = col.sched.get_or_create_filtered_deck(cram)
    assert len(fd.config.search_terms) == 1 and fd.config.reschedule
assert cards_in(cram) == 4
ok("second filter can be switched off")

# --- empty / rebuild ---
r = c.post(f"/deck/{cram}/empty", follow_redirects=True)
assert cards_in(cram) == 0 and "emptied" in flash(r)
r = c.post(f"/deck/{cram}/rebuild", follow_redirects=True)
assert cards_in(cram) == 4 and flash(r) == "Deck rebuilt: 4 cards."
ok("rebuild and empty")

# --- errors keep the form ---
# nothing matches (cards already in another filtered deck are excluded): message + "show excluded"
r = c.post("/filtered/save", data=form(name="Nothing", search1="deck:French tag:none"))
assert r.status_code == 200
assert flash(r).startswith("!No cards matched the provided search"), flash(r)
assert "Show any excluded cards" in r.text and 'value="Nothing"' in r.text
with webapp.collection("tester") as col:
    assert not col.decks.id_for_name("Nothing")
# ...unless "even if empty" is ticked
r = c.post("/filtered/save", data=form(name="Nothing", search1="deck:French tag:none", allow_empty="1"),
           follow_redirects=True)
assert flash(r) == "Deck built: 0 cards."
with webapp.collection("tester") as col:
    assert col.decks.is_filtered(col.decks.id_for_name("Nothing"))
# bad search
r = c.post("/filtered/save", data=form(name="Bad", search1="deck:French ("))
assert flash(r).startswith("!Invalid search"), flash(r)
assert 'value="deck:French ("' in r.text
# empty search
r = c.post("/filtered/save", data=form(name="Blank", search1=" "))
assert flash(r) == "!Please select something."
# bad number
r = c.post("/filtered/save", data=form(name="Num", limit1="lots"))
assert flash(r) == "!Please enter a whole number."
# bad order
r = c.post("/filtered/save", data=form(name="Ord", order1="99"))
assert flash(r) == "!Unknown order."
# limits clamp
c.post("/filtered/save", data=form(name="Big", search1="deck:Spanish", limit1="0"))
with webapp.collection("tester") as col:
    big = col.decks.id_for_name("Big")
    assert col.sched.get_or_create_filtered_deck(big).config.search_terms[0].limit == 1
# editing a normal deck through the form is refused
r = c.post("/filtered/save", data=form(did=str(fr), name="Hijack"), follow_redirects=False)
assert r.status_code == 303
with webapp.collection("tester") as col:
    assert col.decks.name(fr) == "French" and not col.decks.is_filtered(fr)
ok("errors keep the form open with Anki's messages; limits clamp; normal decks untouched")

# --- studying a filtered deck with rescheduling off doesn't change the real schedule ---
with webapp.collection("tester") as col:
    for name in ("Cram 2", "Big", "Nothing"):
        col.sched.empty_filtered_deck(col.decks.id_for_name(name))
c.post("/filtered/save", data=form(name="Preview", search1="deck:Spanish", resched=None))
with webapp.collection("tester") as col:
    pv = col.decks.id_for_name("Preview")
    col.decks.select(pv)
    assert col.sched.counts()[0] + col.sched.counts()[1] + col.sched.counts()[2] > 0
r = c.get(f"/study/{pv}")
assert r.status_code == 200
ok("filtered deck can be studied")

# --- logged out ---
c2 = TestClient(webapp.app)
assert c2.get("/filtered/new", follow_redirects=False).status_code in (302, 303, 307)
assert c2.post("/filtered/save", data=form(), follow_redirects=False).status_code in (302, 303, 307)
ok("login required")
print("ALL FILTERED DECK TESTS PASSED")
