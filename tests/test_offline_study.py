"""Offline study, part 2: studying with no connection. The snapshot's extra data (the engine's next
states, step labels, the prepared type-in answer), the offline study page, the bundled MathJax, the
service worker (offline page for failed pages, failed answers handed to the offline page, media from
the cache with Range requests), and the browser logic in node: the session (order, counts, learning
cards coming back after their step, labels, later days), the learning steps checked against the
engine, the answer queue and undo, and typed answers compared exactly like the engine."""
import html, json, os, random, re, shutil, subprocess, sys, tempfile
os.environ["DATA_DIR"] = tempfile.mkdtemp()
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from fastapi.testclient import TestClient
import app as webapp
from anki.scheduler.v3 import CardAnswer
from flashcards import offline

ok = lambda m: print("PASS", m)
c = TestClient(webapp.app)
RATINGS = {1: CardAnswer.AGAIN, 2: CardAnswer.HARD, 3: CardAnswer.GOOD, 4: CardAnswer.EASY}

# ---------- the offline study page and bundled files work without a login ----------
r = c.get("/offline/study")
assert r.status_code == 200 and r.headers["cache-control"] == "no-cache" and "text/html" in r.headers["content-type"]
assert 'id="fco-list"' in r.text and 'id="fco-study"' in r.text and 'id="frame"' in r.text and 'id="wbtoggle"' in r.text
assert '<script src="/offline/client.js" data-user="" data-page="study"></script>' in r.text
assert "/sync/" not in r.text and "fc-live" not in r.text   # no server polling on the offline page
assert not re.search(r'(?:src|href)="(?:https?:)?//', r.text), "the offline page must not load outside files"
cfg = json.loads(re.search(r"window\.FCO=(\{.*?\});</script>", r.text).group(1))
assert cfg["user"] == "" and cfg["prefs"] == {"times": True, "counts": True, "tap": False} and cfg["slot"] == "<!--typeans-->"
assert cfg["msgs"]["waiting"]["forms"] == ["{n} answer waiting to sync", "{n} answers waiting to sync"]
ok("offline study page: served without a login, no outside files, no server polling, settings and messages")

r = c.get("/static/mathjax/tex-chtml-full.js")
assert r.status_code == 200 and r.headers["content-type"].startswith("text/javascript") and len(r.content) > 1_000_000
r = c.get("/static/mathjax/output/chtml/fonts/woff-v2/MathJax_Main-Regular.woff")
assert r.status_code == 200 and r.headers["content-type"] == "font/woff"
for bad in ("/static/../app.py", "/static/mathjax/../../offline.py", "/static/%2e%2e/offline.py", "/static/nope.js",
            "/static/mathjax"):
    assert c.get(bad).status_code == 404, bad
assets = offline.assets()
assert "/static/mathjax/tex-chtml-full.js" in assets and len(assets) > 20 and all(a.startswith("/static/mathjax/") for a in assets)
doc = webapp.card_doc(r"\(x^2\)")
assert '<script async src="/static/mathjax/tex-chtml-full.js"' in doc and "cdn." not in doc and "http" not in doc
ok("MathJax is bundled (Anki's own build and fonts), served locally, folder escapes refused; cards use it")

# ---------- a collection to study ----------
c.post("/login", data={"username": "offs", "password": "secret123"})
USER = "offs"
with webapp.collection(USER) as col:
    did = col.decks.id("Lang")
    basic = col.models.by_name("Basic")
    typed_nt = col.models.by_name("Basic (type in the answer)")
    def add(front, back="back", nt=basic, deck=did):
        n = col.new_note(nt); n["Front"] = front; n["Back"] = back; col.add_note(n, deck)
        return n.card_ids()[0]
    new_ids = [add(f"new {i}") for i in range(6)]
    type_cid = add("capital of France?", "<b>Paris</b> &amp; co", typed_nt)
    math_cid = add(r"math \(x^2\)")
    rev_cid = add("a review card")
    col.sched.set_due_date([rev_cid], "0!")
    rev_later = add("review in 1 day")
    col.sched.set_due_date([rev_later], "1!")
    # a card in learning (Again once), from the engine
    webapp.select_deck(col, did)
    q = col.sched.get_queued_cards()
    lc = col.get_card(q.cards[0].card.id); lc.start_timer()
    col.sched.answer_card(col.sched.build_answer(card=lc, states=q.cards[0].states, rating=CardAnswer.AGAIN))
    learning = lc.id
c.post(f"/offline/deck/{did}", data={"on": "1"})

st = c.get("/offline/state").json()
assert st["assets"] == assets
snap = c.post(f"/offline/deck/{did}/snapshot", json={}).json()
cards = {x["id"]: x for x in snap["cards"]}
assert snap["learnAhead"] == 1200
conf = snap["confs"][cards[new_ids[1]]["conf"]]
assert conf["learn"] == [1.0, 10.0] and conf["relearn"] == [10.0]
assert conf["labels"] == {"learn": ["1m", "10m"], "relearn": ["10m"], "learnHard": "6m", "relearnHard": "15m",
                          "grad": "1d", "easy": "4d"}, conf["labels"]
n1 = cards[new_ids[1]]["next"]
assert n1[0] == {"k": "l", "secs": 60, "rem": 2} and n1[2] == {"k": "l", "secs": 600, "rem": 1} and n1[3] == {"k": "r"}, n1
rv = cards[rev_cid]
assert rv["next"][0]["k"] == "rl" and rv["next"][0]["secs"] == 600 and rv["next"][0]["rem"] == 1, rv["next"]
assert rv["next"][2] == {"k": "r"} and rv["rl"] and all(x.endswith("d") for x in rv["rl"]), rv["rl"]
assert cards[new_ids[1]]["rl"] is None
assert cards[type_cid]["tplain"] == {"text": "Paris & co", "combining": True}, cards[type_cid]["tplain"]
assert cards[new_ids[1]]["tplain"] is None
assert "/static/mathjax/tex-chtml-full.js" in cards[math_cid]["q"]
assert cards[learning]["group"] == "lrn" and cards[learning]["next"][0]["k"] == "l"
ok("snapshot: the engine's next states per button, step labels per preset, relearn labels, learn-ahead, typed-answer text")

r = c.get("/offline/study")
cfg = json.loads(re.search(r"window\.FCO=(\{.*?\});</script>", r.text).group(1))
assert cfg["user"] == USER and f'data-user="{USER}" data-page="study"' in r.text
with webapp.collection(USER) as col:
    col.set_config("flashcardsTapZones", True)
    p = col.get_preferences(); p.reviewing.show_intervals_on_buttons = False; col.set_preferences(p)
    col.set_config("flashcardsTheme", "dark")
r = c.get("/offline/study")
cfg = json.loads(re.search(r"window\.FCO=(\{.*?\});</script>", r.text).group(1))
assert cfg["prefs"] == {"times": False, "counts": True, "tap": True} and '<html class="dark">' in r.text
with webapp.collection(USER) as col:
    col.set_config("flashcardsTapZones", False)
    p = col.get_preferences(); p.reviewing.show_intervals_on_buttons = True; col.set_preferences(p)
    col.set_config("flashcardsTheme", "system")
r = c.get("/offline/study", headers={"Accept-Language": "de"})
ok("offline study page carries the logged-in person's settings (theme, tap zones, button times)")

# Every page links to it from the offline banner.
r = c.get("/")
assert '<a href="/offline/study">Study offline</a>' in r.text
ok("the offline banner on every page links to the offline study page")

# ---------- node ----------
node = None
try:
    from playwright._impl._driver import compute_driver_executable
    node = compute_driver_executable()[0]
except Exception:
    node = shutil.which("node")
if not (node and os.path.exists(node)):
    print("SKIP browser parts in node (no node found)")
    print("ALL OFFLINE STUDY TESTS PASSED")
    sys.exit(0)
tmp = tempfile.mkdtemp()
cpath = os.path.join(tmp, "client.js")
open(cpath, "w").write(c.get("/offline/client.js").text)
page = c.get("/offline/study").text
for i, s in enumerate(re.findall(r"<script>(.*?)</script>", page, re.S)):
    sp = os.path.join(tmp, f"inline{i}.js"); open(sp, "w").write(s)
    p = subprocess.run([node, "--check", sp], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
assert subprocess.run([node, "--check", cpath], capture_output=True).returncode == 0
ok("the offline study page's scripts and the offline script are valid JavaScript")


def run(js, *args):
    path = os.path.join(tmp, f"h{random.randrange(1 << 30)}.js")
    open(path, "w").write(js)
    p = subprocess.run([node, path, *args], capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, p.stderr + p.stdout
    return json.loads(p.stdout)


# ---------- the service worker ----------
SW = c.get("/sw.js").text
assert "k.startsWith('fc-offline-')&&k!==C" in SW and "const C='fc-offline-v2'" in SW
SWPATH = os.path.join(tmp, "sw.js")
open(SWPATH, "w").write(SW)
sw_out = run(r"""
const fs = require('fs');
const L = {};
global.self = {location: {origin: 'https://app.example'}, addEventListener: (t, f) => { L[t] = f; },
  skipWaiting() {}, clients: {claim() {}}, registration: {}};
const STORE = {'fc-shell': new Map([['/offline/study', 'SHELL']]), 'fc-offline-v2': new Map([['/offline', 'OFFLINE']]),
  'fc-media:offs': new Map([['/media/a.mp3', '0123456789']])};
const mk = (body, u) => new Response(body, {status: 200, headers: {'Content-Type': u.endsWith('.mp3') ? 'audio/mpeg' : 'text/html'}});
global.caches = {
  match: async (u, o) => { const p = typeof u === 'string' ? u : new URL(u.url).pathname;
    for (const m of Object.values(STORE)) if (m.has(p)) return mk(m.get(p), p); return undefined; },
  open: async n => ({match: async u => (STORE[n] && STORE[n].has(u) ? mk(STORE[n].get(u), u) : undefined)}),
  keys: async () => Object.keys(STORE)};
let ONLINE = false;
global.fetch = async r => { if (!ONLINE) throw new TypeError('Failed to fetch'); return new Response('NET'); };
global.clients = {};
eval(fs.readFileSync(process.argv[2], 'utf8'));
async function ev(url, init, mode) {
  const req = new Request('https://app.example' + url, init);
  let resp = null; const e = {request: new Proxy(req, {get: (t, k) => (k === 'mode' ? mode : (typeof t[k] === 'function' ? t[k].bind(t) : t[k]))}),
    respondWith: p => { resp = p; }, waitUntil() {}};
  L.fetch(e);
  if (!resp) return null;
  const r = await resp;
  return {status: r.status, type: r.type, loc: r.headers.get('location'), range: r.headers.get('content-range'),
    body: r.type === 'error' ? '' : await r.text()};
}
(async () => {
  const out = {};
  out.page = await ev('/study/5', {}, 'navigate');
  out.home = await ev('/', {}, 'navigate');
  const fd = new URLSearchParams({card_id: '42', rating: '3', ms: '1500'});
  out.answer = await ev('/study/5/answer', {method: 'POST', body: fd, headers: {'Content-Type': 'application/x-www-form-urlencoded'}}, 'navigate');
  out.other = await ev('/browse/bulk', {method: 'POST', body: 'x=1', headers: {'Content-Type': 'application/x-www-form-urlencoded'}}, 'navigate');
  out.media = await ev('/media/a.mp3', {}, 'no-cors');
  out.range = await ev('/media/a.mp3', {headers: {Range: 'bytes=2-5'}}, 'no-cors');
  out.tail = await ev('/media/a.mp3', {headers: {Range: 'bytes=7-'}}, 'no-cors');
  out.api = await ev('/offline/state', {}, 'cors');
  out.missing = await ev('/media/b.png', {}, 'no-cors');
  ONLINE = true;
  out.online = await ev('/study/5', {}, 'navigate');
  out.onlineMedia = await ev('/media/a.mp3', {}, 'no-cors');
  STORE['fc-shell'].delete('/offline/study');
  ONLINE = false;
  out.noShell = await ev('/study/5', {}, 'navigate');
  out.noShellAnswer = await ev('/study/5/answer', {method: 'POST', body: fd, headers: {'Content-Type': 'application/x-www-form-urlencoded'}}, 'navigate');
  console.log(JSON.stringify(out));
})().catch(e => { console.error(e); process.exit(1); });
""", SWPATH)
assert sw_out["page"]["body"] == "SHELL" and sw_out["home"]["body"] == "SHELL", sw_out
assert sw_out["answer"]["status"] == 303, sw_out["answer"]
loc = sw_out["answer"]["loc"]
assert loc.startswith("https://app.example/study/5?fcans="), loc
a = json.loads(re.sub(r"^.*fcans=", "", __import__("urllib.parse").parse.unquote(loc)))
assert a["cid"] == 42 and a["rating"] == 3 and a["ms"] == 1500 and a["ts"] > 0, a
assert sw_out["other"]["body"] == "OFFLINE"
assert sw_out["media"]["body"] == "0123456789" and sw_out["media"]["status"] == 200
assert sw_out["range"]["status"] == 206 and sw_out["range"]["body"] == "2345" and sw_out["range"]["range"] == "bytes 2-5/10"
assert sw_out["tail"]["body"] == "789" and sw_out["tail"]["range"] == "bytes 7-9/10"
assert sw_out["api"] is None   # API calls are left to the page (they fail and the page says so)
assert sw_out["missing"]["type"] == "error"
assert sw_out["online"]["body"] == "NET" and sw_out["onlineMedia"]["body"] == "NET"
assert sw_out["noShell"]["body"] == "OFFLINE" and sw_out["noShellAnswer"]["body"] == "OFFLINE"
ok("service worker: pages → offline study page, a failed answer → handed to it, other posts → offline page, "
   "media from the cache with Range requests, network first while online")

# ---------- the session in node ----------
FAKE_IDB = r"""
function later(f) { setTimeout(f, 0); }
const DBS = {};
function makeTx(dbo) {
  const tx = {pending: 0, done: false};
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
    transaction: () => makeTx(dbo), close() {} };
  r.result = db;
  if (fresh && r.onupgradeneeded) r.onupgradeneeded();
  r.onsuccess && r.onsuccess(); }); return r; } };
global.caches = {open: async () => ({keys: async () => [], put: async () => {}, delete: async () => {}})};
global.window = {isSecureContext: true};
"""
fx = os.path.join(tmp, "snap.json")
json.dump({"snap": snap, "ids": {"new": new_ids, "type": type_cid, "rev": rev_cid, "revLater": rev_later,
                                 "learning": learning}}, open(fx, "w"))
out = run(FAKE_IDB + r"""
const fs = require('fs');
const FX = JSON.parse(fs.readFileSync(process.argv[2], 'utf8')), S = FX.snap, I = FX.ids;
const F = require(process.argv[3]);
const deck = {did: S.did, name: S.name, cutoff: S.cutoff, today: S.today, days: S.days, counts: S.counts, queue: S.queue,
  cardIds: S.cards.map(c => c.id), confs: S.confs, learnAhead: S.learnAhead};
const cards = new Map(S.cards.map(c => [c.id, c]));
const out = {};
const T0 = S.generated * 1000;   // "now": when the copy was made
let answers = [], ts = T0;
const ses = (now, prefer) => F.session(deck, cards, answers, now, prefer);
out.first = ses(T0);
out.serverCounts = S.counts;
// study everything today: Again on the first new card, Good on the rest
const seen = [];
let s = ses(ts), againCid = 0, n = 0;
while (s.cid && n++ < 50) {
  seen.push([s.cid, s.kind, s.labels]);
  const isNew = I.new.includes(s.cid);
  const rating = !againCid && isNew ? 1 : 3;
  if (rating === 1) againCid = s.cid;
  answers.push({cid: s.cid, rating: rating, ts: ts, ms: 3000});
  ts += 5000;
  s = ses(ts);
  if (!s.cid && s.wait) { out.waitMs = s.wait; out.waitCounts = s.counts; ts += s.wait; s = ses(ts); }
}
out.againCid = againCid;
out.seen = seen;
out.end = s;
// learn ahead: a card due within 20 minutes is shown when nothing else is left
answers = [{cid: I.new[0], rating: 1, ts: T0}];
const only = new Map([[I.new[0], cards.get(I.new[0])]]);
const tiny = Object.assign({}, deck, {queue: {'0': {lrn: [], rev: [], new: [I.new[0]]}}, cardIds: [I.new[0]]});
out.ahead = F.session(tiny, only, answers, T0 + 1000);
out.noAhead = F.session(Object.assign({}, tiny, {learnAhead: 0}), only, answers, T0 + 1000);
// the next day: day-1 cards come in (marked approximate)
answers = [];
const tomorrow = (S.cutoff + 3600) * 1000;
out.day1 = {day: F.dayIndex(deck, tomorrow), today: F.dayIndex(deck, T0)};
const all = []; let t = tomorrow, x = ses(t), guard = 0;
while (x.cid && guard++ < 200) { all.push([x.cid, x.approx, x.labels]); answers.push({cid: x.cid, rating: 3, ts: t}); t += 1000; x = ses(t); }
out.day1.ids = all.map(a => a[0]);
out.day1.revLater = all.find(a => a[0] === I.revLater);
out.day1.newCount = new Set(all.filter(a => I.new.includes(a[0])).map(a => a[0])).size;
// undo prefers the undone card
answers = [{cid: I.new[2], rating: 3, ts: T0}];
out.prefer = ses(T0 + 1000, I.new[2]).cid;
// mixing new cards and reviews
out.mix = F.mix(['r1', 'r2', 'r3', 'r4'], ['n1', 'n2']);
// the queue in (fake) IndexedDB
(async () => {
  F._setUser('offs');
  const a1 = await F.queueAnswer({cid: 1, did: 2, rating: 3, ms: 99999999, ts: 10});
  const a2 = await F.queueAnswer({cid: 5, did: 2, rating: 1, ms: -5, ts: 20});
  out.q1 = (await F.answers()).map(a => [a.cid, a.rating, a.ms, a.ts]);
  out.uids = a1.uid && a2.uid && a1.uid !== a2.uid;
  out.undone = (await F.undoLast()).cid;
  out.q2 = (await F.answers()).map(a => a.cid);
  await F.undoLast(); out.empty = await F.undoLast();
  console.log(JSON.stringify(out));
})().catch(e => { console.error(e); process.exit(1); });
""", fx, cpath)

f = out["first"]
assert f["counts"] == {"new": snap["counts"]["new"], "lrn": 1, "rev": snap["counts"]["rev"]}, (f["counts"], snap["counts"])
assert f["cid"] and f["labels"] == cards[f["cid"]]["labels"] and not f["approx"], f
seen_ids = [s[0] for s in out["seen"]]
today_ids = set(snap["queue"]["0"]["lrn"] + snap["queue"]["0"]["rev"] + snap["queue"]["0"]["new"])
assert set(seen_ids) == today_ids, (seen_ids, today_ids)
again = out["againCid"]
assert seen_ids.count(again) == 3, seen_ids     # Again → shown after 1m, Good → 10m step, Good → graduates
shows = [s for s in out["seen"] if s[0] == again]
assert shows[0][2] == cards[again]["labels"]
assert shows[1][2] == ["~1m", "~6m", "~10m", "~4d"], shows[1]       # first learning step again
assert shows[2][2] == ["~1m", "~10m", "~1d", "~4d"], shows[2]       # last step: Good graduates
assert shows[1][1] == "lrn" and out["end"]["cid"] == 0 and out["end"]["wait"] == 0
assert out["end"]["counts"] == {"new": 0, "lrn": 0, "rev": 0}
assert "waitMs" not in out   # learning cards due within 20 minutes were shown early instead of waiting
ok("offline session: today's cards within the limits, Again comes back after its step, Good steps on and graduates, "
   "labels marked ~")
assert out["ahead"]["cid"] == new_ids[0] and out["noAhead"]["cid"] == 0 and 0 < out["noAhead"]["wait"] <= 60000
assert out["noAhead"]["counts"]["lrn"] == 1
ok("learn ahead: a learning card due within 20 minutes is shown when nothing else is left (like Anki); "
   "without it the page waits for the card")
assert out["day1"]["today"] == 0 and out["day1"]["day"] == 1
assert out["day1"]["revLater"] and out["day1"]["revLater"][1] is True and all(
    l.startswith("~") for l in out["day1"]["revLater"][2] if l), out["day1"]["revLater"]
both_days = {i for d in ("0", "1") for g in ("lrn", "rev", "new") for i in snap["queue"][d][g]}
assert set(out["day1"]["ids"]) == both_days and snap["queue"]["1"]["rev"] == [rev_later], (out["day1"], snap["queue"])
ok("the next day: yesterday's and today's cards from the copy, with approximate (~) labels")
assert out["prefer"] == new_ids[2]
assert out["mix"] == ["r1", "n1", "r2", "r3", "n2", "r4"], out["mix"]
ok("undo shows the undone card again; new cards are spread between reviews")
assert out["q1"] == [[1, 3, 3600000, 10], [5, 1, 0, 20]] and out["uids"], out["q1"]
assert out["undone"] == 5 and out["q2"] == [1] and out["empty"] is None
ok("answer queue: card, rating, time taken (capped) and time answered, unique ids; undo removes the last answer")

# ---------- learning steps: the device's arithmetic against the engine ----------
# Each sequence of answers is played in the engine; after each answer, the engine's next states are
# compared with what the offline page would work out (due offset and steps left).
cases = []
with webapp.collection(USER) as col:
    for deck_name, seq, setup in [("L1", [1, 2, 3, 3], None), ("L2", [1, 1, 2, 3, 3], None),
                                  ("L3", [3, 2, 2, 1, 3, 3], None), ("L4", [2, 3, 3], "three"),
                                  ("L5", [1, 2, 3], "review"), ("L6", [1, 1, 3], "one")]:
        d = col.decks.id(deck_name)
        if setup:
            cf = col.decks.add_config_returning_id(deck_name)
            conf_d = col.decks.get_config(cf)
            if setup == "three":
                conf_d["new"]["delays"] = [2.0, 15.0, 120.0]
            if setup == "one":
                conf_d["new"]["delays"] = [30.0]
            if setup == "review":
                conf_d["lapse"]["delays"] = [5.0, 30.0]
            col.decks.update_config(conf_d)
            dd = col.decks.get(d); dd["conf"] = cf; col.decks.save(dd)
        n = col.new_note(basic); n["Front"] = deck_name; col.add_note(n, d)
        cid = n.card_ids()[0]
        if setup == "review":
            col.sched.set_due_date([cid], "0!")
        conf_info = offline.conf_info(col, col.decks.config_dict_for_deck_id(d))
        steps = []
        for i, rating in enumerate(seq):
            states = col._backend.get_scheduling_states(cid)
            nxt, rl = offline.next_states(col, states)
            card = col.get_card(cid)
            steps.append({"next": nxt, "rating": rating, "type": card.type})
            card.start_timer()
            col.sched.answer_card(col.sched.build_answer(card=card, states=states, rating=RATINGS[rating]))
            if nxt[rating - 1]["k"] == "r":
                break
        cases.append({"conf": conf_info, "steps": steps})
lout = run(r"""
const fs = require('fs'); const F = require(process.argv[3]);
const cases = JSON.parse(fs.readFileSync(process.argv[2], 'utf8')), out = [];
for (const cs of cases) {
  let st = null; const res = [];
  for (const s of cs.steps) {
    // what the device predicts for each button, from the state it worked out (after the first answer)
    if (st && !st.done) res.push([0, 1, 2, 3].map(i => F.nextLearn(st, i + 1, cs.conf, 0)));
    else res.push(null);
    st = st ? F.nextLearn(st, s.rating, cs.conf, 0) : F.firstAnswer({next: s.next, type: s.type}, s.rating, 0);
  }
  out.push(res);
}
console.log(JSON.stringify(out));
""", (lambda p: (json.dump(cases, open(p, "w")), p)[1])(os.path.join(tmp, "cases.json")), cpath)
checked = 0
for cs, pred in zip(cases, lout):
    for s, p in zip(cs["steps"], pred):
        if p is None:
            continue
        for eng, mine in zip(s["next"], p):
            if eng["k"] == "r":
                assert mine.get("done"), (cs["conf"]["learn"], s, p)
            else:
                assert not mine.get("done") and mine["due"] == eng["secs"] * 1000 and mine["rem"] == eng["rem"], (eng, mine, s)
            checked += 1
assert checked >= 40, checked
ok(f"learning and relearning steps worked out on the device match the engine ({checked} button outcomes)")

# ---------- typed answers: compared exactly like the engine ----------
rnd = random.Random(7)
pairs = [("hello world", "helo wrld", True), ("abc", "xbcd", True), ("café", "cafe", True), ("café", "cafe", False),
         ("café", "cafx", False), ("<b>x</b> &nbsp;y<div>z</div>", "x y z", True), ("a<br>b", "ab", True),
         ("  hi  ", "hi", True), ("hi", "  hi ", True), ("x &lt; y", "x < y", True), ("[sound:a.mp3]ab", "ab", True),
         ("abc", "ABC", True), ("a b", "a  b", True), ("日本語", "日本", True), ("ab", "", True), ("ab", "a\nb", True),
         ("Paris & co", "paris and co", True), ("café", "café", True), ("é", "é", False)]
for _ in range(150):
    e = "".join(rnd.choice("abcab é<>&") for _ in range(rnd.randint(1, 12)))
    t_ = "".join(rnd.choice("abcab é<>&") for _ in range(rnd.randint(0, 12)))
    pairs.append((html.escape(e), t_, rnd.random() < 0.8))
with webapp.collection(USER) as col:
    expect = [col.compare_answer(e, t_, comb) for e, t_, comb in pairs]
    plain = [offline.typeans_plain(col, e, comb) for e, _t, comb in pairs]
tp = os.path.join(tmp, "typed.json")
json.dump([[p, t_, comb] for p, (_e, t_, comb) in zip(plain, pairs)], open(tp, "w"))
mine = run(r"""
const fs = require('fs'); const F = require(process.argv[3]);
console.log(JSON.stringify(JSON.parse(fs.readFileSync(process.argv[2], 'utf8')).map(([e, t, c]) => F.compareAnswer(e, t, c))));
""", tp, cpath)
bad = [(pairs[i], expect[i], mine[i]) for i in range(len(pairs)) if expect[i] != mine[i]]
assert not bad, "\n".join(ascii(b) for b in bad[:5]) + f"\n{len(bad)} of {len(pairs)} differ"
ok(f"typed answers are compared on the device exactly like the engine ({len(pairs)} cases)")

print("ALL OFFLINE STUDY TESTS PASSED")
