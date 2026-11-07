"""Add-ons: discovery without running code, enable/disable, card_render, after_answer, extra pages,
errors that never stop studying, desktop Anki add-ons refused."""
import os, re, shutil, sys, tempfile, time
os.environ["DATA_DIR"] = tempfile.mkdtemp()
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from fastapi.testclient import TestClient
import app as webapp
from flashcards import addons as _addons_mod
_addons_mod.BUNDLED_DIR = tempfile.mkdtemp()   # this file tests the server's own add-on folder; bundled ones: test_bundled_addons.py
from flashcards import addons

ok = lambda m: print("PASS", m)
c = TestClient(webapp.app)
ADIR = os.path.join(os.environ["DATA_DIR"], "addons")
HTML = {"Accept": "text/html"}


def write(name, src):
    os.makedirs(ADIR, exist_ok=True)
    p = os.path.join(ADIR, name)
    with open(p, "w") as f:
        f.write(src)
    t = time.time() + write.bump   # make each rewrite's mtime differ even within one second
    write.bump += 1
    os.utime(p, (t, t))
write.bump = 1

# Login needed.
for path in ("/addons", "/addon/x/"):
    r = c.get(path, follow_redirects=False)
    assert r.status_code in (303, 404), (path, r.status_code)
r = c.get("/addons", follow_redirects=False); assert r.headers["location"] == "/login"
r = c.post("/addons/x/enable", follow_redirects=False); assert r.headers["location"] == "/login"
ok("add-ons page needs a login")

c.post("/login", data={"username": "addy", "password": "secret123"})
r = c.get("/settings"); assert 'href="/addons"' in r.text
r = c.get("/addons"); assert r.status_code == 200
assert "No add-ons installed." in r.text and "Desktop Anki add-ons (from ankiweb.net) don't run here" in r.text
ok("settings links to add-ons; empty page explains desktop add-ons don't run here")

# Discovery never runs the code; new add-ons start off.
marker = os.path.join(os.environ["DATA_DIR"], "ran.txt")
os.makedirs(ADIR, exist_ok=True)
shutil.copy(os.path.join(ROOT, "addons_examples", "answer_counter.py"), os.path.join(ADIR, "answer_counter.py"))
write("shout.py", f'''"""Makes the question shout."""
open({marker!r}, "w").write("ran")
NAME = "Shouter"
def card_render(text, card, kind, col):
    return text.upper() + "<i>shout-" + kind + "</i>" if kind == "question" else text + "<i>shout-answer</i>"
''')
r = c.get("/addons")
assert "Shouter" in r.text and "Makes the question shout." in r.text and "Answer counter" in r.text
assert r.text.count("Turn on") == 2 and "Turn off" not in r.text
assert not os.path.exists(marker)
assert addons.enabled_ids() == []
ok("add-ons listed with name and description, all off, and not run until turned on")

# A note to study.
with webapp.collection("addy") as col:
    n = col.new_note(col.models.by_name("Basic")); n["Front"] = "maison"; n["Back"] = "house"
    col.add_note(n, 1)
r = c.get("/study/1"); assert "maison" in r.text and "shout-" not in r.text

# Turn on: card_render applies to both sides of the study screen and to the editor preview.
r = c.post("/addons/shout/enable"); assert "“Shouter” is on." in r.text and "Turn off" in r.text
assert os.path.exists(marker) and addons.enabled_ids() == ["shout"]
assert "Uses: card render" in r.text
r = c.get("/study/1"); assert "MAISON" in r.text and "shout-question" in r.text and "shout-answer" in r.text
with webapp.collection("addy") as col:
    nid = col.find_notes("")[0]
r = c.post("/note/preview", data={"nid": str(nid)}); assert r.status_code == 200
pv = r.json()["cards"][0]
assert "MAISON" in pv["q"] and "shout-question" in pv["q"] and "shout-answer" in pv["a"]
ok("turned on: card_render changes the question and answer on the study screen and in the editor preview")

# Turn off: back to normal at once.
r = c.post("/addons/shout/disable"); assert "“Shouter” is off." in r.text
r = c.get("/study/1"); assert "maison" in r.text and "shout-" not in r.text
ok("turned off: cards render normally again")

# Example add-on: after_answer, card_render on the answer, an extra page, MENU links.
r = c.get("/addon/answer_counter/", headers=HTML); assert r.status_code == 404
c.post("/addons/answer_counter/enable")
r = c.get("/addons"); assert 'href="/addon/answer_counter/"' in r.text and "Today&#x27;s answers" in r.text
assert "Uses: card render, after answer, pages" in r.text
r = c.get("/study/1"); assert "Answered today: 0" in r.text
with webapp.collection("addy") as col:
    cid = col.sched.get_queued_cards().cards[0].card.id
c.post("/study/1/answer", data={"card_id": cid, "rating": 3, "ms": 1000})
r = c.get("/addon/answer_counter/"); assert r.status_code == 200
assert re.search(r"Good</td><td class=n>1<", r.text) and "<header>" in r.text   # in the app's own page shell
r = c.get("/addon/answer_counter/nope"); assert r.status_code == 404
ok("example add-on: after_answer counts the answer, its page shows it inside the app's look")

# Add-on pages need a login; disabled/unknown add-ons are a 404.
c2 = TestClient(webapp.app)
r = c2.get("/addon/answer_counter/", follow_redirects=False)
assert r.status_code == 303 and r.headers["location"] == "/login"
r = c.get("/addon/shout/", headers=HTML); assert r.status_code == 404 and "<header>" in r.text
r = c.get("/addon/../addons"); assert r.status_code in (200, 404)
ok("add-on pages need a login; pages of disabled or unknown add-ons are not found")

# Errors never stop studying; they are shown on the page.
write("broken.py", '''NAME = "Breaks"
def card_render(text, card, kind, col):
    raise ValueError("render boom")
def after_answer(col, card, rating):
    raise RuntimeError("answer boom")
from fastapi import APIRouter
router = APIRouter()
@router.get("/")
def home():
    raise KeyError("page boom")
''')
c.post("/addons/broken/enable")
r = c.get("/study/1"); assert r.status_code == 200 and "Answered today: 1" in r.text
with webapp.collection("addy") as col:
    q = col.sched.get_queued_cards()
    cid = q.cards[0].card.id if q.cards else None
if cid:
    r = c.post("/study/1/answer", data={"card_id": cid, "rating": 1, "ms": 500}); assert r.status_code == 200
r = c.get("/addon/broken/", headers=HTML); assert r.status_code == 500
r = c.get("/addons"); assert "Error:" in r.text and "boom" in r.text
ok("a failing add-on never stops studying or answering; its error shows on the Add-ons page")

# Import errors: shown when turned on, add-on not used.
write("crash.py", 'raise ImportError("no such thing")\n')
r = c.post("/addons/crash/enable"); assert "failed to load" in r.text and "no such thing" in r.text
r = c.get("/study/1"); assert r.status_code == 200
c.post("/addons/crash/disable")
ok("an add-on that fails to load is reported and skipped")

# Syntax errors can't be turned on.
write("typo.py", "def oops(:\n")
r = c.get("/addons"); assert "Syntax error on line 1" in r.text
assert '/addons/typo/enable' not in r.text
r = c.post("/addons/typo/enable"); assert "can&#x27;t be turned on" in r.text
assert "typo" not in addons.enabled_ids()
ok("add-ons with syntax errors are shown but can't be turned on")

# Desktop Anki add-ons are detected and refused.
os.makedirs(os.path.join(ADIR, "1234567"), exist_ok=True)
write("1234567/__init__.py", "from aqt import mw\nfrom aqt.qt import QAction\n")
write("1234567/manifest.json", '{"name": "Desktop thing"}')
r = c.get("/addons"); assert "Desktop Anki add-on: can't run here." in r.text
r = c.post("/addons/1234567/enable"); assert "1234567" not in addons.enabled_ids()
ok("desktop Anki add-ons (aqt imports / manifest.json) are recognised and can't be turned on")

# Edits are picked up without a restart.
c.post("/addons/broken/disable")
write("shout.py", '''NAME = "Shouter"
def card_render(text, card, kind, col):
    return text + "<i>shout-v2</i>"
''')
c.post("/addons/shout/enable")
r = c.get("/study/1"); assert "shout-v2" in r.text
write("shout.py", '''NAME = "Shouter"
def card_render(text, card, kind, col):
    return text + "<i>shout-v3</i>"
''')
r = c.get("/study/1"); assert "shout-v3" in r.text and "shout-v2" not in r.text
ok("a changed add-on file is loaded again on the next request")

# Bad names and unknown add-ons.
r = c.post("/addons/nope/enable"); assert "No such add-on." in r.text
r = c.post("/addons/shout/explode"); assert "No such add-on." in r.text
assert "nope" not in addons.enabled_ids()
# A removed add-on stays harmless.
os.remove(os.path.join(ADIR, "shout.py"))
r = c.get("/study/1"); assert r.status_code == 200 and "shout-" not in r.text
r = c.get("/addons"); assert "Shouter" not in r.text
ok("unknown add-ons and actions refused; a deleted add-on file is simply skipped")

# Enabled list persists in the data folder.
assert "answer_counter" in open(os.path.join(os.environ["DATA_DIR"], "addons.json")).read()
ok("on/off list kept in addons.json")

print("ALL ADD-ON TESTS PASSED")
