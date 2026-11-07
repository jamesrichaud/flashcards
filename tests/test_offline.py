"""Offline study, part 1: the offline switch (deck overview, Preferences), the deck snapshot
(today's queue within the limits + days ahead, study-screen rendering, engine labels, media),
read-only refreshes, render keys, and the browser script (pure helpers and a full refresh in node
with an in-memory IndexedDB / Cache Storage)."""
import html, json, os, re, shutil, subprocess, sys, tempfile
os.environ["DATA_DIR"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fastapi.testclient import TestClient
import app as webapp
from anki.scheduler.v3 import CardAnswer
from flashcards import offline

ok = lambda m: print("PASS", m)
c = TestClient(webapp.app)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ---------- login needed ----------
assert c.get("/offline/state").status_code == 401
assert c.post("/offline/deck/1/snapshot", json={}).status_code == 401
r = c.post("/offline/deck/1", data={"on": "1"}, follow_redirects=False)
assert r.status_code == 303 and r.headers["location"] == "/login"
assert c.post("/offline/remove_all", follow_redirects=False).headers["location"] == "/login"
assert "/offline/client.js" not in c.get("/login").text
ok("offline routes need a login; logged-out pages don't load the offline script")

c.post("/login", data={"username": "offl", "password": "secret123"})
USER = "offl"
with webapp.collection(USER) as col:
    did = col.decks.id("Spanish")
    sub = col.decks.id("Spanish::Verbs")
    other = col.decks.id("Other")
    basic = col.models.by_name("Basic")
    def add(front, deck, back="back"):
        n = col.new_note(basic); n["Front"] = front; n["Back"] = back; col.add_note(n, deck)
        return n
    for i in range(40):
        add(f"new {i:02d}", did)
    for i in range(5):
        add(f"verb {i}", sub)
    add("other card", other)
    pic = add('pic <img src="pic.png"> [sound:say.mp3] <img src="missing.png">', did)
    with open(os.path.join(col.media.dir(), "pic.png"), "wb") as f:
        f.write(b"\x89PNG" + b"0" * 996)
    with open(os.path.join(col.media.dir(), "say.mp3"), "wb") as f:
        f.write(b"1" * 2000)
    ids = list(col.find_cards('"deck:Spanish" -"deck:Spanish::Verbs"', order="c.due asc, c.id asc"))
    # a few review cards: today, tomorrow, in 2 and in 5 days
    rev_today, rev_1, rev_2, rev_5 = ids[30], ids[31], ids[32], ids[33]
    col.sched.set_due_date([rev_today], "0")
    col.sched.set_due_date([rev_1], "1")
    col.sched.set_due_date([rev_2], "2")
    col.sched.set_due_date([rev_5], "5")
    col.sched.suspend_cards([ids[34]])
    # one card in learning (answered Again)
    webapp.select_deck(col, did)
    q = col.sched.get_queued_cards()
    lc = col.get_card(q.cards[0].card.id); lc.start_timer()
    col.sched.answer_card(col.sched.build_answer(card=lc, states=q.cards[0].states, rating=CardAnswer.AGAIN))
    learning = lc.id
    pic_cid = pic.card_ids()[0]

# ---------- defaults: nothing offline ----------
st = c.get("/offline/state").json()
assert st["decks"] == [] and st["days"] == 3 and st["media_mb"] == 250 and st["version"].startswith("1|")
r = c.get(f"/deck/{did}")
assert 'id="fc-off"' in r.text and 'data-on="0"' in r.text and "Make available offline" in r.text
assert '<input type="hidden" name="on" value="1">' in r.text and 'role="switch" onchange' in r.text  # unchecked
assert '<script src="/offline/client.js" defer data-user="offl"></script>' in r.text
msgs = json.loads(html.unescape(re.search(r'data-msgs="([^"]*)"', r.text).group(1)))
assert msgs["ready"] == "Offline ready · updated {ago} · {cards}, {size}"
assert msgs["cards"]["forms"] == ["{n} card", "{n} cards"] and msgs["cards"]["idx"][1] == 0 and msgs["cards"]["idx"][2] == 1
ok("defaults: nothing offline, 3 days ahead, 250 MB; overview has the switch (off), status and messages")

# ---------- switch a deck on ----------
r = c.post(f"/offline/deck/{did}", data={"on": "1"})
assert "This deck will be kept for offline study on your devices." in r.text and 'data-on="1"' in r.text
assert 'role="switch" checked onchange' in r.text and 'id="fc-off-remove"' in r.text
st = c.get("/offline/state").json()
assert st["decks"] == [{"did": did, "name": "Spanish"}], st
with webapp.collection(USER) as col:
    assert col.get_config("flashcardsOffline")["decks"] == [did]
r = c.post(f"/offline/deck/{did}", data={"on": "1", "back": "//evil.example"}, follow_redirects=False)
assert r.headers["location"] == f"/deck/{did}"
assert c.post("/offline/deck/999999", data={"on": "1"}, follow_redirects=False).headers["location"] == "/"
ok("the deck switch saves per person and lists the deck in /offline/state; bad back links and decks refused")

# ---------- the snapshot is read-only ----------
with webapp.collection(USER) as col:
    webapp.select_deck(col, other)
    before = (col.mod, col.undo_status().undo, col.decks.get_current_id())
snap = c.post(f"/offline/deck/{did}/snapshot", json={}).json()
c.get("/offline/state")
with webapp.collection(USER) as col:
    after = (col.mod, col.undo_status().undo, col.decks.get_current_id())
assert before == after, (before, after)
ok("making a snapshot and reading the state change nothing: same modified time, undo step and current deck")

# ---------- what the snapshot holds ----------
qd = snap["queue"]
assert set(qd) == {"0", "1", "2", "3"}
assert qd["0"]["lrn"] == [learning]
assert qd["0"]["rev"] == [rev_today]
assert len(qd["0"]["new"]) == snap["counts"]["new"] == 19   # 20 new a day, one already studied (the learning card)
assert qd["1"]["rev"] == [rev_1] and qd["2"]["rev"] == [rev_2] and qd["3"]["rev"] == []
assert [len(qd[k]["new"]) for k in "0123"] == [19, 20, 1, 0], [len(qd[k]["new"]) for k in "0123"]  # 40 new in all
all_ids = [x["id"] for x in snap["cards"]]
assert rev_5 not in all_ids and ids[34] not in all_ids, "too far ahead / suspended cards are left out"
assert len(set(all_ids)) == len(all_ids)
new_all = qd["0"]["new"] + qd["1"]["new"] + qd["2"]["new"] + qd["3"]["new"]
assert len(new_all) == len(set(new_all))
with webapp.collection(USER) as col:
    verbs = set(col.find_cards('"deck:Spanish::Verbs"'))
    total_new = len(col.find_cards('"deck:Spanish" is:new -is:suspended'))
assert len(new_all) == min(total_new, 19 + 20 * 3)
assert verbs & set(new_all), "subdeck cards are part of the parent deck's copy"
assert snap["counts"]["lrn"] == 1 and snap["counts"]["rev"] == 1
cards = {x["id"]: x for x in snap["cards"]}
assert cards[learning]["group"] == "lrn" and cards[learning]["day"] == 0 and not cards[learning]["approx"]
assert cards[rev_1]["day"] == 1 and cards[rev_1]["approx"]
for x in snap["cards"]:
    assert len(x["labels"]) == 4 and all(x["labels"]), x["labels"]
    assert x["q"].startswith("<!doctype html>") and '<base href="/media/">' in x["q"] and x["rkey"]
conf = snap["confs"][cards[learning]["conf"]]
assert conf["learn"] == [1.0, 10.0] and conf["relearn"] == [10.0] and conf["autoplay"] is True
assert snap["name"] == "Spanish" and snap["days"] == 3 and not snap["filtered"] and not snap["truncated"]
ok("snapshot: learning + due reviews + new cards within today's limits, next 3 days' reviews and new cards, "
   "no suspended or later cards, subdecks included, engine labels and learning steps")

# media: pictures and sounds the card uses, with sizes; files that don't exist are left out
pc = cards[pic_cid]
assert pc["media"] == ["pic.png", "missing.png", "say.mp3"], pc["media"]
assert pc["av"]["q"] == [{"t": "sound", "src": "/media/say.mp3"}]
med = {m["name"]: m for m in snap["media"]}
assert set(med) == {"pic.png", "say.mp3"} and med["pic.png"]["size"] == 1000 and med["say.mp3"]["size"] == 2000
assert med["pic.png"]["url"] == "/media/pic.png" and med["pic.png"]["day"] == cards[pic_cid]["day"]
ok("snapshot media: the card's pictures and sounds with URL and size; missing files skipped; audio list kept")

# Same rendering and labels as the study screen.
r = c.get(f"/study/{did}")
top = int(re.search(r'name="card_id" value="(\d+)"', r.text).group(1))
Q = json.loads(re.search(r"const Q=(.*?),A=", r.text).group(1))
A = json.loads(re.search(r",A=(.*?),t0=", r.text).group(1))
snap = c.post(f"/offline/deck/{did}/snapshot", json={}).json()
cards = {x["id"]: x for x in snap["cards"]}
assert cards[top]["q"] == Q and cards[top]["a"] == A
shown = [html.unescape(x) for x in re.findall(r"<small>(.*?)</small>", r.text)]
assert shown == cards[top]["labels"], (shown, cards[top]["labels"])
ok("snapshot question/answer and button labels are exactly the study screen's")

# Add-on card output is included (the same card_render hook the study screen uses).
orig = webapp.addons.card_render
webapp.addons.card_render = lambda col, card, kind, text: text + f"<i>addon-{kind}</i>"
try:
    s2 = c.post(f"/offline/deck/{did}/snapshot", json={}).json()
finally:
    webapp.addons.card_render = orig
x = s2["cards"][0]
assert "<i>addon-question</i>" in x["q"] and "<i>addon-answer</i>" in x["a"]
ok("add-ons' card output is part of the offline copy")

# ---------- render keys: unchanged cards aren't rendered again ----------
known = {str(x["id"]): x["rkey"] for x in snap["cards"]}
s3 = c.post(f"/offline/deck/{did}/snapshot", json={"known": known}).json()
assert not any("q" in x for x in s3["cards"]) and all(x["labels"] for x in s3["cards"])
with webapp.collection(USER) as col:
    n = col.get_note(col.get_card(top).nid); n["Back"] = "changed back"; col.update_note(n)
s4 = c.post(f"/offline/deck/{did}/snapshot", json={"known": known}).json()
changed = [x for x in s4["cards"] if "q" in x]
assert [x["id"] for x in changed] == [top] and "changed back" in changed[0]["a"]
with webapp.collection(USER) as col:
    col.set_config(webapp.THEME_KEY, "dark")
s5 = c.post(f"/offline/deck/{did}/snapshot", json={"known": known}).json()
assert all("q" in x for x in s5["cards"]) and "night_mode" in s5["cards"][0]["q"]
with webapp.collection(USER) as col:
    col.set_config(webapp.THEME_KEY, "system")
r = c.post(f"/offline/deck/{did}/snapshot", content=b"not json", headers={"Content-Type": "application/json"})
assert r.status_code == 200 and all("q" in x for x in r.json()["cards"])
assert c.post("/offline/deck/424242/snapshot", json={}).status_code == 404
ok("render keys: only edited cards are rendered again; a theme change renders all; bad body = everything; 404")

# ---------- version changes when the collection changes ----------
v1 = c.get("/offline/state").json()["version"]
with webapp.collection(USER) as col:
    add("another", did)
v2 = c.get("/offline/state").json()["version"]
assert v1 != v2
ok("the state's version changes when the collection changes (so browsers refresh)")

# ---------- limits ----------
offline.MAX_CARDS = 5
try:
    s6 = c.post(f"/offline/deck/{did}/snapshot", json={}).json()
finally:
    offline.MAX_CARDS = 3000
assert len(s6["cards"]) == 5 and s6["truncated"] and s6["queue"]["0"]["lrn"] == [learning]
ok("a copy is capped (today's learning cards first) and marked as cut short")

# ---------- Preferences: all decks, days ahead, media limit ----------
r = c.get("/preferences")
assert 'id="fc-off-prefs"' in r.text and 'name="offline_all" value="1">' in r.text
assert '<option value="3" selected>Today + 3 days</option>' in r.text and '<option value="0">Today only</option>' in r.text
assert '<option value="250" selected>250 MB</option>' in r.text
assert 'form="fc-off-removeall"' in r.text and 'action="/offline/remove_all"' in r.text
r = c.post("/preferences", data={"rollover": "4", "learn_ahead": "20", "timebox": "0", "show_counts": "1",
                                 "show_times": "1", "theme": "system", "offline_all": "1", "offline_days": "1",
                                 "offline_media": "100"})
assert "Preferences saved." in r.text and 'name="offline_all" value="1" checked' in r.text
with webapp.collection(USER) as col:
    s = offline.settings(col)
    assert s["all"] and s["days"] == 1 and s["media_mb"] == 100 and s["decks"] == [], s
st = c.get("/offline/state").json()
assert [d["name"] for d in st["decks"]] == ["Other", "Spanish"], st   # top-level decks; empty Default left out
snap = c.post(f"/offline/deck/{did}/snapshot", json={}).json()
assert set(snap["queue"]) == {"0", "1"}
c.post("/preferences", data={"rollover": "4", "learn_ahead": "20", "timebox": "0", "offline_all": "1",
                             "offline_days": "7", "offline_media": "123"})
with webapp.collection(USER) as col:
    s = offline.settings(col)
    assert s["days"] == 1 and s["media_mb"] == 100
ok("Preferences: all decks (top-level decks, empty Default left out), days ahead and media limit; bad values ignored")

# under "all decks": a top-level deck can be switched off, a subdeck on by itself
r = c.get(f"/deck/{did}")
assert "All decks are kept offline (Preferences)." in r.text and 'data-on="1"' in r.text
c.post(f"/offline/deck/{did}", data={"on": "0"})
assert [d["name"] for d in c.get("/offline/state").json()["decks"]] == ["Other"]
c.post(f"/offline/deck/{sub}", data={"on": "1"})
assert [d["name"] for d in c.get("/offline/state").json()["decks"]] == ["Other", "Spanish::Verbs"]
c.post(f"/offline/deck/{did}", data={"on": "1"})
assert [d["name"] for d in c.get("/offline/state").json()["decks"]] == ["Other", "Spanish", "Spanish::Verbs"]
with webapp.collection(USER) as col:
    s = offline.settings(col)
    assert s["decks"] == [sub] and s["off"] == [], s
sv = c.post(f"/offline/deck/{sub}/snapshot", json={}).json()
assert sv["name"] == "Spanish::Verbs" and {x["did"] for x in sv["cards"]} == {sub}
ok("with all decks on: a deck can be switched off and on again; a subdeck can have its own copy")

r = c.post("/offline/remove_all")
assert "All offline copies were removed." in r.text
assert c.get("/offline/state").json()["decks"] == []
with webapp.collection(USER) as col:
    s = offline.settings(col)
    assert not s["all"] and s["decks"] == [] and s["days"] == 1   # other settings kept
ok("Remove all offline copies switches every deck off (keeps days and media limit)")

# a filtered deck: today only
with webapp.collection(USER) as col:
    fid = col.decks.new_filtered("Filtered")
    fd = col.sched.get_or_create_filtered_deck(fid)
    fd.config.search_terms[0].search = '"deck:Other"'
    col.sched.add_or_update_filtered_deck(fd)
c.post(f"/offline/deck/{fid}", data={"on": "1"})
fs = c.post(f"/offline/deck/{fid}/snapshot", json={}).json()
assert fs["filtered"] and set(fs["queue"]) == {"0"}, fs["queue"]
assert len(fs["cards"]) == 1
c.post(f"/offline/deck/{fid}", data={"on": "0"})
ok("filtered decks can be kept offline (their cards for today)")

# ---------- service worker keeps the media caches ----------
sw = c.get("/sw.js").text
assert "k.startsWith('fc-offline-')&&k!==C" in sw
ok("the service worker only deletes its own old caches, not the offline media")

# ---------- the browser script ----------
r = c.get("/offline/client.js")
assert r.status_code == 200 and "javascript" in r.headers["content-type"] and r.headers["cache-control"] == "no-cache"
CLIENT = r.text

node = None
try:
    from playwright._impl._driver import compute_driver_executable
    node = compute_driver_executable()[0]
except Exception:
    node = shutil.which("node")
if not (node and os.path.exists(node)):
    print("SKIP browser script in node (no node found)")
    sys.exit(0)

tmp = tempfile.mkdtemp()
cpath = os.path.join(tmp, "client.js")
open(cpath, "w").write(CLIENT)
assert subprocess.run([node, "--check", cpath], capture_output=True).returncode == 0

# Responses for the fake server: the real state and snapshots.
c.post(f"/offline/deck/{did}", data={"on": "1"})
with webapp.collection(USER) as col:
    s = offline.settings(col); s["days"] = 1; offline.save_settings(col, s)
    col.sched.set_due_date([pic_cid], "0")   # its picture and sound are needed today
state_on = c.get("/offline/state").json()
snap_full = c.post(f"/offline/deck/{did}/snapshot", json={}).json()
snap_known = c.post(f"/offline/deck/{did}/snapshot",
                    json={"known": {str(x["id"]): x["rkey"] for x in snap_full["cards"]}}).json()
c.post(f"/offline/deck/{did}", data={"on": "0"})
state_off = c.get("/offline/state").json()
fixtures = os.path.join(tmp, "fixtures.json")
json.dump({"state_on": state_on, "snap_full": snap_full, "snap_known": snap_known, "state_off": state_off,
           "msgs": offline.messages()}, open(fixtures, "w"))

HARNESS = r"""
const fs = require('fs');
const FX = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
// ---- an in-memory IndexedDB, just enough for the client ----
function later(f) { setTimeout(f, 0); }
const DBS = {};
function makeTx(dbo, names) {
  const tx = {pending: 0, done: false, oncomplete: null, onerror: null, onabort: null};
  const check = () => later(() => { if (!tx.done && tx.pending === 0) { tx.done = true; tx.oncomplete && tx.oncomplete(); } });
  tx.req = fn => { const r = {}; tx.pending++; later(() => { try { r.result = fn(); r.onsuccess && r.onsuccess(); }
    catch (e) { r.error = e; r.onerror && r.onerror(); } tx.pending--; check(); }); return r; };
  tx.objectStore = n => { const s = dbo.stores[n]; return {
    put: (v, k) => tx.req(() => { s.data.set(s.keyPath ? v[s.keyPath] : k, structuredClone(v)); }),
    get: k => tx.req(() => structuredClone(s.data.get(k))),
    getAll: () => tx.req(() => Array.from(s.data.values()).map(v => structuredClone(v))),
    getAllKeys: () => tx.req(() => Array.from(s.data.keys())),
    delete: k => tx.req(() => { s.data.delete(k); }) }; };
  check();
  return tx;
}
global.indexedDB = { open(name) { const r = {}; later(() => {
  let dbo = DBS[name]; const fresh = !dbo;
  if (fresh) dbo = DBS[name] = {stores: {}};
  const db = { objectStoreNames: {contains: n => n in dbo.stores},
    createObjectStore: (n, o) => { dbo.stores[n] = {keyPath: o && o.keyPath, data: new Map()}; },
    transaction: (names) => makeTx(dbo, names), close() {} };
  r.result = db;
  if (fresh && r.onupgradeneeded) r.onupgradeneeded();
  r.onsuccess && r.onsuccess(); }); return r; } };
// ---- Cache Storage ----
const CACHES = {};
global.caches = { open: async n => { const m = CACHES[n] = CACHES[n] || new Map(); return {
  keys: async () => Array.from(m.keys()).map(u => ({url: 'https://app.example' + u})),
  put: async (u, r) => { m.set(u, await r.text()); }, delete: async u => m.delete(u) }; } };
// ---- the server ----
let STATE = FX.state_on, SNAP = FX.snap_full; const SENT = [];
global.fetch = async (url, opts) => {
  if (url === '/offline/state') return new Response(JSON.stringify(STATE), {status: 200});
  if (/^\/offline\/deck\/\d+\/snapshot$/.test(url)) { SENT.push(JSON.parse(opts.body)); return new Response(JSON.stringify(SNAP)); }
  if (url.startsWith('/media/')) return url.includes('say') ? new Response('x'.repeat(2000)) : new Response('', {status: 404});
  return new Response('', {status: 404});
};
global.window = {isSecureContext: true};
const F = require(process.argv[3]);
F._setUser('offl');
(async () => {
  const out = {};
  // pure helpers
  out.size = [F.fmtSize(0), F.fmtSize(500), F.fmtSize(1536), F.fmtSize(12.3 * 1048576), F.fmtSize(3 * 1073741824)];
  out.plural = [F.plural(FX.msgs.cards, 1), F.plural(FX.msgs.cards, 54), F.plural(FX.msgs.cards, 201)];
  const now = 1e12;
  out.ago = [F.ago(now - 10e3, now, 'en'), F.ago(now - 120e3, now, 'en'), F.ago(now - 7200e3, now, 'en'),
             F.ago(now - 120e3, now, 'de')];
  out.fill = F.fill(FX.msgs.ready, {ago: '2 minutes ago', cards: '54 cards', size: '12 MB'});
  const E = [{url: '/media/a', size: 40, day: 1}, {url: '/media/b', size: 50, day: 0}, {url: '/media/c', size: 30, day: 0}];
  out.merge = F.mergeMedia([{media: E}, {media: [{url: '/media/a', size: 40, day: 0}]}]).map(e => e.url + e.day);
  const p = F.planMedia(F.mergeMedia([{media: E}]), new Set(['/media/c', '/media/old']), 85);
  out.plan = {fetch: p.fetch, drop: p.drop, capHit: p.capHit, bytes: p.bytes};
  const st = {version: 'v2', decks: [{did: 1}]};
  out.needs = [F.needsRefresh(null, st, now, false, []), F.needsRefresh({version: 'v2', lastTry: now - 1e3}, st, now, false, [1]),
    F.needsRefresh({version: 'v1', lastTry: now - 1e3}, st, now, false, [1]),
    F.needsRefresh({version: 'v1', lastTry: now - 120e3}, st, now, false, [1]),
    F.needsRefresh({version: 'v2', lastTry: now - 1e3}, st, now, false, []),
    F.needsRefresh({version: 'v2', lastTry: now - 1e3}, st, now, true, [1])];
  // a whole refresh: download
  await F.refresh(true);
  const db = DBS['fc-offline:offl'];
  const deck = db.stores.decks.data.get(FX.snap_full.did), meta = db.stores.meta.data.get('state');
  out.r1 = {decks: db.stores.decks.data.size, cards: db.stores.cards.data.size, cardIds: deck.cardIds.length,
    withQ: Array.from(db.stores.cards.data.values()).filter(x => x.q).length, version: meta.version === FX.state_on.version,
    media: Array.from((CACHES['fc-media:offl'] || new Map()).keys()), missing: meta.missing, error: meta.error,
    mediaBytes: deck.mediaBytes, bytes: deck.bytes > 0, sentKnown: Object.keys(SENT[0].known).length};
  // a second refresh: the server sends no HTML for known cards; the stored HTML is kept
  SNAP = FX.snap_known;
  await F.refresh(true);
  out.r2 = {sentKnown: Object.keys(SENT[1].known).length,
    withQ: Array.from(db.stores.cards.data.values()).filter(x => x.q && x.a).length, cards: db.stores.cards.data.size};
  // throttled: version unchanged = nothing fetched
  await F.refresh(false);
  out.r3 = {snapshots: SENT.length};
  // switched off: the copy, its cards and its media go
  STATE = FX.state_off;
  await F.refresh(false);
  out.r4 = {decks: db.stores.decks.data.size, cards: db.stores.cards.data.size,
    media: Array.from(CACHES['fc-media:offl'].keys()).length, snapshots: SENT.length};
  // the deck overview's status line
  const els = {};
  const el = id => (els[id] = els[id] || {id, textContent: '', style: {}, dataset: {}, children: [],
    classList: {toggle() {}}, appendChild(c) { this.children.push(c); }});
  el('fc-off').dataset = {did: String(FX.snap_full.did), on: '1', msgs: JSON.stringify(FX.msgs)};
  global.document = {documentElement: {lang: 'en'}, getElementById: id => (id in els || id.startsWith('fc-off') ? el(id) : null),
    createElement: () => ({textContent: ''})};
  STATE = FX.state_on; SNAP = FX.snap_full;
  await F.refresh(true);
  await F.render();
  out.status = els['fc-off-status'].textContent; out.removeShown = els['fc-off-remove'].style.display;
  console.log(JSON.stringify(out));
})().catch(e => { console.error(e); process.exit(1); });
"""
hpath = os.path.join(tmp, "harness.js")
open(hpath, "w").write(HARNESS)
p = subprocess.run([node, hpath, fixtures, cpath], capture_output=True, text=True, timeout=60)
assert p.returncode == 0, p.stderr
out = json.loads(p.stdout)
assert out["size"] == ["0 KB", "1 KB", "2 KB", "12 MB", "3.0 GB"], out["size"]
assert out["plural"] == ["1 card", "54 cards", "201 cards"]
assert out["ago"][0] == "now" and out["ago"][1] == "2 minutes ago" and out["ago"][2] == "2 hours ago", out["ago"]
assert out["ago"][3] == "vor 2 Minuten", out["ago"]
assert out["fill"] == "Offline ready · updated 2 minutes ago · 54 cards, 12 MB"
assert out["merge"] == ["/media/a0", "/media/b0", "/media/c0"], out["merge"]   # a: needed today by the 2nd deck
assert out["plan"] == {"fetch": ["/media/b"], "drop": ["/media/old"], "capHit": True, "bytes": 80}, out["plan"]
assert out["needs"][0] is True and out["needs"][1] is False and 0 < out["needs"][2] <= 60000
assert out["needs"][3] is True and out["needs"][4] is True and out["needs"][5] is True, out["needs"]
ok("browser helpers: sizes, plurals, 'updated 2 minutes ago' in the page language, media order and cap, throttle")
r1 = out["r1"]
assert r1["decks"] == 1 and r1["cards"] == r1["cardIds"] == len(snap_full["cards"]) == r1["withQ"], r1
assert r1["version"] and r1["error"] == "" and r1["sentKnown"] == 0, r1
# the fake server answers 404 for pic.png: counted as missing (tried again later), say.mp3 is kept
assert r1["media"] == ["/media/say.mp3"] and r1["missing"] == 1 and r1["mediaBytes"] == 2000 and r1["bytes"], r1
ok("refresh (node): the deck, its cards with their HTML and its media are stored; failed files are counted")
r2 = out["r2"]
assert r2["sentKnown"] == len(snap_full["cards"]) and r2["withQ"] == r2["cards"] == len(snap_full["cards"]), r2
assert out["r3"]["snapshots"] == 2
assert out["r4"] == {"decks": 0, "cards": 0, "media": 0, "snapshots": 2}, out["r4"]
ok("refresh (node): known cards keep their stored HTML, no refetch without changes, switching off removes everything")
assert out["status"].startswith("Offline ready · updated now · ") and " cards, " in out["status"], out["status"]
assert out["removeShown"] == ""
ok("deck overview status line: 'Offline ready · updated now · N cards, size' and the Remove button")

# Messages in another language: Polish plural table has three forms and picks them right.
with webapp.i18n.use_lang("pl"):
    pl = offline.plural_table("{n} card", "{n} cards")
f = lambda n: pl["forms"][pl["idx"][n if n < 200 else 100 + n % 100]].replace("{n}", str(n))
assert [f(1), f(2), f(5), f(22), f(25), f(112), f(1022)] == \
    ["1 karta", "2 karty", "5 kart", "22 karty", "25 kart", "112 kart", "1022 karty"], [f(1), f(2), f(5), f(22)]
ok("plural table for the browser follows each language's rules (Polish)")
print("ALL OFFLINE TESTS PASSED")
