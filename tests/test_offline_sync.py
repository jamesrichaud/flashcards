"""Offline study, part 3: what was done offline goes back to the server. POST /offline/sync replays
queued answers in the order they were given through the real scheduler (time taken and time
answered kept), exactly once per queued id, skipping cards changed elsewhere; notes added offline
are added. Then the browser side in node: the payload, marking answers sent/synced, undo, the
session ignoring answers the refreshed copy already has, the Add queue, and the offline page."""
import json, os, random, re, shutil, subprocess, sys, tempfile, time
os.environ["DATA_DIR"] = tempfile.mkdtemp()
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from fastapi.testclient import TestClient
import app as webapp
from anki.consts import QUEUE_TYPE_LRN, CARD_TYPE_REV
from anki.scheduler.v3 import CardAnswer
from flashcards import offline_sync

ok = lambda m: print("PASS", m)
c = TestClient(webapp.app)

# ---------- needs a login, refuses junk ----------
assert c.post("/offline/sync", json={"answers": []}).status_code == 401
c.post("/login", data={"username": "osync", "password": "secret123"})
USER = "osync"
assert c.post("/offline/sync", content=b"not json", headers={"Content-Type": "application/json"}).status_code == 400
assert c.post("/offline/sync", json=[1, 2]).status_code == 400
assert c.post("/offline/sync", json={"answers": [{}] * 5001}).status_code == 413
r = c.post("/offline/sync", json={"answers": [{"uid": "x1", "cid": "nope", "rating": 9}], "adds": "junk"})
assert r.status_code == 200 and r.json()["results"] == {"x1": "invalid"} and r.json()["skipped"] == 1
ok("sync endpoint: login needed, bad bodies refused, too many items refused, junk items marked invalid")

# ---------- a deck studied offline ----------
with webapp.collection(USER) as col:
    did = col.decks.id("Spanish")
    basic = col.models.by_name("Basic")
    def add(front, deck=did):
        n = col.new_note(basic); n["Front"] = front; n["Back"] = "x"; col.add_note(n, deck)
        return n.card_ids()[0]
    A, B, C, D, E, G = (add(f"card {i}") for i in range(6))
    mods = {cid: col.get_card(cid).mod for cid in (A, B, C, D, E, G)}
c.post(f"/offline/deck/{did}", data={"on": "1"})
snap = c.post(f"/offline/deck/{did}/snapshot", json={}).json()
smod = {x["id"]: x["mod"] for x in snap["cards"]}
sreps = {x["id"]: x["reps"] for x in snap["cards"]}
assert all(v == 0 for v in sreps.values())
assert all(smod[k] == v for k, v in mods.items())

now_ms = int(time.time() * 1000)
T0 = now_ms - 2 * 3600 * 1000     # studied offline two hours ago
def ans(uid, cid, rating, ts, ms=4000, mod=None, reps=None):
    return {"uid": uid, "cid": cid, "did": did, "rating": rating, "ms": ms, "ts": ts,
            "mod": smod[cid] if mod is None else mod, "reps": sreps.get(cid, 0) if reps is None else reps}
batch = [ans("u1", A, 1, T0, 5000), ans("u2", B, 3, T0 + 10_000, 2500), ans("u3", A, 3, T0 + 75_000, 3000),
         ans("u4", A, 3, T0 + 700_000, 1500), ans("u5", C, 4, T0 + 720_000, 999_999_999)]
shuffled = batch[:]
random.Random(3).shuffle(shuffled)
r = c.post("/offline/sync", json={"answers": shuffled, "device": "dev1"}).json()
assert r["results"] == {"u1": "ok", "u2": "ok", "u3": "ok", "u4": "ok", "u5": "ok"}, r
assert r["answered"] == 5 and r["skipped"] == 0 and r["message"] == "5 offline answers were saved.", r
with webapp.collection(USER) as col:
    rows = col.db.all("select id, cid, ease, time from revlog order by id")
    assert rows == [[T0, A, 1, 5000], [T0 + 10_000, B, 3, 2500], [T0 + 75_000, A, 3, 3000],
                    [T0 + 700_000, A, 3, 1500], [T0 + 720_000, C, 4, 60_000]], rows   # capped like the study screen (preset's max answer time)
    a = col.get_card(A); b = col.get_card(B); cc = col.get_card(C)
    assert a.type == CARD_TYPE_REV and a.ivl >= 1, (a.type, a.ivl)    # Again, Good (10m), Good: graduated
    assert b.queue == QUEUE_TYPE_LRN                                     # Good once: on the 10m step
    assert cc.type == CARD_TYPE_REV and cc.ivl >= 3                      # Easy
    assert col.get_card(D).type == 0
ok("answers replayed in the order given (not the order sent), with their own time and time taken (capped); "
   "Again then Good, Good graduates a learning card like on the study screen")

# ---------- exactly once ----------
r2 = c.post("/offline/sync", json={"answers": batch, "device": "dev1"}).json()
assert r2["results"] == r["results"] and r2["answered"] == 0 and r2["repeated"] == 5 and r2["message"] == "", r2
with webapp.collection(USER) as col:
    assert col.db.scalar("select count() from revlog") == 5
store = json.load(open(offline_sync.store_path(USER)))
assert set(store["done"]) >= {"u1", "u2", "u3", "u4", "u5"} and store["done"]["u1"][0] == "ok"
ok("a retry with the same ids answers nothing twice and returns the earlier results")

# Another device that studied the same copy offline answered B too: B was answered elsewhere.
r = c.post("/offline/sync", json={"answers": [ans("o1", B, 3, T0 + 790_000)], "device": "dev2"}).json()
assert r["results"] == {"o1": "changed"}, r
# An answer queued while the first sync was on its way: the device still had B's old mod, but our own
# replay of this device's answers changed it; that is not "another device".
r = c.post("/offline/sync", json={"answers": [ans("u6", B, 3, T0 + 800_000)], "device": "dev1"}).json()
assert r["results"] == {"u6": "ok"}, r
with webapp.collection(USER) as col:
    assert col.get_card(B).type == CARD_TYPE_REV
ok("a later answer from the same device to a card it already synced is accepted (the change was its own); "
   "the same card answered offline on another device is skipped")

# ---------- conflicts: changed or answered elsewhere, deleted ----------
with webapp.collection(USER) as col:
    dcard = col.get_card(D)
    webapp.select_deck(col, did)
    time.sleep(1.1)   # modification times are whole seconds; a real change elsewhere comes later than the copy
    col.sched.set_due_date([D], "0!")    # changed on another device (new mod)
    dcard.load()
    col.remove_notes([col.get_card(G).nid])                          # deleted on another device
with webapp.collection(USER) as col:
    before = col.db.scalar("select count() from revlog where ease > 0")
r = c.post("/offline/sync", json={"answers": [ans("c1", D, 1, T0 + 900_000), ans("c2", D, 3, T0 + 960_000),
                                              ans("c3", E, 3, T0 + 970_000), ans("c4", G, 3, T0 + 980_000)]}).json()
assert r["results"] == {"c1": "changed", "c2": "changed", "c3": "ok", "c4": "gone"}, r
assert r["skipped"] == 3 and r["answered"] == 1
assert r["message"] == ("1 offline answer was saved. 3 answers were skipped because the cards changed on another "
                        "device."), r["message"]
with webapp.collection(USER) as col:
    assert col.db.scalar("select count() from revlog where ease > 0") == before + 1
    assert col.db.scalar("select count() from revlog where cid = ? and ease > 0", D) == 0   # only set due date's entry
log = json.load(open(offline_sync.store_path(USER)))["log"]
assert [x["why"] for x in log[-3:]] == ["changed", "changed", "gone"] and log[-1]["cid"] == G
ok("cards changed or deleted elsewhere are skipped (all their offline answers), logged, and the person is told")

# A time in the future (a wrong device clock) is taken as now.
with webapp.collection(USER) as col:
    F_ = add("future"); fmod = col.get_card(F_).mod
r = c.post("/offline/sync", json={"answers": [ans("f1", F_, 3, now_ms + 10 ** 9, mod=fmod)]}).json()
assert r["results"] == {"f1": "ok"}
with webapp.collection(USER) as col:
    rid = col.db.scalar("select id from revlog where cid = ?", F_)
    assert rid <= int(time.time() * 1000) + 1000, rid
ok("a time answered in the future is taken as now")

# Leeches: the engine's leech handling runs, and the person hears about it.
with webapp.collection(USER) as col:
    Lc = add("leech")
    conf = col.decks.config_dict_for_deck_id(did); conf["lapse"]["leechFails"] = 1; col.decks.update_config(conf)
    col.sched.set_due_date([Lc], "0!")
    col.db.execute("update cards set lapses = 0 where id = ?", Lc)
    lmod = col.get_card(Lc).mod
r = c.post("/offline/sync", json={"answers": [ans("l1", Lc, 1, T0 + 990_000, mod=lmod)]}).json()
assert r["results"] == {"l1": "leech"} and r["leeches"] == 1 and "Card was a leech." in r["message"], r
with webapp.collection(USER) as col:
    assert "leech" in col.get_card(Lc).note().tags
ok("leeches are handled by the engine during the replay and reported")

# Siblings buried by the replay are not "changed elsewhere".
with webapp.collection(USER) as col:
    rev_nt = col.models.by_name("Basic (and reversed card)")
    n = col.new_note(rev_nt); n["Front"] = "sib"; n["Back"] = "ling"; col.add_note(n, did)
    s1, s2 = n.card_ids()
    conf = col.decks.config_dict_for_deck_id(did); conf["new"]["bury"] = True; col.decks.update_config(conf)
    m1, m2 = col.get_card(s1).mod, col.get_card(s2).mod
time.sleep(1.1)   # so the bury's new mod differs from the old one
r = c.post("/offline/sync", json={"answers": [ans("s1", s1, 4, T0 + 1_000_000, mod=m1),
                                              ans("s2", s2, 4, T0 + 1_010_000, mod=m2)], "device": "dev1"}).json()
assert r["results"] == {"s1": "ok", "s2": "ok"}, r
assert offline_sync.load_store(USER)["cmod"][str(s2)][0] == "dev1"   # the bury was recorded as this device's
ok("a sibling buried by an earlier replayed answer is still answered")

# ---------- notes added offline ----------
with webapp.collection(USER) as col:
    cloze = col.models.by_name("Cloze")
    gone_nt = col.models.copy(basic); gone_id = gone_nt["id"]
    other = col.decks.id("Gone deck")
    nb = col.note_count()
adds = [{"uid": "n1", "ntid": basic["id"], "did": did, "fields": ["<b>bold?</b> & co\nline 2", "back"], "tags": "offline  verbs",
         "ts": T0 + 5},
        {"uid": "n2", "ntid": cloze["id"], "did": did, "fields": ["{{c1::Madrid}} is the capital", ""], "tags": "", "ts": T0 + 6},
        {"uid": "n3", "ntid": cloze["id"], "did": did, "fields": ["no cloze here", ""], "ts": T0 + 7},
        {"uid": "n3b", "ntid": basic["id"], "did": did, "fields": ["", "empty front"], "ts": T0 + 7},
        {"uid": "n4", "ntid": gone_id, "did": did, "fields": ["x", "y"], "ts": T0 + 8},
        {"uid": "n5", "ntid": basic["id"], "did": other, "fields": ["deck went away", ""], "ts": T0 + 9}]
with webapp.collection(USER) as col:
    col.models.remove(gone_id)
    col.decks.remove([other])
r = c.post("/offline/sync", json={"adds": adds}).json()
assert r["results"] == {"n1": "ok", "n2": "ok", "n3": "invalid", "n3b": "invalid", "n4": "gone", "n5": "ok"}, r
assert r["added"] == 3 and r["failed"] == 3
assert r["message"] == ("3 notes added offline were saved. 3 notes added offline couldn't be saved (their note type "
                        "was deleted, or they had no cards)."), r["message"]
with webapp.collection(USER) as col:
    assert col.note_count() == nb + 3
    n1 = col.get_note(col.find_notes('tag:offline')[0])
    assert n1["Front"] == "&lt;b&gt;bold?&lt;/b&gt; &amp; co<br>line 2" and sorted(n1.tags) == ["offline", "verbs"], n1.fields
    assert col.get_card(n1.card_ids()[0]).did == did
    n2 = col.get_note(col.find_notes('"Text:*Madrid*"')[0])
    assert n2["Text"] == "{{c1::Madrid}} is the capital" and len(n2.card_ids()) == 1
    n5 = col.get_note(col.find_notes('"Front:deck went away"')[0])
    assert col.get_card(n5.card_ids()[0]).did == 1
r = c.post("/offline/sync", json={"adds": adds}).json()
assert r["added"] == 0 and r["repeated"] == 6
with webapp.collection(USER) as col:
    assert col.note_count() == nb + 3
ok("notes added offline: text escaped, line breaks kept, tags, cloze; deleted deck → Default; no cloze or deleted "
   "note type refused; never added twice")

# Answers and adds together, in time order.
with webapp.collection(USER) as col:
    H = add("mixed"); hmod = col.get_card(H).mod
r = c.post("/offline/sync", json={"answers": [ans("m1", H, 3, T0 + 2_000_000, mod=hmod)],
                                  "adds": [{"uid": "m2", "ntid": basic["id"], "did": did, "fields": ["mixed add", ""],
                                            "ts": T0 + 1_999_000}]}).json()
assert r["results"] == {"m1": "ok", "m2": "ok"} and r["answered"] == 1 and r["added"] == 1
ok("answers and added notes in one sync")

# ---------- the remembered ids are kept within limits ----------
s = offline_sync.load_store(USER)
s["done"]["old"] = ["ok", int(time.time()) - offline_sync.KEEP_SECS - 10]
offline_sync.save_store(USER, s)
assert "old" not in offline_sync.load_store(USER)["done"] and "u1" in offline_sync.load_store(USER)["done"]
ok("handled ids are remembered for 120 days")

# ---------- the offline page: Add screen and note types ----------
page = c.get("/offline/study").text
cfg = json.loads(re.search(r"window\.FCO=(\{.*?\});</script>", page).group(1))
names = [x["name"] for x in cfg["addTypes"]]
assert names[0] == "Basic" and "Cloze" in names and "Image Occlusion" not in names, names
assert next(x for x in cfg["addTypes"] if x["name"] == "Cloze") == {"id": cloze["id"], "name": "Cloze", "cloze": True,
                                                                       "fields": ["Text", "Back Extra"]}
assert 'id="fco-add"' in page and 'id="fco-add-btn"' in page and 'data-nav="/add"' in page
assert cfg["msgs"]["addsWaiting"]["forms"] == ["{n} note waiting to sync", "{n} notes waiting to sync"]
r = c.get("/offline/study", headers={"Accept-Language": "pl"})
ok("offline study page: an Add screen with the person's note types (no image occlusion)")

# ---------- browser side in node ----------
node = None
try:
    from playwright._impl._driver import compute_driver_executable
    node = compute_driver_executable()[0]
except Exception:
    node = shutil.which("node")
if not (node and os.path.exists(node)):
    print("SKIP browser parts in node (no node found)")
    print("ALL OFFLINE SYNC TESTS PASSED")
    sys.exit(0)
tmp = tempfile.mkdtemp()
cpath = os.path.join(tmp, "client.js")
open(cpath, "w").write(c.get("/offline/client.js").text)
for i, sc in enumerate(re.findall(r"<script>(.*?)</script>", page, re.S)):
    sp = os.path.join(tmp, f"inline{i}.js"); open(sp, "w").write(sc)
    p = subprocess.run([node, "--check", sp], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
assert subprocess.run([node, "--check", cpath], capture_output=True).returncode == 0
ok("the offline page's scripts and the offline script are valid JavaScript")

FAKE_IDB = r"""
function later(f) { setTimeout(f, 0); }
const DBS = {};
function makeTx(dbo) {
  const tx = {pending: 0, done: false};
  const check = () => later(() => { if (!tx.done && tx.pending === 0) { tx.done = true; tx.oncomplete && tx.oncomplete(); } });
  tx.req = fn => { const r = {}; tx.pending++; later(() => { try { r.result = fn(); r.onsuccess && r.onsuccess(); }
    catch (e) { r.error = e; r.onerror && r.onerror(); } tx.pending--; check(); }); return r; };
  tx.objectStore = n => { const s = dbo.stores[n]; if (!s) throw new Error('no store ' + n); return {
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
json.dump({"snap": snap}, open(fx, "w"))
out_path = os.path.join(tmp, "out.json")
p = subprocess.run([node, "-e", FAKE_IDB + r"""
const fs = require('fs');
const S = JSON.parse(fs.readFileSync(process.argv[1], 'utf8')).snap;
const F = require(process.argv[2]);
const out = {};
let SERVER_UP = false, LOGGED_IN = true, LOST_REPLY = false; const sent = [];
global.fetch = async (url, o) => {
  if (!SERVER_UP) throw new TypeError('Failed to fetch');
  if (url === '/offline/state') return {ok: LOGGED_IN, status: LOGGED_IN ? 200 : 401, json: async () => ({})};
  if (url === '/offline/sync') {
    const body = JSON.parse(o.body); sent.push(body);
    if (LOST_REPLY) throw new TypeError('connection reset');
    const results = {};
    for (const x of body.answers.concat(body.adds)) results[x.uid] = 'ok';
    return {ok: true, json: async () => ({results, answered: body.answers.length, added: body.adds.length, skipped: 0,
      message: body.answers.length + ' saved', stamp: 5})};
  }
  throw new Error('unexpected ' + url);
};
(async () => {
  F._setUser('osync');
  // a copy of the deck in the device's database, as the refresh stores it (made before studying)
  const deck = {did: S.did, name: S.name, cutoff: S.cutoff, today: S.today, queue: S.queue, cardIds: S.cards.map(c => c.id),
    confs: S.confs, learnAhead: S.learnAhead, asOf: Date.now() - 60000, counts: S.counts};
  const cards = new Map(S.cards.map(c => [c.id, c]));
  const first = F.session(deck, cards, [], Date.now()).cid;
  out.first = first;
  await F.waiting();   // opens the database; the copy's cards go in as the refresh puts them
  for (const c of S.cards) DBS['fc-offline:osync'].stores.cards.data.set(c.id, structuredClone(c));
  await F.queueAnswer({cid: first, did: S.did, rating: 1, ms: 1200, mod: cards.get(first).mod,
    reps: cards.get(first).reps});
  await F.queueAnswer({cid: S.cards[1].id, did: S.did, rating: 3, ms: 800});            // queued without a mod (part 2)
  await F.queueAdd({ntid: 7, did: S.did, fields: ['front', 'back'], tags: 'x'});
  out.waiting1 = await F.waiting();
  // offline: nothing is sent, nothing is marked
  out.offline = await F.syncBack();
  out.unsentAfterOffline = (await F.answers()).filter(a => a.sent).length;
  // the server is up but the person isn't logged in: nothing sent either, undo still possible
  SERVER_UP = true; LOGGED_IN = false;
  out.notLogged = await F.syncBack();
  out.sentCalls0 = sent.length;
  // online, logged in, but the reply is lost: the answers are marked sent (can't be undone), still waiting
  LOGGED_IN = true; LOST_REPLY = true;
  out.lost = await F.syncBack();
  out.afterLost = (await F.answers()).map(a => [!!a.sent, !!a.syncedAt]);
  out.undoAfterLost = await F.undoLast();
  out.waiting2 = await F.waiting();
  // the retry sends the same ids
  LOST_REPLY = false;
  const res = await F.syncBack();
  out.res = {answered: res.answered, added: res.added, message: res.message};
  out.sameIds = JSON.stringify(sent[0].answers.map(a => a.uid)) === JSON.stringify(sent[1].answers.map(a => a.uid))
    && sent[0].adds[0].uid === sent[1].adds[0].uid;
  out.payload = sent[1];
  out.waiting3 = await F.waiting();
  out.adds = (await F.adds()).length;
  // synced answers still count for the session until a newer copy arrives
  const answers = await F.answers();
  out.syncedAll = answers.every(a => a.syncedAt && a.result === 'ok');
  out.sesOld = F.session(deck, cards, answers, Date.now()).cid;
  const fresh = Object.assign({}, deck, {asOf: Date.now() + 1});
  out.sesNew = F.session(fresh, cards, answers, Date.now()).cid;
  // nothing left to send
  out.nothing = await F.syncBack();
  out.calls = sent.length;
  // after a refresh made after the sync, the synced answers are deleted
  await F._gcSynced(Date.now() + 1);
  out.left = (await F.answers()).length;
  // an answer queued after the sync is kept by the clean-up of older ones
  await F.queueAnswer({cid: first, did: S.did, rating: 3, ms: 1});
  await F._gcSynced(Date.now() + 1);
  out.keptUnsynced = (await F.answers()).length;
  // payload helper: the copy's mod for answers queued without one
  const pl = F.syncPayload([{uid: 'a', cid: S.cards[2].id, ts: 2, rating: 3}, {uid: 'b', cid: 1, ts: 1, rating: 1, mod: 9},
    {uid: 'c', cid: 1, ts: 0, syncedAt: 5}], [{uid: 'd', ntid: 1, did: 2, fields: ['x'], ts: 3}], cards);
  out.pl = pl;
  fs.writeFileSync(process.argv[3], JSON.stringify(out));
})().catch(e => { console.error(e); process.exit(1); });
""", fx, cpath, out_path], capture_output=True, text=True, timeout=120)
assert p.returncode == 0, p.stderr + p.stdout
out = json.load(open(out_path))
assert out["waiting1"] == {"answers": 2, "adds": 1}
assert out["offline"] is None and out["unsentAfterOffline"] == 0
assert out["notLogged"] is None and out["sentCalls0"] == 0
ok("no connection or no login: nothing is sent and the answers can still be undone")
assert out["lost"] is None and out["afterLost"] == [[True, False], [True, False]], out["afterLost"]
assert out["undoAfterLost"] is None and out["waiting2"] == {"answers": 2, "adds": 1}
assert out["sameIds"] and out["res"] == {"answered": 2, "added": 1, "message": "2 saved"}
assert out["waiting3"] == {"answers": 0, "adds": 0} and out["adds"] == 0 and out["syncedAll"]
ok("a lost reply: the answers can't be undone any more and the retry sends the same ids")
pa = out["payload"]["answers"]
assert pa[0]["cid"] == out["first"] and pa[0]["rating"] == 1 and pa[0]["ms"] == 1200 and pa[0]["mod"] == smod[out["first"]]
assert pa[0]["reps"] == 0 and pa[1]["mod"] == smod[snap["cards"][1]["id"]] and pa[1]["reps"] == 0, pa[1]   # part 2's get the copy's
assert out["payload"]["adds"][0]["fields"] == ["front", "back"] and out["payload"]["adds"][0]["tags"] == "x"
ok("payload: answers in time order with the card's mod from the copy; added notes with their fields and tags")
assert out["sesOld"] != out["first"] and out["sesNew"] == out["first"], out
assert out["nothing"] is None and out["calls"] == 2
assert out["left"] == 0 and out["keptUnsynced"] == 1
ok("synced answers keep shaping the offline session until a newer copy arrives, then they are deleted")
assert [a["uid"] for a in out["pl"]["answers"]] == ["b", "a"] and out["pl"]["answers"][1]["mod"] == snap["cards"][2]["mod"]
assert out["pl"]["adds"] == [{"uid": "d", "ntid": 1, "did": 2, "fields": ["x"], "tags": "", "ts": 3}]

# The payload the browser builds is accepted by the server as is. Its two cards were answered on the
# server since that copy was made (by the syncs above, from other devices), so they are skipped.
assert out["payload"]["device"] and len(out["payload"]["device"]) > 8
with webapp.collection(USER) as col:
    before = col.db.scalar("select count() from revlog")
body = out["payload"]
body["adds"][0]["ntid"] = basic["id"]
r = c.post("/offline/sync", json=body).json()
assert r["answered"] == 0 and r["added"] == 1 and r["skipped"] == 2, r
assert set(r["results"].values()) == {"changed", "ok"}
# With a copy that is up to date, the same browser payload is answered.
with webapp.collection(USER) as col:
    for a_ in body["answers"]:
        cd = col.get_card(a_["cid"]); a_["mod"] = cd.mod; a_["reps"] = cd.reps; a_["uid"] += "-fresh"
r = c.post("/offline/sync", json=body).json()
assert r["answered"] == 2 and r["repeated"] == 1, r
with webapp.collection(USER) as col:
    assert col.db.scalar("select count() from revlog") == before + 2
ok("the browser's payload is replayed by the server")

print("ALL OFFLINE SYNC TESTS PASSED")
