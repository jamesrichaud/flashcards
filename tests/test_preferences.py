"""Preferences: engine prefs (next day, learn ahead, timebox, counts, next times), theme."""
import os, re, sys, tempfile, time
os.environ["DATA_DIR"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fastapi.testclient import TestClient
import app as webapp

ok = lambda m: print("PASS", m)
c = TestClient(webapp.app)

r = c.get("/preferences", follow_redirects=False)
assert r.status_code == 303 and r.headers["location"] == "/login"
r = c.post("/preferences", data={}, follow_redirects=False)
assert r.status_code == 303 and r.headers["location"] == "/login"
ok("preferences need a login")

c.post("/login", data={"username": "prefs", "password": "secret123"})
r = c.get("/settings"); assert 'href="/preferences"' in r.text; ok("settings links to preferences")

# Defaults come from the engine.
r = c.get("/preferences"); assert r.status_code == 200
for label in ("Next day starts at", "Learn ahead limit", "Timebox time limit", "Show remaining card count",
              "Show next review time above answer buttons", "Theme", "Follow System", "Light", "Dark", "English"):
    assert label in r.text, label
assert re.search(r'name="rollover"[^>]*value="4"', r.text)
assert re.search(r'name="learn_ahead"[^>]*value="20"', r.text)
assert re.search(r'name="timebox"[^>]*value="0"', r.text)
assert re.search(r'name="show_counts" value="1" checked', r.text)
assert re.search(r'name="show_times" value="1" checked', r.text)
assert re.search(r'name="theme" value="system" checked', r.text)
assert "<html>" in r.text  # system theme: no forced class
ok("page shows the engine's defaults (4h, 20 min, timebox off, counts and times on, system theme)")

# Save: written to the engine's preferences.
r = c.post("/preferences", data={"rollover": "2", "learn_ahead": "5", "timebox": "1", "theme": "dark"})
assert "Preferences saved." in r.text
with webapp.collection("prefs") as col:
    p = col.get_preferences()
    assert p.scheduling.rollover == 2 and p.scheduling.learn_ahead_secs == 300
    assert p.reviewing.time_limit_secs == 60
    assert not p.reviewing.show_remaining_due_counts and not p.reviewing.show_intervals_on_buttons
    assert col.get_config("collapseTime") == 300 and col.get_config("timeLim") == 60
    assert col.get_config("rollover") == 2
    assert webapp.theme_of(col) == "dark"
assert re.search(r'name="rollover"[^>]*value="2"', r.text)
assert not re.search(r'name="show_counts" value="1" checked', r.text)
ok("saved to the engine: rollover 2, learn ahead 5 min, timebox 1 min, counts/times off, dark theme")

# Theme is applied to every page.
r = c.get("/"); assert '<html class="dark">' in r.text and ":root.dark{" in r.text
ok("dark theme forces dark colours on pages")

# Bad values are refused with a message and nothing changes.
for bad in ({"rollover": "24", "learn_ahead": "5", "timebox": "1"},
            {"rollover": "x", "learn_ahead": "5", "timebox": "1"},
            {"rollover": "3", "learn_ahead": "-1", "timebox": "1"}):
    r = c.post("/preferences", data=dict(bad, theme="light"))
    assert 'class="flash err"' in r.text, bad
with webapp.collection("prefs") as col:
    assert col.get_preferences().scheduling.rollover == 2 and webapp.theme_of(col) == "dark"
r = c.post("/preferences", data={"rollover": "2", "learn_ahead": "5", "timebox": "1", "theme": "purple"})
with webapp.collection("prefs") as col:
    assert webapp.theme_of(col) == "dark"
ok("out-of-range, non-numeric values and unknown themes are refused")

# Study screen follows the review settings.
c.post("/deck/new", data={"name": "P"})
with webapp.collection("prefs") as col:
    did = col.decks.id_for_name("P"); basic = col.models.by_name("Basic")
    for i in range(4):
        n = col.new_note(basic); n["Front"] = f"q{i}"; n["Back"] = f"a{i}"; col.add_note(n, did)
r = c.get(f"/study/{did}"); assert r.status_code == 200
assert '<span class="new' not in r.text and "<small>" not in r.text
assert 'html class=\\"night_mode nightMode\\"' in r.text, "dark card frame"
ok("study: counts and next times hidden, card frame dark with night_mode class")

c.post("/preferences", data={"rollover": "2", "learn_ahead": "5", "timebox": "1", "theme": "light",
                             "show_counts": "1", "show_times": "1"})
r = c.get(f"/study/{did}")
assert '<span class="new cur">4</span>' in r.text and "<small>" in r.text
assert "night_mode" not in r.text and "prefers-color-scheme:dark){body" not in r.text
assert '<html class="light">' in r.text
ok("study: counts and next times shown again; light theme keeps cards light")

c.post("/preferences", data={"rollover": "2", "learn_ahead": "5", "timebox": "1", "theme": "system",
                             "show_counts": "1", "show_times": "1"})
r = c.get(f"/study/{did}")
assert "prefers-color-scheme:dark){body" in r.text and "classList.add('night_mode')" in r.text
ok("system theme: card dark mode follows the device, with night_mode added by script")

# Editor and template previews use the theme too.
c.post("/preferences", data={"rollover": "2", "learn_ahead": "5", "timebox": "1", "theme": "dark"})
with webapp.collection("prefs") as col:
    from flashcards import notetypes
    nt = col.models.by_name("Basic"); t = nt["tmpls"][0]
    q, a = notetypes.preview_docs(col, nt, 0, t["qfmt"], t["afmt"], nt["css"])
    assert "night_mode" in q and "night_mode" in a
ok("template preview uses the theme")

# Timebox: after the limit, answering shows Anki's "N cards studied in M minutes."
def answer_top():
    with webapp.collection("prefs") as col:
        webapp.select_deck(col, did)
        cid = col.sched.get_queued_cards().cards[0].card.id
    return c.post(f"/study/{did}/answer", data={"card_id": cid, "rating": 3, "ms": 1000})
c.get(f"/deck/{did}")
r = c.get(f"/study/{did}")
r = answer_top(); assert "studied in" not in r.text
# Pretend the session started over a minute ago.
real = webapp.timebox_clock
try:
    webapp.timebox_clock = lambda: real() + 61
    r = answer_top()
finally:
    webapp.timebox_clock = real
assert "2 cards studied in 1 minute." in r.text, r.text[-2000:]
r = answer_top(); assert "studied in" not in r.text
ok("timebox: message after the limit with the engine's wording, then starts again")

c.post("/preferences", data={"rollover": "2", "learn_ahead": "5", "timebox": "0", "theme": "dark"})
try:
    webapp.timebox_clock = lambda: real() + 3600
    r = answer_top()
finally:
    webapp.timebox_clock = real
assert "studied in" not in r.text
ok("timebox 0 never interrupts")

r = c.get("/")
assert r.status_code == 200
ok("pages still render after the changes")
print("ALL PREFERENCES TESTS PASSED")
