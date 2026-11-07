// Flashcards: offline copy of decks (part 1, download). Loaded on every page for a logged-in person
// (flashcards/offline.py). Keeps, per person, an IndexedDB copy of the decks switched on for offline
// study ("fc-offline:<user>": decks, cards, meta) and their pictures/sounds in Cache Storage
// ("fc-media:<user>"). Refreshed in the background on page loads (so also after each answer) while
// online. The deck overview and Preferences show its state. Nothing here changes the collection.
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
    const r = indexedDB.open('fc-offline:' + USER, 1);
    r.onupgradeneeded = () => {
      const d = r.result;
      if (!d.objectStoreNames.contains('decks')) d.createObjectStore('decks', {keyPath: 'did'});
      if (!d.objectStoreNames.contains('cards')) d.createObjectStore('cards', {keyPath: 'id'});
      if (!d.objectStoreNames.contains('meta')) d.createObjectStore('meta');
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

F.refresh = refresh; F.render = render;
F._setUser = u => { USER = u; db = null; };   // for tests (the page sets it from the script tag)
if (typeof window !== 'undefined') window.FCOffline = F;
if (typeof module !== 'undefined') module.exports = F;
if (typeof document === 'undefined' || !document.currentScript) return;
USER = document.currentScript.dataset.user || '';
function start() {
  render();
  setTimeout(() => refresh(false), 1500);       // let the page's own requests go first
  setInterval(render, 30000);                   // keeps "updated 2 min ago" current
  window.addEventListener('online', () => refresh(false));
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(false); });
}
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
