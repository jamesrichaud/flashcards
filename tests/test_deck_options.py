"""Deck options parity: every main field, presets (add/clone/rename/delete/choose), apply to
subdecks, per-deck limits, FSRS toggle/parameters and the Optimize button."""
import html, os, random, re, sys, tempfile, time
os.environ["DATA_DIR"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fastapi.testclient import TestClient
import app as webapp
from flashcards import deck_options as dopt

ok = lambda m: print("PASS", m)
c = TestClient(webapp.app)
c.post("/login", data={"username": "tester", "password": "secret123"})

def deck(name):
    c.post("/deck/new", data={"name": name})
    with webapp.collection("tester") as col:
        return col.decks.id_for_name(name)

def conf_for(did):
    with webapp.collection("tester") as col:
        return col.decks.config_dict_for_deck_id(did)

def flash(r):
    m = re.search(r'<div class="flash( err)?">(.*?)</div>', r.text)
    return html.unescape(m.group(2)) if m else ""

def page_form(did, preset=0):
    """Collect the options form as the browser would submit it (unchanged)."""
    r = c.get(f"/deck/{did}/options" + (f"?preset={preset}" if preset else ""))
    assert r.status_code == 200, r.status_code
    form = re.search(r'<form method="post" action="/deck/\d+/options" id="optform">(.*?)</form>', r.text, re.S).group(1)
    data = {}
    for m in re.finditer(r'<input ([^>]*)>', form):
        a = m.group(1)
        name = re.search(r'name="([^"]+)"', a)
        if not name: continue
        val = re.search(r'value="([^"]*)"', a)
        if 'type="checkbox"' in a and "checked" not in a: continue
        data[name.group(1)] = val.group(1) if val else ""
    for m in re.finditer(r'<select name="([^"]+)"[^>]*>(.*?)</select>', form, re.S):
        s = re.search(r'<option value="([^"]*)" selected', m.group(2))
        data[m.group(1)] = s.group(1)
    for m in re.finditer(r'<textarea[^>]*name="([^"]+)"[^>]*>(.*?)</textarea>', form, re.S):
        data[m.group(1)] = m.group(2)
    return r, data

# --- step parsing helpers ---
assert dopt.parse_steps("1m 10m 1h 1d") == [1, 10, 60, 1440]
assert dopt.parse_steps("30s, 5") == [0.5, 5]
assert dopt.fmt_steps([1, 10, 60, 1440, 0.5, 90]) == "1m 10m 1h 1d 30s 90m"
for bad in ("abc", "0m", "-1m", "5x"):
    try: dopt.parse_steps(bad); raise AssertionError(bad)
    except ValueError: pass
ok("learning steps parse and format like Anki (1m 10m 1h 1d)")

# --- the page shows every section and the overview links to it ---
d = deck("Lang")
r = c.get(f"/deck/{d}")
assert f'href="/deck/{d}/options"' in r.text and "Deck options" in r.text and "Preset “Default”" in r.text
r, form = page_form(d)
for text in ["Daily limits", "New cards", "Learning steps", "Graduating interval", "Easy interval",
             "Insertion order", "Lapses", "Relearning steps", "Leech threshold", "Leech action",
             "Display order", "New card gather order", "New/review order", "Review sort order",
             "Burying", "Bury new siblings", "Bury review siblings", "Bury interday learning siblings",
             "Maximum interval", "Starting ease", "Easy bonus", "Hard interval", "Desired retention",
             "FSRS parameters", "Optimize", "Add", "Clone", "Rename", "This deck only"]:
    assert text in r.text, text
assert form["learn_steps"] == "1m 10m" and form["relearn_steps"] == "10m"
assert "Delete" not in re.search(r"<h3>Preset</h3>.*?</div></div>", r.text, re.S).group(0), \
    "Default preset has no delete button"
assert "Save to all subdecks" not in r.text, "no subdecks, so no apply-to-subdecks box"
ok("options page shows all of Anki's main fields and is linked from the overview")

# --- unchanged form round-trips without changing anything ---
before = conf_for(d)
r = c.post(f"/deck/{d}/options", data=form)
after = conf_for(d)
for k in ("new", "rev", "lapse"):
    for kk, v in before[k].items():
        if isinstance(v, float):
            assert abs(after[k][kk] - v) < 1e-4, (k, kk, v, after[k][kk])
        else:
            assert after[k][kk] == v, (k, kk, v, after[k][kk])
assert after["maxTaken"] == before["maxTaken"] and abs(after["desiredRetention"] - before["desiredRetention"]) < 1e-6
ok("saving the untouched form leaves every option as it was")

# --- change every field ---
form.update({
    "new_per_day": "33", "reviews_per_day": "444", "learn_steps": "2m 15m 1h", "relearn_steps": "5m 30m",
    "graduating_interval_good": "2", "graduating_interval_easy": "6", "minimum_lapse_interval": "3",
    "leech_threshold": "5", "leech_action": "0", "maximum_review_interval": "3650",
    "initial_ease": "2.3", "easy_multiplier": "1.5", "hard_multiplier": "1.1", "lapse_multiplier": "0.2",
    "interval_multiplier": "0.9", "desired_retention": "0.87", "cap_answer_time_to_secs": "90",
    "new_card_insert_order": "1", "new_card_gather_priority": "3", "new_card_sort_order": "4",
    "new_mix": "1", "interday_learning_mix": "2", "review_order": "8",
    "bury_new": "1", "bury_reviews": "1", "bury_interday_learning": "1", "disable_autoplay": "1",
    "ignore_revlogs_before_date": "2024-01-01", "param_search": "deck:Lang",
})
form.pop("skip_question_when_replaying_answer", None)
r = c.post(f"/deck/{d}/options", data=form)
assert "Options saved" in flash(r), flash(r)
with webapp.collection("tester") as col:
    p = dopt.find_conf(dopt.load(col, d), col.decks.get(d)["conf"]).config
C = dopt.Cfg
assert (p.new_per_day, p.reviews_per_day) == (33, 444)
assert list(p.learn_steps) == [2, 15, 60] and list(p.relearn_steps) == [5, 30]
assert (p.graduating_interval_good, p.graduating_interval_easy, p.minimum_lapse_interval) == (2, 6, 3)
assert p.leech_threshold == 5 and p.leech_action == C.LEECH_ACTION_SUSPEND
assert p.maximum_review_interval == 3650 and abs(p.initial_ease - 2.3) < 1e-6
assert abs(p.easy_multiplier - 1.5) < 1e-6 and abs(p.hard_multiplier - 1.1) < 1e-6
assert abs(p.lapse_multiplier - 0.2) < 1e-6 and abs(p.interval_multiplier - 0.9) < 1e-6
assert abs(p.desired_retention - 0.87) < 1e-6 and p.cap_answer_time_to_secs == 90
assert p.new_card_insert_order == C.NEW_CARD_INSERT_ORDER_RANDOM
assert p.new_card_gather_priority == C.NEW_CARD_GATHER_PRIORITY_RANDOM_NOTES
assert p.new_card_sort_order == C.NEW_CARD_SORT_ORDER_RANDOM_CARD
assert p.new_mix == C.REVIEW_MIX_AFTER_REVIEWS and p.interday_learning_mix == C.REVIEW_MIX_BEFORE_REVIEWS
assert p.review_order == C.REVIEW_CARD_ORDER_RANDOM
assert p.bury_new and p.bury_reviews and p.bury_interday_learning and p.disable_autoplay
assert not p.skip_question_when_replaying_answer
assert p.ignore_revlogs_before_date == "2024-01-01" and p.param_search == "deck:Lang"
# The legacy dict (what the scheduler and study screen read) agrees.
legacy = conf_for(d)
assert legacy["new"]["perDay"] == 33 and legacy["new"]["delays"] == [2, 15, 60]
assert legacy["new"]["bury"] and legacy["autoplay"] is False and legacy["lapse"]["leechAction"] == 0
r, form2 = page_form(d)
assert form2["learn_steps"] == "2m 15m 1h" and form2["review_order"] == "8" and "bury_new" in form2
assert form2["new_per_day"] == "33" and form2["desired_retention"] == "0.87"
ok("every field saves through the engine and shows again on the page")

# Unticking a checkbox turns it off.
form2.pop("bury_new"); c.post(f"/deck/{d}/options", data=form2)
assert conf_for(d)["new"]["bury"] is False
ok("unticked boxes are saved as off")

# --- validation: nothing is saved on bad input ---
for bad, msg in [({"learn_steps": "1m banana"}, "not a valid step"),
                 ({"learn_steps": ""}, "can't be empty"),
                 ({"new_per_day": "lots"}, "not a number"),
                 ({"ignore_revlogs_before_date": "yesterday"}, "date"),
                 ({"fsrs_params": "1, 2, 3"}, "17, 19 or 21")]:
    r = c.post(f"/deck/{d}/options", data={**form2, "new_per_day": "1", **bad})
    assert "Not saved" in flash(r) and msg in flash(r), (bad, flash(r))
    assert conf_for(d)["new"]["perDay"] == 33
# Out-of-range numbers are clamped to Anki's limits.
c.post(f"/deck/{d}/options", data={**form2, "desired_retention": "1.5", "initial_ease": "0.5"})
assert abs(conf_for(d)["desiredRetention"] - 0.99) < 1e-6 and abs(conf_for(d)["new"]["initialFactor"] - 1310) < 1
ok("bad input is rejected with a message and nothing is saved; numbers are clamped")

# --- old quick form (new/day, reviews/day, retention only) still works ---
c.post(f"/deck/{d}/options", data={"new_per_day": 8, "rev_per_day": 80, "retention": 0.88})
legacy = conf_for(d)
assert legacy["new"]["perDay"] == 8 and legacy["rev"]["perDay"] == 80 and legacy["new"]["delays"] == [2, 15, 60]
ok("partial form changes only the fields it sends")

# --- this deck's own limits ---
r, form = page_form(d)
c.post(f"/deck/{d}/options", data={**form, "limit_new": "3", "limit_review": "40"})
with webapp.collection("tester") as col:
    lim = dopt.load(col, d).current_deck.limits
    assert lim.new == 3 and lim.review == 40
r, form = page_form(d)
assert form["limit_new"] == "3"
c.post(f"/deck/{d}/options", data={**form, "limit_new": "", "limit_review": ""})
with webapp.collection("tester") as col:
    lim = dopt.load(col, d).current_deck.limits
    assert not lim.HasField("new") and not lim.HasField("review")
ok("'this deck only' limits set and clear")

# --- presets: add, clone, rename, choose, delete ---
d2 = deck("Maths")
with webapp.collection("tester") as col:
    default_id = col.decks.get(d2)["conf"]
assert default_id == 1
r = c.post(f"/deck/{d2}/options/preset", data={"op": "add", "name": "Hard stuff", "preset": 1})
assert "created" in flash(r)
with webapp.collection("tester") as col:
    hard = col.decks.get(d2)["conf"]
    assert hard != 1 and col.decks.get_config(hard)["name"] == "Hard stuff"
    assert col.decks.get_config(hard)["new"]["perDay"] == 20, "new preset starts from Anki's defaults"
r, form = page_form(d2)
c.post(f"/deck/{d2}/options", data={**form, "new_per_day": "5"})
assert conf_for(d2)["new"]["perDay"] == 5 and conf_for(d)["new"]["perDay"] == 8, "presets are separate"

r = c.post(f"/deck/{d2}/options/preset", data={"op": "clone", "name": "", "preset": hard})
with webapp.collection("tester") as col:
    clone = col.decks.get(d2)["conf"]
    cc = col.decks.get_config(clone)
    assert clone not in (1, hard) and cc["name"] == "Hard stuff copy" and cc["new"]["perDay"] == 5
ok("add preset (Anki defaults) and clone preset (copied values), each chosen for the deck")

# Duplicate names get a number.
c.post(f"/deck/{d2}/options/preset", data={"op": "add", "name": "Hard stuff", "preset": clone})
with webapp.collection("tester") as col:
    assert col.decks.get_config(col.decks.get(d2)["conf"])["name"] == "Hard stuff 2"
    dup = col.decks.get(d2)["conf"]

r = c.post(f"/deck/{d2}/options/preset", data={"op": "rename", "name": "Easy stuff", "preset": hard})
assert "Renamed" in flash(r)
with webapp.collection("tester") as col:
    assert col.decks.get_config(hard)["name"] == "Easy stuff"
    assert col.decks.get(d2)["conf"] == dup, "renaming another preset doesn't move the deck"
r = c.post(f"/deck/{d2}/options/preset", data={"op": "rename", "name": "", "preset": hard})
assert "Type the new name" in flash(r)

# Viewing another preset doesn't change the deck; saving does.
r, form = page_form(d2, preset=hard)
assert "Saving will switch this deck" in r.text and form["new_per_day"] == "5"
with webapp.collection("tester") as col:
    assert col.decks.get(d2)["conf"] == dup
c.post(f"/deck/{d2}/options", data=form)
with webapp.collection("tester") as col:
    assert col.decks.get(d2)["conf"] == hard
ok("rename preset; choosing a preset in the list and saving assigns it to the deck")

# Delete: decks using it go back to Default. Default can't be deleted.
r = c.post(f"/deck/{d2}/options/preset", data={"op": "delete", "preset": hard})
assert "deleted" in flash(r)
with webapp.collection("tester") as col:
    assert col.decks.get(d2)["conf"] == 1
    assert col.decks.get_config(hard) is None or col.decks.get_config(hard)["id"] != hard
r = c.post(f"/deck/{d2}/options/preset", data={"op": "delete", "preset": 1})
assert "can't be deleted" in flash(r)
with webapp.collection("tester") as col:
    assert col.decks.get_config(1)["name"] == "Default"
ok("delete preset (deck falls back to Default); Default can't be deleted")

# --- apply to subdecks ---
parent = deck("Sci")
kid, grandkid = deck("Sci::Bio"), deck("Sci::Bio::Cells")
c.post(f"/deck/{parent}/options/preset", data={"op": "add", "name": "Science", "preset": 1})
r, form = page_form(parent)
assert "Save to all subdecks" in r.text
c.post(f"/deck/{parent}/options", data={**form, "new_per_day": "11"})
with webapp.collection("tester") as col:
    sci = col.decks.get(parent)["conf"]
    assert col.decks.get(kid)["conf"] == 1, "without the box, subdecks keep their preset"
r = c.post(f"/deck/{parent}/options", data={**form, "new_per_day": "11", "apply_children": "1"})
assert "Subdecks" in flash(r)
with webapp.collection("tester") as col:
    assert col.decks.get(kid)["conf"] == sci and col.decks.get(grandkid)["conf"] == sci
ok("'Save to all subdecks' gives the preset to every subdeck")

# --- FSRS toggle, parameters and Optimize ---
r, form = page_form(d)
assert "fsrs" in form, "app turns FSRS on for new collections"
form.pop("fsrs")
c.post(f"/deck/{d}/options", data=form)
with webapp.collection("tester") as col:
    assert col.get_config("fsrs") is False
r, form = page_form(d)
c.post(f"/deck/{d}/options", data={**form, "fsrs": "1"})
with webapp.collection("tester") as col:
    assert col.get_config("fsrs") is True
ok("FSRS can be switched on and off from the options page")

params = [0.212, 1.2931, 2.3065, 8.2956, 6.4133, 0.8334, 3.0194, 0.001, 1.8722, 0.1666, 0.796,
          1.4835, 0.0614, 0.2629, 1.6483, 0.6014, 1.8729, 0.5425, 0.0912, 0.0658, 0.25]
r, form = page_form(d)
c.post(f"/deck/{d}/options", data={**form, "fsrs_params": ", ".join(map(str, params))})
with webapp.collection("tester") as col:
    p = dopt.find_conf(dopt.load(col, d), col.decks.get(d)["conf"]).config
    assert [round(x, 4) for x in p.fsrs_params_6] == params
r, form = page_form(d)
assert form["fsrs_params"].endswith("0.0658, 0.25")
c.post(f"/deck/{d}/options", data=form)   # unchanged text keeps them
with webapp.collection("tester") as col:
    p = dopt.find_conf(dopt.load(col, d), col.decks.get(d)["conf"]).config
    assert len(p.fsrs_params_6) == 21
c.post(f"/deck/{d}/options", data={**form, "fsrs_params": ""})
with webapp.collection("tester") as col:
    p = dopt.find_conf(dopt.load(col, d), col.decks.get(d)["conf"]).config
    assert len(p.fsrs_params_6) == 0
ok("FSRS parameters can be typed in, kept, and cleared back to defaults")

# Optimize with no history: says so, changes nothing.
d3 = deck("Hist")
c.post(f"/deck/{d3}/options/preset", data={"op": "add", "name": "Hist", "preset": 1})
r, form = page_form(d3)
r = c.post(f"/deck/{d3}/options/optimize", data=form)
assert "No reviews" in flash(r), flash(r)

# Optimize with a year of review history: the engine fits new parameters and they are saved.
with webapp.collection("tester") as col:
    m = col.models.by_name("Basic")
    random.seed(1)
    day = 86400 * 1000
    start = int(time.time() * 1000) - 400 * day
    seq = 0
    for i in range(300):
        n = col.new_note(m); n["Front"] = f"q{i}"; n["Back"] = "a"; col.add_note(n, d3)
        cid = n.cards()[0].id
        t, last = start + random.randint(0, 30) * day, 0
        for k in range(8):
            ease = 1 if random.random() < 0.15 else 3
            typ = 0 if k == 0 else (2 if ease == 1 else 1)
            ivl = 1 if ease == 1 or k == 0 else max(1, int(max(last, 1) * 2.5))
            seq += 1
            col.db.execute("insert into revlog values (?,?,?,?,?,?,?,?,?)",
                           t + seq, cid, -1, ease, ivl, last, 2500, 5000, typ)
            last, t = ivl, t + ivl * day
r, form = page_form(d3)
r = c.post(f"/deck/{d3}/options/optimize", data={**form, "new_per_day": "9"})
assert "Optimized using 2100 reviews" in flash(r), flash(r)
with webapp.collection("tester") as col:
    p = dopt.find_conf(dopt.load(col, d3), col.decks.get(d3)["conf"]).config
    assert len(p.fsrs_params_6) == 21 and p.new_per_day == 9, "form saved first, then params fitted"
r, form = page_form(d3)
assert form["fsrs_params"]
r = c.post(f"/deck/{d3}/options/optimize", data=form)
assert "Optimized using" in flash(r) and "!" not in flash(r)
ok("Optimize runs the engine's FSRS optimizer and saves the fitted parameters")

# --- filtered decks and missing decks ---
with webapp.collection("tester") as col:
    fid = col.decks.new_filtered("Filt")
r = c.get(f"/deck/{fid}/options")
assert "Filtered decks have no options preset" in r.text
assert c.get("/deck/999999/options").url.path == "/"
ok("filtered and missing decks are handled")

print("ALL DECK OPTIONS TESTS PASSED")
