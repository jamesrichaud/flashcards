"""Deck list parity: collapse/expand, move deck, description, studied-today line, shared decks link."""
import html, os, re, sys, tempfile
os.environ["DATA_DIR"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fastapi.testclient import TestClient
import app as webapp  # imports anki.collection first (anki.decks alone hits a circular import)
from anki.decks import DeckCollapseScope
from flashcards import decks as deck_list

ok = lambda m: print("PASS", m)
c = TestClient(webapp.app)
c.post("/login", data={"username": "decky", "password": "secret123"})


def flash(r):
    m = re.search(r'<div class="flash( err)?">(.*?)</div>', r.text, re.S)
    return ("!" if m and m.group(1) else "") + html.unescape(m.group(2)) if m else ""


def deck_names(text):
    return [html.unescape(n) for n in re.findall(r'<a class="deck" href="/deck/\d+"[^>]*>(.*?)</a>', text)]


for name in ("Languages::French::Verbs", "Languages::Spanish", "Science"):
    c.post("/deck/new", data={"name": name})
with webapp.collection("decky") as col:
    lang = col.decks.id_for_name("Languages"); french = col.decks.id_for_name("Languages::French")
    verbs = col.decks.id_for_name("Languages::French::Verbs"); spanish = col.decks.id_for_name("Languages::Spanish")
    sci = col.decks.id_for_name("Science")
    basic = col.models.by_name("Basic")
    for i in range(3):
        n = col.new_note(basic); n["Front"] = f"verb {i}"; n["Back"] = "x"; col.add_note(n, verbs)
    n = col.new_note(basic); n["Front"] = "atom"; n["Back"] = "y"; col.add_note(n, sci)
    # decks made on the deck list have their parents opened, so the new deck shows
    assert not col.decks.get(lang)["collapsed"] and not col.decks.get(french)["collapsed"]

# --- collapse / expand ---
r = c.get("/")
assert deck_names(r.text) == ["Languages", "French", "Verbs", "Spanish", "Science"], deck_names(r.text)
assert f'action="/deck/{lang}/collapse"' in r.text and f'action="/deck/{verbs}/collapse"' not in r.text
assert 'aria-label="Collapse Languages"' in r.text
ok("deck list shows +/- only on decks with subdecks")
# counts of a parent include its subdecks (engine tree)
assert re.search(r">Languages</a></td>\s*<td class=\"n new\">3<", r.text); ok("parent counts include subdecks")

r = c.post(f"/deck/{lang}/collapse", data={"collapsed": "1"})
assert deck_names(r.text) == ["Languages", "Science"]; assert 'aria-label="Expand Languages"' in r.text
assert re.search(r">Languages</a></td>\s*<td class=\"n new\">3<", r.text)
with webapp.collection("decky") as col:
    assert col.decks.get(lang)["collapsed"] is True
ok("collapsing hides subdecks, keeps the parent's counts, saved in the deck (as Anki)")
c.post(f"/deck/{lang}/collapse", data={"collapsed": "1"})
assert deck_names(c.get("/").text) == ["Languages", "Science"]; ok("collapsing twice is harmless (no flip-flop on double tap)")
r = c.post(f"/deck/{lang}/collapse")
assert deck_names(r.text) == ["Languages", "French", "Verbs", "Spanish", "Science"]
ok("posting without a value toggles")
c.post(f"/deck/{french}/collapse", data={"collapsed": "1"})
assert deck_names(c.get("/").text) == ["Languages", "French", "Spanish", "Science"]; ok("nested collapse works")
c.post(f"/deck/{french}/collapse", data={"collapsed": "0"})
assert c.post("/deck/999999/collapse", data={"collapsed": "1"}).status_code == 200; ok("unknown deck is ignored")

# --- studied today + shared decks ---
r = c.get("/")
assert 'id="studied-today"' in r.text and "Studied 0 cards in 0 seconds today" in r.text, r.text[-1500:]
assert "⁨" not in r.text; ok("today line from the engine (bidi marks removed)")
r = c.get(f"/study/{verbs}"); cid = int(re.search(r'name="card_id" value="(\d+)"', r.text).group(1))
c.post(f"/study/{verbs}/answer", data={"card_id": cid, "rating": 3, "ms": 65000})
r = c.get("/")
with webapp.collection("decky") as col:
    expected = deck_list.clean_msg(col.studied_today())
assert "Studied 1 card" in expected and html.escape(expected) in r.text, expected
ok(f"today line updates after a review: {expected!r}")
assert 'href="https://ankiweb.net/shared/decks"' in r.text and 'rel="noopener noreferrer"' in r.text
assert 'href="/import"' in r.text; ok("Get shared decks and Import file links")

# --- move deck (Anki's drag and drop as a form) ---
r = c.get(f"/deck/{spanish}")
opts = re.findall(r'<option value="(\d+)"( selected)?>(.*?)</option>', r.text)
names = [html.unescape(o[2]) for o in opts]
assert "(top level)" in names and "Languages" in names and "Science" in names and "Languages::Spanish" not in names
assert [o for o in opts if o[2] == "Languages"][0][1] == " selected"; ok("move list shows the current parent selected")
r = c.get(f"/deck/{lang}")
names = [html.unescape(o[2]) for o in re.findall(r'<option value="(\d+)"( selected)?>(.*?)</option>', r.text)]
assert not any(n.startswith("Languages") for n in names) and "Science" in names
ok("a deck can't be moved under itself or its subdecks (not offered)")

with webapp.collection("decky") as col:
    col.decks.set_collapsed(sci, True, DeckCollapseScope.REVIEWER)
r = c.post(f"/deck/{spanish}/move", data={"parent": sci})
assert flash(r) == "Moved “Languages::Spanish” to “Science::Spanish”.", flash(r)
with webapp.collection("decky") as col:
    assert col.decks.name(spanish) == "Science::Spanish"
    assert col.decks.get(sci)["collapsed"] is False
assert deck_names(c.get("/").text) == ["Languages", "French", "Verbs", "Science", "Spanish"]
ok("moved Spanish under Science; new parent expanded so it shows")
r = c.post(f"/deck/{french}/move", data={"parent": 0})
assert "Moved" in flash(r)
with webapp.collection("decky") as col:
    assert col.decks.name(french) == "French" and col.decks.name(verbs) == "French::Verbs"
    assert col.card_count() == 4 and len(col.find_cards(f"did:{verbs}")) == 3
ok("moved French (with its subdeck and cards) to the top level")
r = c.post(f"/deck/{french}/move", data={"parent": verbs})
assert flash(r).startswith("!")
with webapp.collection("decky") as col:
    assert col.decks.name(french) == "French"
ok("moving a deck under its own subdeck is refused")
r = c.post(f"/deck/{french}/move", data={"parent": 0})
assert flash(r) == "!The deck can't be moved there."; ok("moving to where it already is says so")
with webapp.collection("decky") as col:
    fid = col.decks.new_filtered("Cram")
r = c.post(f"/deck/{verbs}/move", data={"parent": fid})
assert flash(r).startswith("!") and "Filtered" in flash(r), flash(r)
with webapp.collection("decky") as col:
    assert col.decks.name(verbs) == "French::Verbs"
assert f'<option value="{fid}"' not in c.get(f"/deck/{verbs}").text
ok("filtered decks can't take subdecks (engine error shown, not offered)")
r = c.post(f"/deck/{verbs}/move", data={"parent": 424242})
assert flash(r) == "!That deck no longer exists."; ok("missing target deck refused")
with webapp.collection("decky") as col:
    col.decks.rename(french, "Languages::French")
    assert col.undo_status().undo

# --- description ---
r = c.get(f"/deck/{sci}")
assert f'href="/deck/{sci}/description"' in r.text and 'class="desc"' not in r.text
ok("overview has a Description button and no description yet")
r = c.get(f"/deck/{sci}/description"); assert "<textarea" in r.text and "No description." in r.text
desc = ('<b>Physics</b> and <i>chemistry</i><script>alert(1)</script><style>body{display:none}</style>\n'
        '<a href="javascript:alert(2)" onclick="alert(3)">bad</a> <a href="https://example.org/x">site</a> '
        '<img src="atom.png" onerror="alert(4)"> <img src="/logout"> <iframe src="https://evil"></iframe>'
        '<p style="position:fixed">styled</p><unknown>kept text</unknown> 5 < 6 &amp; done')
r = c.post(f"/deck/{sci}/description", data={"desc": desc})
assert flash(r) == "Description saved."
with webapp.collection("decky") as col:
    d = col.decks.get(sci); assert d["desc"] == desc and not d.get("md")
m = re.search(r'<div class="desc">(.*?)</div>', r.text, re.S); shown = m.group(1)
assert "<b>Physics</b>" in shown and "<i>chemistry</i>" in shown
for bad in ("<script", "alert", "<style", "display:none", "javascript", "onclick", "onerror", "<iframe",
            "/logout", "position:fixed", "<unknown"):
    assert bad not in shown, (bad, shown)
assert '<a href="https://example.org/x" target="_blank" rel="noopener noreferrer">site</a>' in shown
assert '<img src="/media/atom.png">' in shown and "kept text" in shown and "5 &lt; 6 &amp; done" in shown
ok("description saved as typed and shown as cleaned HTML (no scripts, styles, handlers or js links)")
r = c.get(f"/deck/{sci}/description")
assert html.escape(desc) in r.text; ok("description page shows the saved text for editing")
p = deck_list.DescCleaner(); p.feed("<div><b>open <i>tags"); assert p.result() == "<div><b>open <i>tags</i></b></div>"
p = deck_list.DescCleaner(); p.feed("</b>x<br/>y<p/>"); assert p.result() == "x<br>y<p></p>"
ok("cleaner closes open tags and ignores stray ones")

with webapp.collection("decky") as col:
    d = col.decks.get(sci); d["desc"] = "# Title\n\n*md* <b>x</b>"; d["md"] = True; col.decks.update_dict(d)
r = c.get(f"/deck/{sci}")
assert "# Title\n\n*md* &lt;b&gt;x&lt;/b&gt;" in r.text; ok("markdown description from desktop Anki shown as plain text")
assert "markdown" in c.get(f"/deck/{sci}/description").text
c.post(f"/deck/{sci}/description", data={"desc": "plain"})
with webapp.collection("decky") as col:
    assert not col.decks.get(sci).get("md") and col.decks.get(sci)["desc"] == "plain"
c.post(f"/deck/{sci}/description", data={"desc": ""})
assert 'class="desc"' not in c.get(f"/deck/{sci}").text; ok("saving here turns markdown off; empty description hides it")

r = c.get(f"/deck/{fid}")
assert "/description" not in r.text; ok("filtered decks have no description (as in Anki)")
r = c.get(f"/deck/{fid}/description", follow_redirects=False); assert r.status_code == 303
r = c.post(f"/deck/{fid}/description", data={"desc": "x"}, follow_redirects=False); assert r.status_code == 303
with webapp.collection("decky") as col:
    assert not col.decks.get(fid).get("desc")
ok("filtered deck description routes refuse")

# --- rename error shown instead of a crash; other users can't be touched ---
r = c.post(f"/deck/{sci}/rename", data={"name": "  "})
assert r.status_code == 200 and flash(r) == "!Please type a deck name."
with webapp.collection("decky") as col:
    assert col.decks.name(sci) == "Science"
r = c.post(f"/deck/{sci}/rename", data={"name": "Languages"})
with webapp.collection("decky") as col:
    assert r.status_code == 200 and col.decks.name(sci) == "Languages+"  # the engine's rule for clashes
ok("blank rename shows a message; a clashing name gets the engine's + suffix")
c2 = TestClient(webapp.app)
r = c2.post(f"/deck/{sci}/collapse", data={"collapsed": "1"}, follow_redirects=False)
assert r.status_code == 303 and r.headers["location"] == "/login"; ok("logged-out users can't change decks")

print("ALL DECK LIST TESTS PASSED")
