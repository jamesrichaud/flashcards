"""Custom study: raise today's new/review limits, forgotten cards, review ahead, preview new
cards, study by card state or tag, plus Rebuild/Empty on the filtered deck it makes."""
import html, os, re, sys, tempfile, time
os.environ["DATA_DIR"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fastapi.testclient import TestClient
import app as webapp
from flashcards import custom_study as cs
from anki.scheduler.v3 import CardAnswer

ok = lambda m: print("PASS", m)
c = TestClient(webapp.app)
c.post("/login", data={"username": "tester", "password": "secret123"})
SESSION = "Custom Study Session"

def flash(r):
    m = re.search(r'<div class="flash( err)?">(.*?)</div>', r.text)
    return ("!" if m and m.group(1) else "") + html.unescape(m.group(2)) if m else ""

def deck(name, n=0, tags=None):
    c.post("/deck/new", data={"name": name})
    with webapp.collection("tester") as col:
        did = col.decks.id_for_name(name)
        nt = col.models.by_name("Basic")
        for i in range(n):
            note = col.new_note(nt)
            note["Front"], note["Back"] = f"{name} q{i}", "a"
            note.tags = [tags[i % len(tags)]] if tags else []
            col.add_note(note, did)
        return did

def post(did, **data):
    return c.post(f"/deck/{did}/custom", data=data, follow_redirects=True)

def counts(did):
    with webapp.collection("tester") as col:
        col.decks.select(did)
        return col.sched.counts()

def session_id():
    with webapp.collection("tester") as col:
        return col.decks.id_for_name(SESSION)

def cards_in(did):
    with webapp.collection("tester") as col:
        return len(col.find_cards(f"did:{did}"))

# --- page and link from the overview ---
d = deck("Words", 30, ["animals", "food"])
with webapp.collection("tester") as col:
    conf = col.decks.config_dict_for_deck_id(d); conf["new"]["perDay"] = 5; col.decks.update_config(conf)
r = c.get(f"/deck/{d}")
assert f'href="/deck/{d}/custom"' in r.text
r = c.get(f"/deck/{d}/custom")
assert r.status_code == 200
for text in ("Increase today's new card limit", "Increase today's review card limit",
             "Review forgotten cards", "Review ahead", "Preview new cards", "Study by card state or tag",
             "Available new cards:", "New cards only", "Due cards only",
             "All review cards in random order", "All cards in random order (don't reschedule)",
             "Require one or more of these tags:", "Select tags to exclude:"):
    assert html.escape(text) in r.text or text in r.text, text
assert 'value="animals"' in r.text and 'value="food"' in r.text
assert re.search(r'value="new" checked', r.text)
ok("custom study page shows Anki's options, available counts and the deck's tags")

# --- increase today's new card limit ---
assert counts(d)[0] == 5
r = post(d, mode="new", new_delta="7")
assert r.url.path == f"/deck/{d}" and "limit was changed" in flash(r), flash(r)
assert counts(d)[0] == 12, counts(d)
r = post(d, mode="new", new_delta="-3")
assert counts(d)[0] == 9
assert session_id() is None
ok("new card limit can be raised (and lowered) for today without making a filtered deck")

# --- increase today's review limit ---
rv = deck("Reviews", 6)
with webapp.collection("tester") as col:
    cids = col.find_cards(f"did:{rv}")
    col.sched.set_due_date(cids, "0")
    conf = col.decks.add_config("Tight")
    conf["rev"]["perDay"] = 2; conf["new"]["perDay"] = 0; col.decks.update_config(conf)
    deck_obj = col.decks.get(rv); deck_obj["conf"] = conf["id"]; col.decks.save(deck_obj)
assert counts(rv)[2] == 2
post(rv, mode="rev", rev_delta="3")
assert counts(rv)[2] == 5, counts(rv)
ok("review limit can be raised for today")

# --- study by card state or tag (cram) with tag include ---
r = post(d, mode="cram", cram_kind=str(cs.Cram.CRAM_KIND_NEW), cram_limit="100", include="animals")
sid = session_id()
assert sid and r.url.path == f"/deck/{sid}", r.url
assert "session created" in flash(r)
assert cards_in(sid) == 15, cards_in(sid)
with webapp.collection("tester") as col:
    assert all("animals" in col.get_card(cid).note().tags for cid in col.find_cards(f"did:{sid}"))
    assert col.decks.get(sid)["dyn"]
assert "Filtered deck" in r.text and f'action="/deck/{sid}/rebuild"' in r.text and f'action="/deck/{sid}/empty"' in r.text
assert f'href="/deck/{sid}/custom"' not in r.text
ok("study by tag builds the Custom Study Session filtered deck and opens its overview")

# exclude + limit; reusing the session deck rebuilds it
post(d, mode="cram", cram_kind=str(cs.Cram.CRAM_KIND_ALL), cram_limit="4", exclude="animals")
assert session_id() == sid and cards_in(sid) == 4
with webapp.collection("tester") as col:
    assert all("animals" not in col.get_card(cid).note().tags for cid in col.find_cards(f"did:{sid}"))
ok("excluded tags and the card limit are honoured; the session deck is reused")

# the study screen works on the session deck
r = c.get(f"/study/{sid}")
assert r.status_code == 200 and "Words q" in r.text
ok("the custom study session can be studied")

# --- rebuild / empty ---
r = c.post(f"/deck/{sid}/empty", follow_redirects=True)
assert cards_in(sid) == 0 and "emptied" in flash(r)
r = c.post(f"/deck/{sid}/rebuild", follow_redirects=True)
assert cards_in(sid) == 4 and "rebuilt" in flash(r)
c.post(f"/deck/{sid}/empty")
ok("filtered deck overview can rebuild and empty")

# custom study is refused on a filtered deck
r = c.get(f"/deck/{sid}/custom", follow_redirects=True)
assert flash(r).startswith("!") and r.url.path == f"/deck/{sid}"
r = post(sid, mode="new", new_delta="5")
assert flash(r).startswith("!")
ok("custom study is only offered for normal decks")

# --- preview new cards ---
post(d, mode="preview", preview_days="1")
assert cards_in(sid) == 30, cards_in(sid)
c.post(f"/deck/{sid}/empty")
ok("preview new cards added in the last N days")

# --- forgotten cards / review ahead: nothing matches -> error, stay on the page ---
r = post(d, mode="forgot", forgot_days="1")
assert r.url.path == f"/deck/{d}/custom" and "mode=forgot" in str(r.url)
assert flash(r) == "!No cards matched the criteria you provided.", flash(r)
assert re.search(r'value="forgot" checked', r.text)
with webapp.collection("tester") as col:
    cid = col.find_cards(f"did:{d}")[0]
    col.sched.set_due_date([cid], "0")
    card = col.get_card(cid); card.start_timer()
    col.sched.answer_card(col.sched.build_answer(card=card, states=col._backend.get_scheduling_states(cid),
                                                 rating=CardAnswer.AGAIN))
r = post(d, mode="forgot", forgot_days="1")
assert r.url.path == f"/deck/{sid}" and cards_in(sid) == 1, (r.url, flash(r))
c.post(f"/deck/{sid}/empty")
ok("review forgotten cards uses cards failed in the last N days; empty result keeps the form")

with webapp.collection("tester") as col:
    cids = col.find_cards(f"did:{rv}")
    col.sched.set_due_date(cids[:2], "3")
r = post(rv, mode="ahead", ahead_days="1")
assert r.url.path == f"/deck/{sid}" and cards_in(sid) == 4, (flash(r), cards_in(sid))
c.post(f"/deck/{sid}/empty")
r = post(rv, mode="ahead", ahead_days="5")
assert r.url.path == f"/deck/{sid}" and cards_in(sid) == 6, (flash(r), cards_in(sid))
c.post(f"/deck/{sid}/empty")
ok("review ahead by N days")

# --- bad input ---
r = post(d, mode="new", new_delta="abc")
assert flash(r).startswith("!") and r.url.path == f"/deck/{d}/custom"
r = post(d, mode="forgot", forgot_days="999")  # clamped to Anki's 30
assert r.url.path == f"/deck/{sid}" or flash(r).startswith("!")
r = post(d, mode="cram", cram_kind="42")
assert flash(r).startswith("!")
r = post(d, mode="bogus")
assert flash(r).startswith("!") and "mode=new" in str(r.url)
ok("bad input is rejected with a message")

# --- empty deck: only limit options ---
e = deck("Empty")
r = c.get(f"/deck/{e}/custom")
assert re.search(r'value="cram" disabled', r.text) and "no cards" in r.text
ok("a deck without cards only offers the limit options")

# --- congratulations screen points to custom study ---
with webapp.collection("tester") as col:
    col.sched.suspend_cards(col.find_cards(f"did:{d}"))
r = c.get(f"/deck/{d}")
assert "Congratulations" in r.text and f'<a href="/deck/{d}/custom">Custom study</a>' in r.text
ok("finished deck suggests custom study, as Anki does")

# needs login
c2 = TestClient(webapp.app)
assert c2.get(f"/deck/{d}/custom", follow_redirects=False).status_code == 303
assert c2.post(f"/deck/{d}/custom", data={"mode": "new"}, follow_redirects=False).status_code == 303
ok("custom study needs login")
print("ALL CUSTOM STUDY TESTS PASSED")
