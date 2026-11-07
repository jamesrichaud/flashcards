"""Second set of bundled add-ons: Keyboard Shortcuts, More Overview Stats, Advanced Copy Fields,
Hierarchical Tags Filter and Anki Simulator, plus the page hooks they use."""
import html, json, os, re, shutil, subprocess, sys, tempfile, time
os.environ["DATA_DIR"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fastapi.testclient import TestClient
import app as webapp
from flashcards import addons

ok = lambda m: print("PASS", m)
c = TestClient(webapp.app)
c.post("/login", data={"username": "friend", "password": "secret123"})
NEW = {"keyboard_shortcuts": "Customize Keyboard Shortcuts", "overview_stats": "More Overview Stats",
       "copy_fields": "Advanced Copy Fields", "tag_filter": "Hierarchical Tags Filter", "simulator": "Anki Simulator"}

# 1. Listed, off by default
r = c.get("/addons")
for aid, name in NEW.items():
    assert name in r.text and f'id="addon-{aid}"' in r.text, name
    box = r.text.split(f'id="addon-{aid}"')[1].split('class="box addon"')[0]
    assert "/enable" in box and '<b class="rev">On</b>' not in box, aid
ok("the 5 new add-ons are listed and start switched off")

with webapp.collection("friend") as col:
    did = col.decks.id("Spanish")
    sub = col.decks.id("Spanish::Verbs")
    rows = [("el perro", "the dog", "animals::mammals", did), ("el gato", "the cat", "animals::mammals grammar", did),
            ("el pez", "the fish", "animals", did), ("comer", "to eat", "verbs::ar", sub), ("vivir", "to live", "", sub)]
    for q, a, tags, d in rows:
        n = col.new_note(col.models.by_name("Basic")); n["Front"] = q; n["Back"] = a; n.tags = tags.split()
        col.add_note(n, d)
    m = col.models.by_name("Basic (and reversed card)")
    n = col.new_note(m); n["Front"] = "rojo"; n["Back"] = "red"; col.add_note(n, did)
    # one mature card, one young, one suspended
    cids = col.find_cards('"deck:Spanish" is:new')
    col.sched.set_due_date([cids[0]], "1!")             # review, due tomorrow
    col.db.execute("update cards set ivl=30 where id=?", cids[0])
    col.sched.set_due_date([cids[1]], "0!")             # young review, due today
    col.sched.suspend_cards([cids[2]])

overview = lambda d=did: c.get(f"/deck/{d}").text
study = lambda: c.get(f"/study/{did}").text
base_ov, base_st = overview(), study()
for marker in ["os-tile", "tf-row", "Tags in this deck"]:
    assert marker not in base_ov, marker
assert "MAP=" not in base_st
assert "acf_from" not in c.get("/browse?q=deck:Spanish").text
ok("deck overview, study screen and Browse are unchanged while they are off")

for aid in NEW:
    r = c.post(f"/addons/{aid}/enable")
    assert "failed" not in r.text, (aid, re.findall(r'class="flash[^"]*">([^<]*)', r.text))
r = c.get("/addons")
for aid in NEW:
    box = r.text.split(f'id="addon-{aid}"')[1].split('class="box addon"')[0]
    assert '<b class="rev">On</b>' in box and "Error" not in box, aid
assert "Uses: deck overview" in r.text and "Uses: study screen, pages" in r.text and "Uses: Browse, pages" in r.text
ok("all 5 turn on without errors; the Add-ons page names the new hooks")

# 2. More Overview Stats
ov = overview()
tiles = dict((name, int(n)) for n, name in re.findall(r'class="os-tile"[^>]*><b>(\d+)</b><span>([^<]+)</span>', ov))
assert tiles == {"Total": 7, "New": 5, "Learning": 0, "Young": 1, "Mature": 1, "Suspended": 1, "Due tomorrow": 1}, tiles
assert "left today (8.0 s per card)" in ov and "14% of cards are mature." in ov
href = re.search(r'class="os-tile" href="([^"]+)"><b>1</b><span>Mature', ov).group(1)
assert c.get(href.replace("&amp;", "&")).text.count('name="ids"') == 1
sub_ov = overview(sub)
assert re.search(r'<b>2</b><span>Total', sub_ov)
ok("More Overview Stats: total/new/learning/young/mature/suspended/due tomorrow, time today, tiles link to Browse")

# 3. Hierarchical Tags Filter
assert "Tags in this deck" in ov
names = re.findall(r'<span class="tf-name">([^<]+)</span> <span class="muted">(\d+)</span>', ov)
assert names == [("animals", "3"), ("mammals", "2"), ("grammar", "1"), ("verbs", "1"), ("ar", "1")], names
link = re.search(r'href="(/browse\?q=[^"]*mammals[^"]*)"', ov).group(1)
assert c.get(link).text.count('name="ids"') == 2
study_link = re.search(r'href="(/filtered/new\?search=[^"]*verbs[^"]*)"', ov).group(1)
r = c.get(study_link); assert r.status_code == 200 and "tag:verbs" in r.text and "Spanish" in r.text
assert "tf-name" in sub_ov and "animals" not in sub_ov       # subdeck only has its own tags
with webapp.collection("friend") as col:
    empty = col.decks.id("Empty deck")
assert "No tags in this deck." in overview(empty)
ok("Hierarchical Tags Filter: nested tree with note counts, Browse and Study links, per deck")

# 4. Keyboard shortcuts: nothing changed -> no script; then remap
assert "MAP=" not in study()
r = c.get("/addon/keyboard_shortcuts/")
assert 'id="kb-bury"' in r.text and 'value="-"' in r.text and 'id="kb-flag1"' in r.text and "Red flag" in r.text
form = {"show": "Space", "again": "1", "hard": "2", "good": "3", "easy": "4", "undo": "U", "edit": "E", "info": "I",
        "replay": "R", "mark": "*", "bury": "B", "bury_note": "=", "suspend": "@", "suspend_note": "!",
        "flag1": "F1", "flag2": "", "flag3": "", "flag4": ""}
r = c.post("/addon/keyboard_shortcuts/", data=form)
assert "Saved." in r.text
with webapp.collection("friend") as col:
    assert col.get_config("addon_keyboard_shortcuts") == {"undo": "u", "bury": "b", "flag1": "F1"}
s = study()
mp = json.loads(re.search(r"MAP=(\{[^}]*\})", s).group(1)); off = json.loads(re.search(r"OFF=(\[[^\]]*\])", s).group(1))
assert mp == {"u": "undo", "b": "bury", "F1": "flag1"} and off == ["-"], (mp, off)
r = c.post("/addon/keyboard_shortcuts/", data=dict(form, edit="B"))
assert "One key can only do one thing: B" in r.text
r = c.post("/addon/keyboard_shortcuts/", data=dict(form, edit="NotAKey"))
assert "Not a key that can be used: NotAKey" in r.text
with webapp.collection("friend") as col:
    assert col.get_config("addon_keyboard_shortcuts") == {"undo": "u", "bury": "b", "flag1": "F1"}
r = c.post("/addon/keyboard_shortcuts/", data=dict(form, show="S", good="G"))
s = study(); off = json.loads(re.search(r"OFF=(\[[^\]]*\])", s).group(1))
assert off == [" ", "-", "3", "Enter"] and "OFF_SHOW_ONLY=false" in s, off
c.post("/addon/keyboard_shortcuts/", data=dict(form, show="S"))
s = study(); assert "OFF_SHOW_ONLY=true" in s
ok("Keyboard Shortcuts: per-person keys saved, clashes and bad keys refused, study screen gets the key map")

# the study script really does what it says, in node with a fake page
node = None
try:
    from playwright._impl._driver import compute_driver_executable
    node = compute_driver_executable()[0]
except Exception:
    node = shutil.which("node")
if node and os.path.exists(node):
    script = re.search(r"<script>(\(function\(\)\{\nvar MAP=.*?)</script>", s, re.S).group(1)
    harness = """
let calls=[];var shown=false;
function show(){calls.push('show');shown=true;} function answer(n){calls.push('answer'+n);}
function act(a,b){calls.push('act:'+a+(b!==undefined?':'+b:''));} function edit(){calls.push('edit');}
function info(){calls.push('info');} function replay(){calls.push('replay');}
var window={fcUndo:function(){calls.push('undo');}}; var fcUndo=window.fcUndo;
var handlers=[];var document={getElementById:function(){return null;},addEventListener:function(t,f,c){handlers.push(f);}};
function press(key,extra){var ev=Object.assign({key:key,target:{tagName:'BODY'},prevented:false,stopped:false,
 preventDefault(){this.prevented=true;},stopImmediatePropagation(){this.stopped=true;}},extra||{});handlers.forEach(h=>h(ev));return ev;}
""" + script + """
var r={};
r.space=press(' '); r.s=press('s'); r.u=press('U'); r.b=press('b'); r.dash=press('-'); r.f1=press('F1');
r.ctrl=press('b',{ctrlKey:true}); r.input=press('b',{target:{tagName:'INPUT'}});
shown=true; r.space2=press(' '); r.s2=press('s');
console.log(JSON.stringify({calls:calls,r:Object.fromEntries(Object.entries(r).map(([k,v])=>[k,[v.prevented,v.stopped]]))}));
"""
    path = os.path.join(tempfile.mkdtemp(), "kb.js")
    open(path, "w").write(harness)
    out = json.loads(subprocess.run([node, path], capture_output=True, text=True, check=True).stdout)
    assert out["calls"] == ["show", "undo", "act:bury", "act:flag:1"], out
    rr = out["r"]
    assert rr["space"] == [True, True] and rr["dash"] == [True, True]          # old keys switched off
    assert rr["ctrl"] == [False, False] and rr["input"] == [False, False]      # modifiers and typing left alone
    assert rr["space2"] == [False, False]      # once shown, Space still answers Good (study screen's own key)
    assert rr["s2"] == [True, True]            # show key does nothing more once shown
    ok("Keyboard Shortcuts: the study script maps keys, blocks the old ones, ignores Ctrl and typing (node)")
else:
    print("SKIP keyboard script in node (no node found)")
r = c.post("/addon/keyboard_shortcuts/", data={"reset": "1"})
assert "Standard keys restored." in r.text and "MAP=" not in study()
ok("Keyboard Shortcuts: back to the standard keys")

# 5. Advanced Copy Fields
b = c.get("/browse?q=deck:Spanish&mode=notes").text
assert 'name="acf_from"' in b and 'formaction="/addon/copy_fields/run"' in b and "data-addon" in b
with webapp.collection("friend") as col:
    perro, gato = col.find_notes("perro")[0], col.find_notes("gato")[0]
    rojo = col.find_notes("rojo")[0]
    col.get_note(gato)  # exists
back = "/browse?q=deck%3ASpanish&mode=notes"
r = c.post("/addon/copy_fields/run", data={"mode": "notes", "ids": [perro, gato], "acf_from": "Front", "acf_to": "Back",
                                          "acf_mode": "append", "acf_sep": "br", "back": back})
assert "Notes changed: 2." in r.text
with webapp.collection("friend") as col:
    assert col.get_note(perro)["Back"] == "the dog<br>el perro"
    assert col.undo_status().undo, "copy must be undoable"
r = c.post("/addon/copy_fields/run", data={"mode": "notes", "all": "1", "q": "deck:Spanish", "acf_from": "Back",
                                          "acf_to": "Front", "acf_mode": "move", "acf_sep": "space", "back": back})
with webapp.collection("friend") as col:
    n = col.get_note(perro)
    assert n["Front"] == "the dog<br>el perro" and n["Back"] == ""
    assert col.get_note(rojo)["Front"] == "red"
r = c.post("/addon/copy_fields/run", data={"mode": "notes", "ids": [perro], "acf_from": "Front", "acf_to": "Front",
                                          "back": back})
assert "Choose two different fields." in r.text
r = c.post("/addon/copy_fields/run", data={"mode": "cards", "ids": [], "acf_from": "Front", "acf_to": "Back", "back": back})
assert "Nothing selected." in r.text
with webapp.collection("friend") as col:
    m = col.models.new("Vocab"); [col.models.add_field(m, col.models.new_field(f)) for f in ("Word", "Meaning")]
    tmpl = col.models.new_template("Card 1"); tmpl["qfmt"] = "{{Word}}"; tmpl["afmt"] = "{{Meaning}}"
    col.models.add_template(m, tmpl); col.models.add(m)
    v = col.new_note(col.models.by_name("Vocab")); v["Word"] = "x"; col.add_note(v, did)
r = c.post("/addon/copy_fields/run", data={"mode": "notes", "ids": [perro, v.id], "acf_from": "Front", "acf_to": "Back",
                                          "acf_mode": "copy", "back": back})
assert "Notes changed: 1." in r.text and "Skipped, the field isn't in their note type: 1." in html.unescape(r.text)
ok("Advanced Copy Fields: append with separator, move over a whole search, undoable, bad input and other note types")

# 6. Anki Simulator
r = c.get("/addon/simulator/")
assert 'name="new"' in r.text and 'value="20"' in r.text and 'value="200"' in r.text and 'value="0.90"' in r.text
assert "Reviews per day" not in r.text
r = c.get(f"/addon/simulator/?did={did}&days=30&new=5&rev=100&retention=0.9&run=1")
assert "engine's FSRS simulator" in html.unescape(r.text) and 'class="chart"' in r.text, r.text[-3000:]
assert r.text.count('class="hit"') == 30 and "Day 1:" in r.text and "Average reviews per day" in r.text
nums = [int(x) for x in re.findall(r'data-tip="Day \d+: (\d+) reviews, \d+ new', r.text)]
news = [int(x) for x in re.findall(r'data-tip="Day \d+: \d+ reviews, (\d+) new', r.text)]
assert len(nums) == 30 and sum(nums) > 0 and 4 <= sum(news) <= 6, (nums, news)
r = c.get(f"/addon/simulator/?did={did}&days=30&new=5&rev=100&retention=0.9&extra=20&run=1")
news2 = [int(x) for x in re.findall(r'data-tip="Day \d+: \d+ reviews, (\d+) new', r.text)]
assert sum(news2) == sum(news) + 20, (news, news2)
r = c.get(f"/addon/simulator/?did={did}&days=999&new=-5&rev=10&retention=5&run=1")
assert r.status_code == 200 and r.text.count('class="hit"') == 90
ok("Anki Simulator: preset defaults, engine FSRS simulation with chart and summary, bad values cleaned")

sim = addons.load("simulator")
orig = sim.engine
sim.engine = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no simulator"))
try:
    r = c.get(f"/addon/simulator/?did={did}&days=30&new=2&rev=50&run=1")
    assert "simple projection" in r.text and r.text.count('class="hit"') == 30
    with webapp.collection("friend") as col:
        rows = sim.projection(col, '"deck:Spanish"', 30, 2, 50)
    assert [n for _, n, _ in rows[:4]] == [2, 2, 1, 0], rows[:4]     # 5 new cards (one is suspended), 2 a day
    assert rows[0][0] == 1 and rows[1][0] >= 3                      # today's review, then tomorrow's + new cards' first review
finally:
    sim.engine = orig
ok("Anki Simulator: falls back to a simple projection when the engine can't simulate")

# 7. A broken page hook never breaks the page
bad_dir = addons.addons_dir(); os.makedirs(bad_dir, exist_ok=True)
open(os.path.join(bad_dir, "bad_hooks.py"), "w").write(
    "def deck_overview(col, did):\n    raise RuntimeError('boom')\n"
    "def study_page(col, card):\n    return 42\n"
    "def browse_tools(col, mode):\n    return '<b id=bt-ok>hi</b>'\n")
c.post("/addons/bad_hooks/enable")
assert c.get(f"/deck/{did}").status_code == 200 and c.get(f"/study/{did}").status_code == 200
assert "bt-ok" in c.get("/browse").text
r = c.get("/addons").text
assert "study_page: must return HTML as text" in r or "deck_overview: RuntimeError: boom" in r
ok("a failing or wrong page hook is skipped and reported on the Add-ons page")

# 8. Every inline script on the changed pages parses (as the regression crawler checks for other pages)
c.post("/addon/keyboard_shortcuts/", data={"bury": "B"})
pages = [f"/deck/{did}", f"/study/{did}", "/browse", "/addon/keyboard_shortcuts/", "/addon/simulator/",
         f"/addon/simulator/?did={did}&run=1"]
if node and os.path.exists(node):
    d, bad = tempfile.mkdtemp(), []
    for url in pages:
        r = c.get(url); assert r.status_code == 200, url
        for i, code in enumerate(re.findall(r"<script>(.*?)</script>", r.text, re.S)):
            f = os.path.join(d, f"s{len(bad)}_{i}_{abs(hash(url))}.js"); open(f, "w").write(code)
            p = subprocess.run([node, "--check", f], capture_output=True, text=True)
            if p.returncode:
                bad.append(f"{url}: {p.stderr.strip()[-300:]}")
    assert not bad, "\n".join(bad)
    ok("inline scripts on the overview, study screen, Browse and the add-on pages parse (node --check)")
c.post("/addon/keyboard_shortcuts/", data={"reset": "1"})

# 9. Off again
for aid in list(NEW) + ["bad_hooks"]:
    c.post(f"/addons/{aid}/disable")
assert "os-tile" not in overview() and "tf-row" not in overview() and "acf_from" not in c.get("/browse").text
assert c.get("/addon/simulator/").status_code == 404
ok("turning them off removes them from the overview, Browse and their pages")
print("ALL MORE ADD-ON TESTS PASSED")
