// Flashcards: offline copy of decks. Loaded on every page for a logged-in person (flashcards/offline.py)
// and by the offline study page. Part 1 (download): keeps, per person, an IndexedDB copy of the decks
// switched on for offline study ("fc-offline:<user>": decks, cards, meta) and their pictures/sounds in
// Cache Storage ("fc-media:<user>"); the offline study page and its scripts go in "fc-shell". Refreshed
// in the background on page loads (so also after each answer) while online. The deck overview and
// Preferences show its state. Nothing here changes the collection.
// Part 2 (studying offline): the answer queue ("answers" store) and the offline session: which card
// comes next, learning cards coming back after their step, button labels, typed-answer comparison.
(function () {
'use strict';
const F = {};
const MB = 1048576, THROTTLE = 60000, RETRY_MISSING = 300000;

// ---------- small pure helpers (tested in node: tests/test_offline.py) ----------
F.fmtSize = function (b) {
  b = Math.max(0, b || 0);
  if (b < MB) return Math.max(b ? 1 : 0, Math.round(b / 1024)) + ' KB';
  if (b < 10 * MB) return (b / MB).toFixed(1) + ' MB';
  if (b < 1024 * MB) return Math.round(b / MB) + ' MB';
  return (b / 1024 / MB).toFixed(1) + ' GB';
};
F.fill = function (tpl, vals) {
  return String(tpl).replace(/\{(\w+)\}/g, (m, k) => (k in vals ? String(vals[k]) : m));
};
F.plural = function (table, n) {
  const i = n < 200 ? n : 100 + (n % 100);
  return F.fill(table.forms[table.idx[i]] || table.forms[table.forms.length - 1], {n: n});
};
F.ago = function (then, now, lang) {
  const s = Math.max(0, (now - then) / 1000);
  let v, u;
  if (s < 45) { v = 0; u = 'second'; }
  else if (s < 3600) { v = Math.max(1, Math.round(s / 60)); u = 'minute'; }
  else if (s < 86400) { v = Math.round(s / 3600); u = 'hour'; }
  else { v = Math.round(s / 86400); u = 'day'; }
  try { return new Intl.RelativeTimeFormat(lang || 'en', {numeric: 'auto'}).format(-v, u); }
  catch (e) { return v ? ('-' + v + ' ' + u) : 'now'; }
};
// All decks' media, most needed first: today's cards before later days, decks in list order.
F.mergeMedia = function (decks) {
  const seen = new Map(); let i = 0;
  for (const d of decks) for (const m of (d.media || [])) {
    const old = seen.get(m.url);
    if (!old) seen.set(m.url, {url: m.url, size: m.size, day: m.day, i: i++});
    else if (m.day < old.day) old.day = m.day;
  }
  return Array.from(seen.values()).sort((a, b) => a.day - b.day || a.i - b.i);
};
// What to download and what to delete so the media stays under the cap.
F.planMedia = function (entries, cached, cap) {
  let used = 0, capHit = false; const keep = new Set(), fetch = [];
  for (const e of entries) {
    if (used + e.size <= cap) { used += e.size; keep.add(e.url); if (!cached.has(e.url)) fetch.push(e.url); }
    else capHit = true;
  }
  const drop = Array.from(cached).filter(u => !keep.has(u));
  return {keep: keep, fetch: fetch, drop: drop, capHit: capHit, bytes: used};
};
// true = refresh now, a number = refresh in that many ms (throttled), false = nothing to do.
F.needsRefresh = function (meta, st, now, force, localDids) {
  const want = st.decks.map(d => d.did);
  if (force || !meta) return true;
  if (want.some(d => !localDids.includes(d))) return true;             // a deck was switched on
  const wait = (meta.lastTry || 0) + THROTTLE - now;
  if (meta.version !== st.version || meta.error) return wait > 0 ? wait : true;
  if (meta.missing && now - (meta.lastTry || 0) > RETRY_MISSING) return true;
  return false;
};

// ---------- storage ----------
let USER = '', db = null, running = false, timer = 0;
const req = r => new Promise((ok, bad) => { r.onsuccess = () => ok(r.result); r.onerror = () => bad(r.error); });
const done = tx => new Promise((ok, bad) => { tx.oncomplete = () => ok(); tx.onerror = tx.onabort = () => bad(tx.error); });
F.supported = function () {
  return typeof indexedDB !== 'undefined' && typeof caches !== 'undefined' && !!window.isSecureContext;
};
function openDB() {
  if (db) return Promise.resolve(db);
  return new Promise((ok, bad) => {
    const r = indexedDB.open('fc-offline:' + USER, 2);
    r.onupgradeneeded = () => {
      const d = r.result;
      if (!d.objectStoreNames.contains('decks')) d.createObjectStore('decks', {keyPath: 'did'});
      if (!d.objectStoreNames.contains('cards')) d.createObjectStore('cards', {keyPath: 'id'});
      if (!d.objectStoreNames.contains('meta')) d.createObjectStore('meta');
      if (!d.objectStoreNames.contains('answers')) d.createObjectStore('answers', {keyPath: 'uid'});   // part 2
    };
    r.onsuccess = () => { db = r.result; db.onversionchange = () => { db.close(); db = null; }; ok(db); };
    r.onerror = () => bad(r.error);
  });
}
async function getAll(store) { const d = await openDB(); return req(d.transaction(store).objectStore(store).getAll()); }
async function getMeta() { const d = await openDB(); return req(d.transaction('meta').objectStore('meta').get('state')); }
async function putMeta(m) { const d = await openDB(); const tx = d.transaction('meta', 'readwrite');
  tx.objectStore('meta').put(m, 'state'); return done(tx); }
const cacheName = () => 'fc-media:' + USER;
F.deckInfo = async function (did) { const d = await openDB(); return req(d.transaction('decks').objectStore('decks').get(did)); };

// Delete cards no deck uses any more (and the decks given).
async function removeDecks(dids) {
  const d = await openDB();
  const tx = d.transaction(['decks', 'cards'], 'readwrite');
  for (const did of dids) tx.objectStore('decks').delete(did);
  await done(tx);
  await gcCards();
}
async function gcCards() {
  const decks = await getAll('decks'), used = new Set();
  for (const k of decks) for (const id of k.cardIds) used.add(id);
  const d = await openDB(), tx = d.transaction('cards', 'readwrite'), st = tx.objectStore('cards');
  const keys = await req(st.getAllKeys());
  for (const id of keys) if (!used.has(id)) st.delete(id);
  return done(tx);
}

// ---------- refresh ----------
async function refresh(force) {
  if (running || !F.supported() || !USER) return;
  if (typeof navigator !== 'undefined' && navigator.onLine === false) return;
  if (navigator.locks && navigator.locks.request) {   // one tab at a time; the others skip this round
    return navigator.locks.request('fc-offline:' + USER, {ifAvailable: true}, lock => (lock ? update(force) : null));
  }
  return update(force);
}
async function update(force) {
  if (running) return;
  running = true; clearTimeout(timer);
  let meta = null;
  try {
    const r = await fetch('/offline/state', {cache: 'no-store', credentials: 'same-origin'});
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const st = await r.json();
    meta = await getMeta();
    let local = await getAll('decks');
    const want = st.decks.map(d => d.did);
    const gone = local.filter(k => !want.includes(k.did)).map(k => k.did);
    if (gone.length) {   // switched off: delete its copy now, and media no other deck needs
      await removeDecks(gone); local = local.filter(k => want.includes(k.did));
      await syncMedia(local, st);
    }
    let todo = F.needsRefresh(meta, st, Date.now(), force, local.map(k => k.did));
    if (!st.decks.length && !local.length) {   // nothing offline: drop leftovers, don't keep a cache
      if (meta && (meta.mediaCount || meta.version !== st.version)) await syncMedia([], st);
      await putMeta({version: st.version, updated: Date.now(), lastTry: Date.now(), mediaCount: 0});
      todo = false;
    }
    if (typeof todo === 'number') { timer = setTimeout(() => refresh(false), todo + 200); todo = false; }
    if (!todo) return;
    meta = Object.assign({}, meta || {}, {lastTry: Date.now(), error: ''});
    await putMeta(meta);
    if (navigator.storage && navigator.storage.persist && navigator.storage.persisted) {
      try { if (!(await navigator.storage.persisted())) await navigator.storage.persist(); } catch (e) { /* ignore */ }
    }
    const stored = new Map((await getAll('cards')).map(c => [c.id, c]));
    for (const dk of st.decks) {
      running = 'deck:' + dk.did; render();
      const prev = local.find(k => k.did === dk.did), known = {};
      for (const [id, c] of stored) if (c.q) known[id] = c.rkey;   // also cards kept for another deck
      const res = await fetch('/offline/deck/' + dk.did + '/snapshot', {method: 'POST', credentials: 'same-origin',
        headers: {'Content-Type': 'application/json'}, body: JSON.stringify({known: known})});
      if (res.status === 404) continue;
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const snap = await res.json();
      const d = await openDB(), tx = d.transaction(['decks', 'cards'], 'readwrite'), cs = tx.objectStore('cards');
      let bytes = 0;
      for (const c of snap.cards) {
        if (!('q' in c)) {
          const old = stored.get(c.id);
          if (old) { c.q = old.q; c.a = old.a; c.av = old.av; c.typeans = old.typeans; c.marked = old.marked; }
        }
        bytes += (c.q || '').length + (c.a || '').length + 300;
        cs.put(c); stored.set(c.id, c);
      }
      tx.objectStore('decks').put({did: snap.did, name: snap.name, updated: Date.now(), today: snap.today,
        cutoff: snap.cutoff, days: snap.days, counts: snap.counts, queue: snap.queue, filtered: snap.filtered,
        cardIds: snap.cards.map(c => c.id), media: snap.media, confs: snap.confs, truncated: snap.truncated,
        generated: snap.generated, bytes: bytes, mediaBytes: prev ? prev.mediaBytes || 0 : 0});
      await done(tx);
    }
    await gcCards();
    await cacheShell(st);
    const m = await syncMedia(await getAll('decks'), st);
    await putMeta(Object.assign(meta, {version: st.version, updated: Date.now(), error: ''}, m));
  } catch (e) {
    try { await putMeta(Object.assign({}, meta || {}, {lastTry: Date.now(), error: String((e && e.message) || e)})); }
    catch (e2) { /* ignore */ }
  } finally {
    running = false; render();
  }
}

// Make Cache Storage hold the media of the offline decks, within the cap, most needed first.
async function syncMedia(decks, st) {
  const cache = await caches.open(cacheName());
  const cached = new Set((await cache.keys()).map(q => new URL(q.url).pathname));
  const plan = F.planMedia(F.mergeMedia(decks), cached, (st.media_mb || 250) * MB);
  for (const u of plan.drop) await cache.delete(u);
  let missing = 0, capHit = plan.capHit, i = 0;
  async function worker() {
    while (i < plan.fetch.length && !capHit) {
      const u = plan.fetch[i++];
      try {
        const r = await fetch(u, {credentials: 'same-origin'});
        if (r.ok) await cache.put(u, r); else missing++;
      } catch (e) {
        if (e && e.name === 'QuotaExceededError') capHit = true; else missing++;
      }
    }
  }
  await Promise.all([worker(), worker(), worker()]);
  const have = new Set((await cache.keys()).map(q => new URL(q.url).pathname));
  const d = await openDB(), tx = d.transaction('decks', 'readwrite'), ds = tx.objectStore('decks');
  for (const k of decks) {
    k.mediaBytes = (k.media || []).reduce((s, m) => s + (have.has(m.url) ? m.size : 0), 0);
    ds.put(k);
  }
  await done(tx);
  return {capHit: capHit, missing: missing, mediaCount: have.size, mediaBytes: plan.bytes};
}

// The offline study page and what it loads, so the service worker can show it without a connection.
// The page itself (it carries the person's language and settings) and this script are fetched again
// on each refresh; the bundled MathJax files only when missing.
async function cacheShell(st) {
  try {
    if (typeof caches === 'undefined' || !caches.open) return;
    const c = await caches.open('fc-shell');
    if (!c.match || !c.put) return;
    for (const u of ['/offline/study', '/offline/client.js']) {
      const r = await fetch(u, {credentials: 'same-origin', cache: 'no-store'});
      if (r.ok) await c.put(u, r);
    }
    for (const u of (st.assets || [])) {
      if (await c.match(u)) continue;
      const r = await fetch(u, {credentials: 'same-origin'});
      if (r.ok) await c.put(u, r);
    }
  } catch (e) { /* the copy itself is still fine; tried again next refresh */ }
}

// ---------- status on the deck overview and in Preferences ----------
const lang = () => (document.documentElement.lang || 'en');
const setText = (id, s, warn) => { const e = document.getElementById(id); if (e) { e.textContent = s;
  e.classList.toggle('fc-off-warn', !!warn); } };
async function render() {
  if (typeof document === 'undefined') return;
  const box = document.getElementById('fc-off'), pref = document.getElementById('fc-off-prefs');
  if (!box && !pref) return;
  const M = JSON.parse((box || pref).dataset.msgs || '{}');
  if (!F.supported()) { setText(box ? 'fc-off-status' : 'fc-off-list', M.unsupported); return; }
  let meta, decks;
  try { meta = (await getMeta()) || {}; decks = await getAll('decks'); } catch (e) { return; }
  const now = Date.now();
  const line = k => F.fill(M.ready, {ago: F.ago(k.updated, now, lang()), cards: F.plural(M.cards, k.cardIds.length),
    size: F.fmtSize((k.bytes || 0) + (k.mediaBytes || 0))});
  const extra = [];
  if (meta.capHit) extra.push(M.cap);
  if (meta.missing) extra.push(M.missing);
  if (meta.error) extra.push(F.fill(M.error, {error: meta.error}));
  if (box) {
    const did = +box.dataset.did, on = box.dataset.on === '1', k = decks.find(x => x.did === did);
    let s = '';
    if (running === 'deck:' + did || (on && !k && running)) s = M.working;
    else if (k) s = line(k) + (k.truncated ? ' ' + M.truncated : '');
    else if (on) s = M.waiting;
    setText('fc-off-status', [s].concat(on || k ? extra : []).filter(Boolean).join(' '), on && extra.length);
    const rm = document.getElementById('fc-off-remove'); if (rm) rm.style.display = (k || on) ? '' : 'none';
  }
  if (pref) {
    const list = document.getElementById('fc-off-list');
    if (list) {
      list.textContent = '';
      if (!decks.length) list.textContent = M.none;
      for (const k of decks.sort((a, b) => a.name.localeCompare(b.name))) {
        const p = document.createElement('div');
        p.textContent = F.fill(M.deck, {name: k.name, cards: F.plural(M.cards, k.cardIds.length),
          size: F.fmtSize((k.bytes || 0) + (k.mediaBytes || 0)), ago: F.ago(k.updated, now, lang())});
        list.appendChild(p);
      }
      for (const x of extra) { const p = document.createElement('div'); p.className = 'fc-off-warn'; p.textContent = x;
        list.appendChild(p); }
    }
  }
  if (navigator.storage && navigator.storage.estimate) {
    try {
      const est = await navigator.storage.estimate();
      let s = F.fill(M.storage, {used: F.fmtSize(est.usage), quota: F.fmtSize(est.quota)});
      if (navigator.storage.persisted && decks.length) s += ' ' + ((await navigator.storage.persisted()) ? M.persisted : M.notpersisted);
      setText('fc-off-storage', s);
    } catch (e) { /* ignore */ }
  }
}

// ---------- part 2: studying offline (tested in node: tests/test_offline_study.py) ----------
// The session is worked out again from the copy and the queued answers each time (nothing else is
// stored), so undo is just deleting the last queued answer.
const DAY = 86400;
// Which day of the copy it is: 0 = the day it was downloaded, 1 = the next day (from the engine's
// day cutoff), ...
F.dayIndex = function (deck, nowMs) {
  const s = nowMs / 1000;
  return s < deck.cutoff ? 0 : Math.floor((s - deck.cutoff) / DAY) + 1;
};
// Anki's default "mix new cards and reviews": new cards spread evenly between the reviews.
F.mix = function (rev, nw) {
  const R = rev.length, N = nw.length, all = [];
  rev.forEach((id, j) => all.push([(j + 1) / (R + 1), 0, j, id]));
  nw.forEach((id, i) => all.push([(i + 1) / (N + 1), 1, i, id]));
  all.sort((a, b) => a[0] - b[0] || a[1] - b[1] || a[2] - b[2]);
  return all.map(x => x[3]);
};
const stepSecs = mins => (mins || []).map(m => Math.round(m * 60));
// Hard on the first step: halfway to the second step, or 1.5 x a single step (at most a day more).
F.hardFirst = s => (s.length > 1 ? Math.floor((s[0] + s[1]) / 2) : Math.min(Math.floor(s[0] * 3 / 2), s[0] + DAY));
// A card's state after its first offline answer: the engine's own next state from the copy.
F.firstAnswer = function (card, rating, ts) {
  const n = (card.next || [])[rating - 1];
  if (!n) return rating === 1 ? {kind: card.type >= 2 ? 'relearn' : 'learn', rem: 1, due: ts + 60000} : {done: true};
  if (n.k === 'l' || n.k === 'rl') return {kind: n.k === 'rl' ? 'relearn' : 'learn', rem: n.rem, due: ts + n.secs * 1000};
  if (n.k === 'p') return {kind: 'preview', secs: n.secs, due: ts + n.secs * 1000};
  return {done: true};
};
// Learning steps worked out on the device (as the engine does it), for cards shown again offline.
// rem = steps left, counting the current one (the engine's remaining_steps).
F.nextLearn = function (st, rating, conf, ts) {
  if (st.kind === 'preview') return rating === 1 ? {kind: 'preview', secs: st.secs, due: ts + st.secs * 1000} : {done: true};
  const steps = stepSecs(st.kind === 'relearn' ? conf.relearn : conf.learn), n = steps.length;
  if (!n) return {done: true};
  const left = st.rem % 1000, idx = Math.min(n - 1, Math.max(0, n - left));
  if (rating === 1) return {kind: st.kind, rem: n, due: ts + steps[0] * 1000};
  if (rating === 2) return {kind: st.kind, rem: st.rem, due: ts + (idx === 0 ? F.hardFirst(steps) : steps[idx]) * 1000};
  if (rating === 3 && idx + 1 < n && left > 1) return {kind: st.kind, rem: n - idx - 1, due: ts + steps[idx + 1] * 1000};
  return {done: true};
};
// Button labels for a card shown again offline (approximate, so marked "~").
F.learnLabels = function (st, conf, card) {
  const L = conf.labels || {}, ap = x => (x ? '~' + x : '');
  if (st.kind === 'preview') return ['', '', '', ''];
  const rel = st.kind === 'relearn', list = (rel ? L.relearn : L.learn) || [], n = list.length;
  const left = st.rem % 1000, idx = Math.min(Math.max(0, n - 1), Math.max(0, n - left));
  const grad = rel ? (card.rl || [])[0] : L.grad, easy = rel ? (card.rl || [])[1] : L.easy;
  return [ap(list[0]), ap(idx === 0 ? (rel ? L.relearnHard : L.learnHard) : list[idx]),
    ap(idx + 1 < n && left > 1 ? list[idx + 1] : grad), ap(easy)];
};
// The offline session of one deck copy. cards: Map id -> card record (at least the answered cards and
// the learning cards; see F.neededIds). answers: the queued answers (any order). prefer: show this card
// if it can be shown (after undo). Returns {cid, kind, labels, approx, state, counts: {new, lrn, rev},
// wait (ms until the next learning card, when nothing can be shown yet), day}; cid 0 = nothing to show.
F.session = function (deck, cards, answers, now, prefer) {
  const k = F.dayIndex(deck, now), end = (deck.cutoff + DAY * k) * 1000;
  const ahead = (deck.learnAhead == null ? 1200 : deck.learnAhead) * 1000;
  const inDeck = new Set(deck.cardIds), st = new Map();
  for (const a of answers.slice().sort((x, y) => x.ts - y.ts)) {
    if (!inDeck.has(a.cid)) continue;
    const c = cards.get(a.cid); if (!c) continue;
    const prev = st.get(a.cid), conf = (deck.confs || {})[c.conf] || {};
    if (prev && prev.done) continue;
    st.set(a.cid, prev ? F.nextLearn(prev, a.rating, conf, a.ts) : F.firstAnswer(c, a.rating, a.ts));
  }
  const learning = [], rev = [], nw = [], isNew = new Set();
  const days = Object.keys(deck.queue || {}).map(Number).filter(d => d <= k).sort((a, b) => a - b);
  for (const d of days) {
    const g = deck.queue[String(d)];
    for (const id of g.lrn || []) {
      if (st.has(id)) continue;
      const c = cards.get(id);
      learning.push({id: id, due: c && (c.queue === 1 || c.queue === 4) ? c.due * 1000 : 0});
    }
    for (const id of g.rev || []) rev.push(id);
    for (const id of g.new || []) { nw.push(id); isNew.add(id); }
  }
  for (const [id, s] of st) if (!s.done) learning.push({id: id, due: s.due});
  const main = F.mix(rev, nw).filter(id => !st.has(id));
  const today = learning.filter(x => x.due < end).sort((a, b) => a.due - b.due);
  const counts = {new: main.filter(id => isNew.has(id)).length, lrn: today.length,
    rev: main.filter(id => !isNew.has(id)).length};
  const lrnIds = new Set(today.map(x => x.id));
  let cid = 0;
  if (prefer && (lrnIds.has(prefer) || main.includes(prefer))) cid = prefer;
  else if (today.length && today[0].due <= now) cid = today[0].id;
  else if (main.length) cid = main[0];
  else if (today.length && today[0].due <= now + ahead) cid = today[0].id;
  const out = {cid: cid, counts: counts, day: k, wait: 0, labels: null, approx: false, kind: '', state: null};
  if (!cid) { if (today.length) out.wait = Math.max(1000, today[0].due - now); return out; }
  out.kind = lrnIds.has(cid) ? 'lrn' : (isNew.has(cid) ? 'new' : 'rev');
  const c = cards.get(cid), s = st.get(cid);
  if (s && !s.done) {
    out.state = s; out.approx = true;
    out.labels = F.learnLabels(s, (deck.confs || {})[c.conf] || {}, c);
  } else if (c) {
    out.approx = !!c.approx || k > 0;
    out.labels = (c.labels || []).map(x => (x && out.approx ? '~' + x : x));
  }
  return out;
};
// The card records the session needs besides the deck: answered cards and the learning cards.
F.neededIds = function (deck, answers) {
  const ids = new Set(), inDeck = new Set(deck.cardIds);
  for (const a of answers) if (inDeck.has(a.cid)) ids.add(a.cid);
  for (const d of Object.keys(deck.queue || {})) for (const id of deck.queue[d].lrn || []) ids.add(id);
  return Array.from(ids);
};

// ---------- typed answers, compared like the engine (rslib typeanswer) ----------
const esc = s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
// Python difflib's SequenceMatcher (without the junk heuristic): the opcodes turning a into b.
F.opcodes = function (a, b) {
  const b2j = new Map();
  b.forEach((x, j) => { if (!b2j.has(x)) b2j.set(x, []); b2j.get(x).push(j); });
  function longest(alo, ahi, blo, bhi) {
    let bi = alo, bj = blo, bs = 0, j2len = new Map();
    for (let i = alo; i < ahi; i++) {
      const nj = new Map();
      for (const j of b2j.get(a[i]) || []) {
        if (j < blo) continue;
        if (j >= bhi) break;
        const kk = (j2len.get(j - 1) || 0) + 1; nj.set(j, kk);
        if (kk > bs) { bi = i - kk + 1; bj = j - kk + 1; bs = kk; }
      }
      j2len = nj;
    }
    return [bi, bj, bs];
  }
  const blocks = [], todo = [[0, a.length, 0, b.length]];
  while (todo.length) {
    const [alo, ahi, blo, bhi] = todo.pop(), [i, j, kk] = longest(alo, ahi, blo, bhi);
    if (kk) {
      blocks.push([i, j, kk]);
      if (alo < i && blo < j) todo.push([alo, i, blo, j]);
      if (i + kk < ahi && j + kk < bhi) todo.push([i + kk, ahi, j + kk, bhi]);
    }
  }
  blocks.sort((x, y) => x[0] - y[0] || x[1] - y[1]);
  const merged = [];
  for (const bl of blocks) {
    const l = merged[merged.length - 1];
    if (l && l[0] + l[2] === bl[0] && l[1] + l[2] === bl[1]) l[2] += bl[2]; else merged.push(bl.slice());
  }
  merged.push([a.length, b.length, 0]);
  const ops = []; let i = 0, j = 0;
  for (const [ai, bj, size] of merged) {
    const tag = i < ai && j < bj ? 'replace' : i < ai ? 'delete' : j < bj ? 'insert' : '';
    if (tag) ops.push([tag, i, ai, j, bj]);
    i = ai + size; j = bj + size;
    if (size) ops.push(['equal', ai, i, bj, j]);
  }
  return ops;
};
const noMarks = s => s.normalize('NFD').replace(/\p{M}/gu, '').normalize('NFC');
// expected: the engine's prepared text (card.tplain.text); typed: what the person typed.
F.compareAnswer = function (expected, typed, combining) {
  const original = String(expected || '');
  expected = original.normalize('NFC'); typed = String(typed || '').normalize('NFC');
  if (!typed) return '<code id=typeans>' + esc(expected) + '</code>';
  const ex = combining ? expected : noMarks(expected), ty = combining ? typed : noMarks(typed);
  // Without combining characters (nc:), the engine shows the expected text decomposed, each letter
  // with its accents.
  const disp = combining ? expected : expected.normalize('NFD');
  if (ex === ty) return '<code id=typeans><span class=typeGood>' + esc(combining ? expected : original) + '</span></code>';
  const A = Array.from(ty), B = Array.from(ex);
  const show = combining ? Array.from(expected) : (disp.match(/\P{M}\p{M}*|\p{M}+/gu) || []);
  const shown = (i, j) => (show.length === B.length ? show : B).slice(i, j).join('');
  const span = (cls, s) => '<span class=' + cls + '>' + esc(s) + '</span>';
  let top = '', bottom = '';
  for (const [tag, i1, i2, j1, j2] of F.opcodes(A, B)) {
    if (tag === 'equal') { top += span('typeGood', A.slice(i1, i2).join('')); bottom += span('typeGood', shown(j1, j2)); }
    else if (tag === 'delete') top += span('typeBad', A.slice(i1, i2).join(''));
    else if (tag === 'insert') { top += span('typeMissed', '-'.repeat(j2 - j1)); bottom += span('typeMissed', shown(j1, j2)); }
    else { top += span('typeBad', A.slice(i1, i2).join('')); bottom += span('typeMissed', shown(j1, j2)); }
  }
  return '<code id=typeans>' + top + '<br><span id=typearrow>&darr;</span><br>' + bottom + '</code>';
};

// ---------- the answer queue (sent to the server by part 3's sync) ----------
F.uid = function () {
  try { if (typeof crypto !== 'undefined' && crypto.randomUUID) return crypto.randomUUID(); } catch (e) { /* old Safari */ }
  return Date.now().toString(36) + '-' + Math.random().toString(36).slice(2) + Math.random().toString(36).slice(2);
};
F.answers = async function () { return (await getAll('answers')).sort((a, b) => a.ts - b.ts); };
// a: {cid, did, rating, ms, ts}. Each answer gets a unique id so the server can apply it exactly once.
F.queueAnswer = async function (a) {
  const rec = Object.assign({uid: F.uid(), ts: Date.now()}, a);
  rec.ms = Math.max(0, Math.min(3600000, Math.round(rec.ms || 0)));
  const d = await openDB(), tx = d.transaction('answers', 'readwrite');
  tx.objectStore('answers').put(rec);
  await done(tx);
  return rec;
};
// Undo: forget the last offline answer that hasn't been sent yet.
F.undoLast = async function () {
  const all = (await F.answers()).filter(a => !a.sent);
  const last = all[all.length - 1];
  if (!last) return null;
  const d = await openDB(), tx = d.transaction('answers', 'readwrite');
  tx.objectStore('answers').delete(last.uid);
  await done(tx);
  return last;
};
F.getCards = async function (ids) {
  const d = await openDB(), s = d.transaction('cards').objectStore('cards'), out = new Map();
  const got = await Promise.all(ids.map(id => req(s.get(id))));
  got.forEach((c, i) => { if (c) out.set(ids[i], c); });
  return out;
};
F.decks = () => getAll('decks');
F.meta = () => getMeta();
F.user = () => USER;

F.refresh = refresh; F.render = render;
F._setUser = u => { USER = u; db = null; };   // for tests (the page sets it from the script tag)
if (typeof window !== 'undefined') window.FCOffline = F;
if (typeof module !== 'undefined') module.exports = F;
if (typeof document === 'undefined' || !document.currentScript) return;
USER = document.currentScript.dataset.user || '';
// The offline study page opens the copy of whoever used this device last (it may be shown without a
// login, by the service worker); normal pages remember that person.
try {
  if (USER) localStorage.setItem('fc-offline-user', USER);
  else USER = localStorage.getItem('fc-offline-user') || '';
} catch (e) { /* private mode */ }
if (document.currentScript.dataset.page === 'study') return;   // the offline study page drives itself
function start() {
  render();
  setTimeout(() => refresh(false), 1500);       // let the page's own requests go first
  setInterval(render, 30000);                   // keeps "updated 2 min ago" current
  window.addEventListener('online', () => refresh(false));
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(false); });
}
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
