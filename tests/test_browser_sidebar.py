"""Browse sidebar: sections, searches built by the engine, saved searches, tag tree actions, collapse state."""
import glob, html, os, re, shutil, subprocess, sys, tempfile
os.environ["DATA_DIR"] = tempfile.mkdtemp()
os.environ["REMINDERS"] = "0"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from urllib.parse import unquote, urlsplit, parse_qs
from fastapi.testclient import TestClient
import app as webapp
from flashcards import browser_sidebar as sb

ok = lambda m: print("PASS", m)
c = TestClient(webapp.app)
c.post("/login", data={"username": "tester", "password": "secret123"})
c.post("/deck/new", data={"name": "French::Verbs"})
c.post("/deck/new", data={"name": 'Odd "deck" name'})
with webapp.collection("tester") as col:
    verbs = col.decks.id_for_name("French::Verbs")
    french = col.decks.id_for_name("French")
    odd = col.decks.id_for_name('Odd "deck" name')
    basic = col.models.by_name("Basic")["id"]
    rev = col.models.by_name("Basic (and reversed card)")["id"]
c.post("/add", data={"ntid": basic, "did": verbs, "f0": "aller", "f1": "to go", "tags": "lang::fr::verb top"})
c.post("/add", data={"ntid": rev, "did": verbs, "f0": "être", "f1": "to be", "tags": "lang::fr::verb"})
c.post("/add", data={"ntid": basic, "did": odd, "f0": "odd", "f1": "x", "tags": "lang::de"})
with webapp.collection("tester") as col:
    nid_aller = col.find_notes("aller")[0]
    cid_aller = col.card_ids_of_note(nid_aller)[0]
    col.set_user_flag_for_cards(1, [cid_aller])
    col.sched.suspend_cards([col.card_ids_of_note(col.find_notes("odd")[0])[0]])

def links(text):
    """{label: search} for every sidebar link."""
    side = re.search(r'<aside class="sb".*?</aside>', text, re.S).group(0)
    out = {}
    for href, label in re.findall(r'<a class="sbl" href="([^"]+)"[^>]*>(.*?)</a>', side, re.S):
        label = html.unescape(re.sub(r"<[^>]+>", "", label)).strip()
        out.setdefault(label, parse_qs(urlsplit(html.unescape(href)).query).get("q", [""])[0])
    return out

def count(q):
    r = c.get("/browse", params={"q": q, "mode": "notes"})
    return int(re.search(r"(\d+) notes", r.text).group(1))

def flash(r):
    m = re.search(r'<div class="flash( err)?">(.*?)</div>', r.text)
    return ("!" if m.group(1) else "") + html.unescape(m.group(2)) if m else ""

def act(**data):
    return c.post("/browse/sidebar", data=data)

# --- sections and searches ---
r = c.get("/browse")
assert r.status_code == 200 and '<aside class="sb"' in r.text
for sec in ("Saved searches", "Today", "Flags", "Card state", "Decks", "Note types", "Tags"):
    assert f"<b>{sec}</b>" in r.text, sec
ok("Browse shows the sidebar with all seven sections")
L = links(r.text)
expect = {"Added today": "added:1", "Edited today": "edited:1", "Studied today": "rated:1", "Again today": "rated:1:1",
          "Red": "flag:1", "Purple": "flag:7", "No flag": "flag:0", "New": "is:new", "Learning": "is:learn",
          "Review": "is:review", "Suspended": "is:suspended", "Buried": "is:buried",
          "French": "deck:French", "Verbs": "deck:French::Verbs", 'Odd "deck" name': '"deck:Odd \\"deck\\" name"',
          "Basic": "note:Basic", "Basic (and reversed card)": '"note:Basic (and reversed card)"',
          "lang": "tag:lang", "fr": "tag:lang::fr", "verb": "tag:lang::fr::verb", "de": "tag:lang::de", "top": "tag:top"}
for label, q in expect.items():
    assert L.get(label) == q, (label, L.get(label), q)
ok("each item's search is the engine's (Today, flags, states, decks, note types, hierarchical tags; escaping)")
assert "Card 2" in L and L["Card 2"] == '"note:Basic (and reversed card)" card:2', L.get("Card 2")
ok("note types list their card types, searching note type AND card type")
assert count(L["Added today"]) == 3 and count(L["Red"]) == 1 and count(L["Suspended"]) == 1
assert count(L["Verbs"]) == 2 and count(L["French"]) == 2 and count(L['Odd "deck" name']) == 1
assert count(L["fr"]) == 2 and count(L["lang"]) == 3 and count(L["Card 2"]) == 1
ok("clicking the items finds the right notes (parent tags and decks include children)")
r = c.get("/browse", params={"q": "tag:lang::fr"})
on = re.findall(r'<div class="sbrow on">.*?</div>', r.text, re.S)
assert len(on) == 1 and ">fr<" in on[0] and 'aria-current="page"' in on[0], on
r = c.get("/browse", params={"q": "tag:lang::fr   "})
assert len(re.findall(r'<div class="sbrow on">', r.text)) == 1
ok("the current search's item is highlighted (compared in the engine's normal form)")

# --- Ctrl/Shift-click joins ---
r = c.get("/browse", params={"q": "deck:French", "add": "tag:top", "join": "and"}, follow_redirects=False)
assert r.status_code == 303 and unquote(r.headers["location"].split("q=")[1]) == "deck:French tag:top", r.headers["location"]
r = c.get("/browse", params={"q": "deck:French", "add": "flag:1", "join": "or"}, follow_redirects=False)
assert unquote(r.headers["location"].split("q=")[1]) == "deck:French OR flag:1", r.headers["location"]
r = c.get("/browse", params={"q": "", "add": "flag:1", "join": "and"}, follow_redirects=False)
assert unquote(r.headers["location"].split("q=")[1]) == "flag:1"
r = c.get("/browse", params={"q": "deck:(", "add": "flag:1", "join": "and"}, follow_redirects=False)
assert r.status_code == 303 and unquote(r.headers["location"].split("q=")[1]) == "flag:1"
ok("Ctrl-click ANDs and Shift-click ORs the item onto the search through the engine; a broken search falls back")

# --- saved searches ---
r = c.get("/browse")
assert "Save current search" not in r.text and "then save it here" in r.text
r = c.get("/browse", params={"q": "deck:French"})
assert "Save current search" in r.text
r = act(op="save", name="My French", q="deck:French  tag:lang*")
assert "added" in flash(r), flash(r)
with webapp.collection("tester") as col:
    assert col.get_config("savedFilters") == {"My French": "deck:French tag:lang*"}, col.get_config("savedFilters")
ok("Save current search stores it in Anki's savedFilters config (engine-normalised)")
r = c.get("/browse")
assert links(r.text).get("My French") == "deck:French tag:lang*" and 'data-kind="saved" data-name="My French"' in r.text
assert count("deck:French tag:lang*") == 2
r = act(op="save", name="My French", q="deck:French")
assert "updated" in flash(r)
r = act(op="save", name="Bad", q="deck:(")
assert flash(r).startswith("!"), flash(r)
r = act(op="save", name="", q="deck:French"); assert "name" in flash(r).lower()
r = act(op="save", name="x", q=""); assert "first" in flash(r)
with webapp.collection("tester") as col:
    assert set(col.get_config("savedFilters")) == {"My French"}
ok("saving under an existing name updates it; empty names, empty and broken searches are refused")
act(op="save", name="Other", q="is:new")
r = act(op="rename_saved", name="My French", new="Other")
assert "already" in flash(r)
r = act(op="rename_saved", name="My French", new="Français")
with webapp.collection("tester") as col:
    assert col.get_config("savedFilters") == {"Français": "deck:French", "Other": "is:new"}
r = act(op="rename_saved", name="Nope", new="x"); assert "no longer" in flash(r)
r = act(op="delete_saved", name="Other")
with webapp.collection("tester") as col:
    assert col.get_config("savedFilters") == {"Français": "deck:French"}
assert "deleted" in flash(r)
ok("saved searches can be renamed (no clashes) and deleted")
with webapp.collection("tester") as col:
    col.set_config("savedFilters", ["junk"])
assert c.get("/browse").status_code == 200
with webapp.collection("tester") as col:
    col.set_config("savedFilters", {"Français": "deck:French"})
ok("a damaged savedFilters value doesn't break the page")

# --- tags ---
r = c.post("/browse/sidebar", data={"op": "rename_tag", "name": "lang::fr", "new": "lang::french", "q": "tag:lang::fr"},
           follow_redirects=False)
assert unquote(r.headers["location"].split("q=")[1]) == "tag:lang::french", r.headers["location"]
r = c.get(r.headers["location"])
assert "renamed" in flash(r) and "2 notes" in flash(r), flash(r)
with webapp.collection("tester") as col:
    assert sorted(col.tags.all()) == ["lang::de", "lang::french::verb", "top"], col.tags.all()
    assert col.undo_status().undo, "rename should be undoable"
ok("renaming a tag renames it and its children on every note (undoable), and follows the current search")
r = act(op="rename_tag", name="top", new="two words"); assert "spaces" in flash(r)
r = act(op="rename_tag", name="top", new=""); assert flash(r).startswith("!")
ok("bad new tag names are refused")
r = c.post("/browse/sidebar", data={"op": "delete_tag", "name": "lang::french", "q": "tag:lang::french"},
           follow_redirects=False)
assert r.headers["location"] == "/browse?q=", r.headers["location"]
with webapp.collection("tester") as col:
    assert sorted(col.tags.all()) == ["lang::de", "top"], col.tags.all()
    assert col.get_note(nid_aller).tags == ["top"]
ok("deleting a tag removes it and its children from every note")
with webapp.collection("tester") as col:
    n = col.get_note(nid_aller); n.tags = []; col.update_note(n)
    assert "top" in col.tags.all()
r = act(op="clear_tags")
assert "1 unused tag cleared" in flash(r), flash(r)
with webapp.collection("tester") as col:
    assert col.tags.all() == ["lang::de"]
r = act(op="clear_tags"); assert "No unused tags" in flash(r)
ok("Clear unused tags uses the engine and reports the count")
r = act(op="bogus"); assert flash(r).startswith("!")

# --- collapse state in the engine ---
r = c.post("/browse/sidebar", data={"op": "collapse", "deck": str(french), "collapsed": "1"})
assert r.json()["ok"] and r.json()["stamp"]
r = c.get("/browse")
node = re.search(rf'<div class="sbn( shut)?"><div class="sbrow"><button type="button" class="sbt" data-deck="{french}"', r.text)
assert node and node.group(1) == " shut", "French should be shut"
with webapp.collection("tester") as col:
    assert col.decks.get(french)["browserCollapsed"] and not col.decks.get(french)["collapsed"]
c.post("/browse/sidebar", data={"op": "collapse", "deck": str(french), "collapsed": "0"})
r = c.get("/browse")
assert re.search(rf'<div class="sbn"><div class="sbrow"><button type="button" class="sbt" data-deck="{french}"', r.text)
ok("opening/closing a deck is saved as the deck's browser-collapsed flag (not the deck list's)")
c.post("/browse/sidebar", data={"op": "collapse", "tag": "lang", "collapsed": "0"})
r = c.get("/browse")
assert re.search(r'<div class="sbn"><div class="sbrow"><button type="button" class="sbt" data-tag="lang"', r.text)
c.post("/browse/sidebar", data={"op": "collapse", "tag": "lang", "collapsed": "1"})
r = c.get("/browse")
assert re.search(r'<div class="sbn shut"><div class="sbrow"><button type="button" class="sbt" data-tag="lang"', r.text)
ok("opening/closing a tag is saved in the engine's tag tree")
assert c.post("/browse/sidebar", data={"op": "collapse", "deck": "123"}).status_code == 400
assert c.post("/browse/sidebar", data={"op": "collapse"}).status_code == 400
ok("collapse with a missing deck is refused")

# --- page structure: narrow-screen panel, scripts parse, logged-out ---
r = c.get("/browse")
assert 'class="sbopen"' in r.text and "@media (max-width:1000px)" in r.text and "translateX(-105%)" in r.text
ids = re.findall(r'\sid="([^"]+)"', r.text)
assert len(ids) == len(set(ids)), "duplicate ids"
ok("on iPad width the sidebar is a slide-out panel with a ☰ Sidebar button")
node = shutil.which("node") or next(iter(glob.glob(os.path.join(
    os.path.dirname(__import__("playwright").__file__) if __import__("importlib").util.find_spec("playwright")
    else "/nonexistent", "driver", "node*"))), None)
if node:
    f = os.path.join(tempfile.mkdtemp(), "sb.js")
    open(f, "w").write(sb.JS.replace("<script>", "").replace("</script>", ""))
    p = subprocess.run([node, "--check", f], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    ok("sidebar script parses (node --check)")
else:
    print("SKIP sidebar script syntax check (no node found)")
anon = TestClient(webapp.app)
assert anon.post("/browse/sidebar", data={"op": "save", "name": "x", "q": "y"}, follow_redirects=False).status_code == 303
assert anon.post("/browse/sidebar", data={"op": "collapse", "deck": "1"}).status_code == 401
ok("logged-out requests are refused")
print("ALL BROWSER SIDEBAR TESTS PASSED")
