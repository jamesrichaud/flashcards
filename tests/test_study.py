"""Study screen parity: Again re-shows, Edit returns to the same card, type-in answers,
audio play/replay/autoplay, leech notice."""
import os, re, sys, tempfile
from urllib.parse import quote
os.environ["DATA_DIR"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fastapi.testclient import TestClient
import app as webapp

ok = lambda m: print("PASS", m)
c = TestClient(webapp.app)
c.post("/login", data={"username": "tester", "password": "secret123"})

def new_deck(name, **conf_changes):
    """A deck with its own options preset, so changes don't leak into other decks."""
    c.post("/deck/new", data={"name": name})
    with webapp.collection("tester") as col:
        did = col.decks.id_for_name(name)
        if conf_changes:
            conf = col.decks.add_config_returning_id(name)
            deck = col.decks.get(did); deck["conf"] = conf; col.decks.save(deck)
            d = col.decks.get_config(conf)
            for path, val in conf_changes.items():
                tgt = d
                keys = path.split("__")
                for k in keys[:-1]: tgt = tgt[k]
                tgt[keys[-1]] = val
            col.decks.update_config(d)
    return did

def model_id(name):
    with webapp.collection("tester") as col:
        return col.models.by_name(name)["id"]

def shown_card(did):
    r = c.get(f"/study/{did}")
    m = re.search(r'name="card_id" value="(\d+)"', r.text)
    return (int(m.group(1)) if m else None), r

def answer(did, cid, rating):
    return c.post(f"/study/{did}/answer", data={"card_id": cid, "rating": rating, "ms": 3000},
                  follow_redirects=False)

basic = model_id("Basic")

# --- "Again" re-shows the card in the same session ---
d1 = new_deck("Again")
c.post("/add", data={"ntid": basic, "did": d1, "f0": "un", "f1": "one"})
cid, _ = shown_card(d1)
answer(d1, cid, 1)
again, r = shown_card(d1)
assert again == cid, "card answered Again should come straight back (learn-ahead)"
ok("Again: a lone card comes back in the same session")

# Steps longer than the learn-ahead limit: wait screen instead of 'finished'.
d2 = new_deck("LongStep", new__delays=[60.0])
c.post("/add", data={"ntid": basic, "did": d2, "f0": "deux", "f1": "two"})
cid, _ = shown_card(d2)
answer(d2, cid, 1)
r = c.get(f"/study/{d2}", follow_redirects=False)
assert r.status_code == 200 and "Nothing due right now" in r.text and "next learning card" in r.text, r.text[:300]
assert "location.replace('/study/" in r.text and "minutes" in r.text
ok("Again with a long step: waiting screen that reloads when the card is due")

# A finished deck still goes back to the overview with the congratulations message.
d3 = new_deck("Finish")
c.post("/add", data={"ntid": basic, "did": d3, "f0": "trois", "f1": "three"})
cid, _ = shown_card(d3)
answer(d3, cid, 4)
r = c.get(f"/study/{d3}", follow_redirects=False)
assert r.status_code == 303 and r.headers["location"] == f"/deck/{d3}"
ok("finished deck still returns to the overview")

# --- Edit returns to the same card, same side, timer kept ---
d4 = new_deck("EditBack")
c.post("/add", data={"ntid": basic, "did": d4, "f0": "quatre", "f1": "four"})
c.post("/add", data={"ntid": basic, "did": d4, "f0": "cinq", "f1": "five"})
cid, r = shown_card(d4)
assert 'onclick="edit();return false;"' in r.text and "function edit()" in r.text and "e.key==='e'" in r.text
with webapp.collection("tester") as col:
    nid = col.get_card(cid).nid
back = f"/study/{d4}?cid={cid}&shown=1&ms=5000"
r = c.get(f"/edit/{nid}?back={quote(back, safe='/')}")
assert quote(back, safe="/") in r.text, "edit form keeps the whole back URL, query included"
r = c.post(f"/edit/{nid}?back={quote(back, safe='/')}", data={"ntid": basic, "did": d4, "f0": "quatre!", "f1": "four"},
           follow_redirects=False)
assert r.status_code == 303 and r.headers["location"] == back, r.headers.get("location")
r = c.get(back)
assert f'name="card_id" value="{cid}"' in r.text and "if(true)show()" in r.text and "Date.now()-5000" in r.text
assert "quatre!" in r.text
assert 'toast(FL)' in r.text and '"Saved."' in r.text
ok("Edit returns to the same card with the answer still shown and the timer kept")
r = c.get(f"/study/{d4}?cid=1&shown=1&ms=5000")
assert "if(false)show()" in r.text and "Date.now()-0" in r.text
ok("a stale card id in the URL is ignored")
# Card info uses the same return path
assert "/info?back=/study/" in r.text and "encodeURIComponent(here())" in r.text
r = c.get(f"/card/{cid}/info?back={quote(back, safe='/')}")
assert f'href="{back.replace("&", "&amp;")}"' in r.text
ok("Card info's Back button returns to the same card and side")

# --- type in the answer ---
d5 = new_deck("Typing")
typ = model_id("Basic (type in the answer)")
c.post("/add", data={"ntid": typ, "did": d5, "f0": "chien", "f1": "dog"})
cid, r = shown_card(d5)
assert 'id=\\"typeans\\"' in r.text and "[[type:" not in r.text, "question shows an input box"
assert "HAS_TYPE=true" in r.text and "\\u003c!--typeans-->" in r.text
j = c.post(f"/study/{d5}/typeans", data={"card_id": cid, "typed": "dgo"}).json()
assert "typeBad" in j["html"] or "typeMissed" in j["html"], j
assert "typearrow" in j["html"]
j = c.post(f"/study/{d5}/typeans", data={"card_id": cid, "typed": "dog"}).json()
assert "typeGood" in j["html"] and "typeBad" not in j["html"] and "typearrow" not in j["html"], j
ok("type-in answer: input on the question, engine comparison on the answer")
# Enter in the box shows the answer; other keys in the box are not shortcuts.
assert "e.key==='Enter'&&e.target.id==='typeans'" in r.text and "tg==='INPUT'" in r.text
ok("typing in the box doesn't trigger shortcuts; Enter shows the answer")

# Cloze typing and unknown fields
with webapp.collection("tester") as col:
    m = col.models.copy(col.models.by_name("Cloze"), add=False)
    m["name"] = "Cloze typed"
    m["tmpls"][0]["qfmt"] = "{{cloze:Text}}<br>{{type:cloze:Text}}"
    m["tmpls"][0]["afmt"] = "{{cloze:Text}}<br>{{type:cloze:Text}}"
    col.models.add_dict(m)
d6 = new_deck("ClozeType")
c.post("/add", data={"ntid": model_id("Cloze typed"), "did": d6, "f0": "{{c1::Paris}} is in {{c2::France}}"})
with webapp.collection("tester") as col:
    cards = sorted(col.find_cards(f"did:{d6}"), key=lambda i: col.get_card(i).ord)
for card_id, word in zip(cards, ("Paris", "France")):
    j = c.post(f"/study/{d6}/typeans", data={"card_id": card_id, "typed": word}).json()
    assert "typeGood" in j["html"] and "typeBad" not in j["html"], (word, j)
ok("type:cloze: compares against the card's own cloze")
# The engine refuses templates naming a missing field, so check the filters directly.
q = webapp.type_question({"expected": None, "field": "Nope", "cloze": False}, "x [[type:Nope]]")
assert q == "x Type answer: unknown field Nope", q
assert "Empty Cards" in webapp.type_question({"expected": None, "field": "Text", "cloze": True}, "[[type:cloze:Text]]")
assert webapp.type_question({"expected": "", "field": "Back", "cloze": False}, "x [[type:Back]]") == "x "
assert webapp.type_answer({"expected": "", "field": "Back"}, "y [[type:Back]]") == "y "
ok("unknown type field shows Anki's warning; empty field removes the box")

# The typed text in a URL can't break out of the script
r = c.get(f"/study/{d5}?cid={cid}&typed=" + quote("</script><script>alert(1)</script>"))
assert "<script>alert(1)" not in r.text
ok("typed text from the URL is escaped")

# --- audio: replay buttons, autoplay settings, R key, TTS ---
d8 = new_deck("Audio")
c.post("/add", data={"ntid": basic, "did": d8, "f0": "bonjour [sound:bonjour.mp3]", "f1": "hello [sound:hello.mp3]"})
cid, r = shown_card(d8)
assert "<audio" not in r.text and "parent.fcPlay" in r.text and "replay-button" in r.text
assert '"src": "/media/bonjour.mp3"' in r.text and '"src": "/media/hello.mp3"' in r.text
assert '"autoplay": true' in r.text and '"replayq": true' in r.text
assert 'id="replay"' in r.text and "e.key==='r'" in r.text and "Replay audio" in r.text
assert "if(AV.autoplay&&!quiet)playList(AV.a)" in r.text and "quiet=false;" in r.text
r = c.get(f"/study/{d8}?cid={cid}&shown=1")
assert "quiet=true" in r.text   # back from Edit: no replay of the audio

ok("audio: replay buttons, R key, autoplay question then answer as in Anki")
d9 = new_deck("NoAuto", autoplay=False, replayq=False)
c.post("/add", data={"ntid": basic, "did": d9, "f0": "salut [sound:s.mp3]", "f1": "hi"})
cid, r = shown_card(d9)
assert '"autoplay": false' in r.text and '"replayq": false' in r.text
ok("deck options 'don't play audio automatically' and 'skip question when replaying' are honoured")
cid, r = shown_card(d1)
assert 'id="replay"' not in r.text
ok("no replay button on cards without audio")
with webapp.collection("tester") as col:
    m3 = col.models.copy(col.models.by_name("Basic"), add=False)
    m3["name"] = "Spoken"
    m3["tmpls"][0]["qfmt"] = "{{Front}} {{tts fr_FR speed=0.8:Front}}"
    col.models.add_dict(m3)
d10 = new_deck("TTS")
c.post("/add", data={"ntid": model_id("Spoken"), "did": d10, "f0": "merci", "f1": "thanks"})
cid, r = shown_card(d10)
assert '"t": "tts"' in r.text and '"lang": "fr-FR"' in r.text and '"speed": 0.8' in r.text
ok("TTS tags are spoken with the browser's speech synthesis")
c.post("/add", data={"ntid": basic, "did": d8, "f0": "film [sound:clip.mp4]", "f1": "x"})
with webapp.collection("tester") as col:
    card = col.get_card(col.find_cards("film")[0])
    assert "<video" in webapp.render_side(col, card, card.question())
ok("video files are shown inline")

# --- leech notice ---
d11 = new_deck("Leech", lapse__leechFails=1, lapse__leechAction=1)
c.post("/add", data={"ntid": basic, "did": d11, "f0": "leech1", "f1": "x"})
c.post("/add", data={"ntid": basic, "did": d11, "f0": "other1", "f1": "x"})
with webapp.collection("tester") as col:
    lc = col.find_cards("leech1")[0]
    col.sched.set_due_date([lc], "0!")
    col.sched.set_due_date([col.find_cards("other1")[0]], "0!")
cid, _ = shown_card(d11)
answer(d11, cid, 1)
r = c.get(f"/study/{d11}")
assert "Card was a leech." in r.text and "It has been suspended" not in r.text, r.text[-600:]
with webapp.collection("tester") as col:
    assert col.get_card(cid).note().has_tag("leech")
ok("leech (tag only): 'Card was a leech.' notice and leech tag")
r = c.get(f"/study/{d11}")
assert "Card was a leech." not in r.text
ok("leech notice shows once")

d12 = new_deck("LeechSusp", lapse__leechFails=1, lapse__leechAction=0)
c.post("/add", data={"ntid": basic, "did": d12, "f0": "leech2", "f1": "x"})
with webapp.collection("tester") as col:
    col.sched.set_due_date([col.find_cards("leech2")[0]], "0!")
cid, _ = shown_card(d12)
answer(d12, cid, 1)
r = c.get(f"/study/{d12}", follow_redirects=False)
assert r.status_code == 303   # only card was suspended -> deck finished, notice carried to the overview
r = c.get(r.headers["location"])
assert "Card was a leech. It has been suspended." in r.text and "Congratulations" in r.text
with webapp.collection("tester") as col:
    assert col.get_card(cid).queue == -1
ok("leech (suspend): card suspended and notice shown on the overview")

print("ALL STUDY TESTS PASSED")
