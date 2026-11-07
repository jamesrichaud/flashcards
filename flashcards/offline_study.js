// Flashcards: the offline study page (offline study, part 2). Inlined into /offline/study by
// flashcards/offline.py; the service worker shows that page when the server can't be reached. Runs
// only from the device's offline copy (window.FCOffline, flashcards/offline_client.js): a deck list
// with today's counts and the study screen. Answers go into the device's queue (sent to the server
// by part 3's sync); cards answered Again or still learning come back after their step, like Anki.
(function () {
'use strict';
const C = window.FCO || {}, M = C.msgs || {}, P = C.prefs || {}, F = window.FCOffline;
const $ = id => document.getElementById(id);
const fr = $('frame');
let DECK = null, CARD = null, SES = null, shown = false, sent = false, busy = false, t0 = 0, shownAt = 0;
let prefer = 0, waitTimer = 0, typed = '';

// ---------- small helpers ----------
const plural = (tb, n) => (F && tb ? F.plural(tb, n) : String(n));
function toast(m) {
  const t = $('toast'); if (!t || !m) return;
  t.textContent = m; t.style.opacity = 1; clearTimeout(t._h); t._h = setTimeout(() => { t.style.opacity = 0; }, 1800);
}
function flash(m) { const f = $('fco-flash'); f.textContent = m || ''; f.hidden = !m; }
function view(name) {
  for (const v of ['list', 'wait', 'study']) $('fco-' + v).hidden = v !== name;
  clearTimeout(waitTimer);
  if (name !== 'study') stopAudio();
}
function fmtWait(ms) {
  const mins = Math.max(1, Math.round(ms / 60000));
  return mins < 60 ? plural(M.minutes, mins) : plural(M.hours, Math.round(mins / 60));
}
async function banner() {
  const b = $('fco-banner'); let n = 0;
  try { n = (await F.answers()).filter(a => !a.sent).length; } catch (e) { /* no copy yet */ }
  const parts = [];
  if (navigator.onLine === false) parts.push(M.offline);
  if (n) parts.push(plural(M.waiting, n));
  b.textContent = parts.join(' · '); b.hidden = !parts.length;
  for (const u of document.querySelectorAll('.fco-undo')) u.hidden = !n;
}
window.addEventListener('online', banner);
window.addEventListener('offline', banner);

// ---------- the deck list ----------
async function showList(msg) {
  view('list'); flash(msg || '');
  const tbody = $('fco-decks'), empty = $('fco-empty');
  tbody.textContent = '';
  let decks = [], answers = [];
  try { decks = await F.decks(); answers = await F.answers(); }
  catch (e) { empty.textContent = M.error; empty.hidden = false; return; }
  decks.sort((a, b) => a.name.localeCompare(b.name));
  empty.textContent = M.none; empty.hidden = decks.length > 0;
  const now = Date.now();
  for (const d of decks) {
    let s = {counts: {new: '', lrn: '', rev: ''}};
    try { s = F.session(d, await F.getCards(F.neededIds(d, answers)), answers, now); } catch (e) { /* shown blank */ }
    const tr = document.createElement('tr');
    const td = document.createElement('td'), a = document.createElement('a');
    a.href = '/study/' + d.did; a.className = 'deck'; a.textContent = d.name.split('::').join(' › ');
    a.addEventListener('click', ev => { ev.preventDefault(); go('/study/' + d.did); });
    const sub = document.createElement('div');
    sub.className = 'muted'; sub.style.fontSize = '13px';
    sub.textContent = F.fill(M.updated, {ago: F.ago(d.updated, now, document.documentElement.lang || 'en')});
    td.appendChild(a); td.appendChild(sub); tr.appendChild(td);
    for (const [k, cls] of [['new', 'new'], ['lrn', 'lrn'], ['rev', 'rev']]) {
      const c = document.createElement('td'); c.className = 'n ' + cls; c.textContent = s.counts[k]; tr.appendChild(c);
    }
    tbody.appendChild(tr);
  }
  banner();
}

// ---------- moving between the list and a deck (the address stays the app's own) ----------
function go(path) { history.pushState(null, '', path); route(); }
function route() {
  const m = /^\/(?:study|deck)\/(\d+)/.exec(location.pathname);
  if (m && /^\/study\//.test(location.pathname)) openDeck(+m[1]);
  else showList();
}
window.addEventListener('popstate', route);
document.addEventListener('click', e => {
  const a = e.target.closest && e.target.closest('a[data-nav]');
  if (a) { e.preventDefault(); go('/offline/study'); }
});

async function openDeck(did) {
  let decks = [];
  try { decks = await F.decks(); } catch (e) { /* handled below */ }
  DECK = decks.find(d => d.did === did) || null;
  if (!DECK) { history.replaceState(null, '', '/offline/study'); showList(M.notoffline); return; }
  flash('');
  next();
}

// ---------- the study screen ----------
async function next() {
  if (!DECK) return;
  let answers, cards;
  try {
    answers = await F.answers();
    cards = await F.getCards(F.neededIds(DECK, answers));
    SES = F.session(DECK, cards, answers, Date.now(), prefer);
    prefer = 0;
    if (SES.cid && !cards.has(SES.cid)) cards = new Map([...cards, ...(await F.getCards([SES.cid]))]);
  } catch (e) { showList(M.error); return; }
  banner();
  if (!SES.cid) {
    if (SES.wait) {
      view('wait');
      $('fco-wait-text').textContent = F.fill(M.readyIn, {time: fmtWait(SES.wait)});
      waitTimer = setTimeout(next, SES.wait + 500);
      return;
    }
    history.replaceState(null, '', '/offline/study');
    showList(M.congrats);
    return;
  }
  CARD = cards.get(SES.cid);
  if (!CARD) { showList(M.error); return; }
  view('study');
  shown = false; sent = false; busy = false; typed = ''; t0 = Date.now();
  if (window.WB) { const s = window.WB.strokes(); s.length = 0; window.WB.redraw(); }
  const cn = $('fco-counts');
  for (const k of ['new', 'lrn', 'rev']) {
    const el = cn.querySelector('.' + k);
    el.textContent = P.counts ? SES.counts[k] : '';
    el.classList.toggle('cur', SES.kind === k);
  }
  const labels = SES.labels || ['', '', '', ''];
  document.querySelectorAll('#btns .ans').forEach((b, i) => {
    const sm = b.querySelector('small'), br = b.querySelector('br');
    sm.textContent = P.times ? (labels[i] || '') : '';
    sm.hidden = br.hidden = !P.times;
  });
  const ap = $('fco-approx'); ap.textContent = M.approx; ap.hidden = true;
  $('show').style.display = ''; $('btns').style.display = 'none';
  const th = $('taphint'); if (th && P.tap) th.textContent = th.dataset.q || (th.dataset.q = th.textContent);
  const av = CARD.av || {q: [], a: []};
  $('replay').hidden = !av.q.concat(av.a).some(x => x.t !== 'video');
  load(CARD.q || '<p>' + M.broken + '</p>');
  if (conf().autoplay !== false) playList(av.q);
  scrollTo(0, 0);
}
const conf = () => ((DECK && DECK.confs) || {})[CARD && CARD.conf] || {};
function fit() {
  try { const h = fr.contentDocument.documentElement.scrollHeight; fr.style.height = Math.max(h + 10, innerHeight * 0.55) + 'px'; }
  catch (e) { /* not loaded yet */ }
}
function load(d) { fr.srcdoc = d; }
fr.addEventListener('fc-resize', fit);
fr.onload = () => {
  fit(); setTimeout(fit, 300);
  const doc = fr.contentDocument; if (!doc) return;
  doc.addEventListener('keydown', key);
  const ti = doc.getElementById('typeans');
  if (ti && ti.tagName === 'INPUT' && !shown) { ti.value = typed; ti.focus(); }
  if (P.tap) tapZones(doc);
};
function show() {
  if (shown || busy || !CARD) return;
  let d = CARD.a || '<p>' + M.broken + '</p>';
  if (CARD.tplain) {
    try { const ti = fr.contentDocument.getElementById('typeans'); if (ti && ti.tagName === 'INPUT') typed = ti.value; }
    catch (e) { /* keep what we had */ }
    const out = F.compareAnswer(CARD.tplain.text, typed, CARD.tplain.combining);
    d = d.replace(C.slot, () => out);
  }
  shown = true; shownAt = Date.now(); load(d);
  const th = $('taphint'); if (th && P.tap) th.textContent = M.tapHint;
  $('show').style.display = 'none'; $('btns').style.display = 'flex';
  $('fco-approx').hidden = !(SES && SES.approx && P.times);
  if (conf().autoplay !== false) playList((CARD.av || {}).a || []);
}
async function answer(r) {
  if (!shown || sent || !CARD) return;
  sent = true; stopAudio();
  try { await F.queueAnswer({cid: CARD.id, did: DECK.did, rating: r, ms: Date.now() - t0}); }
  catch (e) { sent = false; toast(M.error); return; }
  next();
}
async function undo() {
  let last = null;
  try { last = await F.undoLast(); } catch (e) { /* nothing to undo */ }
  if (!last) return;
  toast(M.undone);
  if (DECK && DECK.cardIds.includes(last.cid)) { prefer = last.cid; next(); }
  else if (DECK) next(); else showList();
}
for (const u of document.querySelectorAll('.fco-undo')) u.addEventListener('click', undo);
$('show').addEventListener('click', show);
$('fco-check').addEventListener('click', next);
document.querySelectorAll('#btns .ans').forEach(b => b.addEventListener('click', () => answer(+b.dataset.r)));

// ---- audio: the study screen's queue player (sound files from the offline copy, and TTS) ----
let playing = [], cur = null;
function stopAudio() {
  playing = []; if (cur && cur.pause) cur.pause(); cur = null;
  if (window.speechSynthesis) speechSynthesis.cancel();
}
function playList(list) { stopAudio(); playing = (list || []).filter(x => x.t !== 'video'); nextSound(); }
function nextSound() {
  const it = playing.shift(); if (!it) { cur = null; return; }
  if (it.t === 'tts') {
    if (!window.speechSynthesis) { nextSound(); return; }
    const u = new SpeechSynthesisUtterance(it.text); if (it.lang) u.lang = it.lang; u.rate = it.speed || 1;
    u.onend = nextSound; u.onerror = nextSound; cur = null; speechSynthesis.speak(u); return;
  }
  cur = new Audio(it.src); cur.onended = nextSound; cur.onerror = nextSound;
  cur.play().catch(e => { if (e.name === 'NotAllowedError') { playing = []; toast(M.tapPlay); } else nextSound(); });
}
window.fcPlay = function (side, i) { const it = ((CARD && CARD.av) || {})[side]; if (it && it[i]) playList([it[i]]); };
function replay() {
  if (!CARD) return; const av = CARD.av || {q: [], a: []};
  playList(shown ? (conf().replayq !== false ? av.q.concat(av.a) : av.a) : av.q);
}
$('replay').addEventListener('click', replay);

// ---- tap zones (Preferences): a tap shows the answer, then left half = Again, right half = Good ----
const TAPPABLE = 'a,button,input,textarea,select,label,audio,video,summary,[contenteditable],[onclick]';
function tapZones(doc) {
  let sx = 0, sy = 0, st = 0, on = false;
  doc.addEventListener('pointerdown', e => { on = e.isPrimary && (e.pointerType !== 'mouse' || e.button === 0);
    sx = e.clientX; sy = e.clientY; st = Date.now(); }, true);
  doc.addEventListener('pointercancel', () => { on = false; }, true);
  doc.addEventListener('pointerup', e => {
    if (!on) return; on = false;
    if (Math.abs(e.clientX - sx) > 10 || Math.abs(e.clientY - sy) > 10 || Date.now() - st > 600) return;
    if (e.target && e.target.closest && e.target.closest(TAPPABLE)) return;
    const sel = doc.getSelection && doc.getSelection(); if (sel && !sel.isCollapsed) return;
    if (!shown) { show(); return; }
    if (Date.now() - shownAt < 400) return;
    answer(e.clientX < doc.documentElement.clientWidth / 2 ? 1 : 3);
  }, true);
}
// a tap on the whiteboard goes to the card: its links/buttons/inputs, else the tap zones
window.fcoTap = function (x, y) {
  const d = fr.contentDocument; if (!d) return;
  x -= fr.clientLeft; y -= fr.clientTop;
  const el = d.elementFromPoint(x, y), c = el && el.closest && el.closest(TAPPABLE);
  if (c) {
    if (c.matches('input,textarea,select,[contenteditable]')) c.focus();
    else if (c.matches('audio,video')) { if (c.paused) c.play().catch(() => {}); else c.pause(); }
    else c.click();
    return;
  }
  if (!P.tap) return;
  if (!shown) { show(); return; }
  if (Date.now() - shownAt < 400) return;
  answer(x < d.documentElement.clientWidth / 2 ? 1 : 3);
};

// ---- keys: the study screen's (Space/Enter, 1-4, R) and Z / Ctrl+Z for undo ----
function key(e) {
  if ($('fco-study').hidden) return;
  const tg = e.target.tagName;
  if (tg === 'INPUT' || tg === 'TEXTAREA' || tg === 'SELECT') {
    if (e.key === 'Enter' && e.target.id === 'typeans') { e.preventDefault(); show(); }
    return;
  }
  if ((e.ctrlKey || e.metaKey) && !e.altKey && (e.key === 'z' || e.key === 'Z')) { e.preventDefault(); undo(); return; }
  if (e.ctrlKey || e.metaKey || e.altKey) return;
  if (e.key === 'z') { e.preventDefault(); undo(); return; }
  if (e.key === 'r' || e.key === 'R' || e.key === 'F5') { e.preventDefault(); replay(); return; }
  if (e.key === ' ' || e.key === 'Enter') {
    if (tg === 'BUTTON' || tg === 'SUMMARY' || tg === 'A') return;
    e.preventDefault(); if (shown) answer(3); else show();
  } else if (e.key.length === 1 && '1234'.includes(e.key)) answer(+e.key);
}
document.addEventListener('keydown', key);
document.addEventListener('visibilitychange', () => { if (!document.hidden && DECK && !$('fco-wait').hidden) next(); });

// ---------- start ----------
async function start() {
  if (!F || !F.supported()) { view('list'); $('fco-empty').textContent = M.unsupported; $('fco-empty').hidden = false; return; }
  if (!F.user()) { view('list'); $('fco-empty').textContent = M.nouser; $('fco-empty').hidden = false; return; }
  // An answer from the online study screen that couldn't be sent (the service worker brings it here).
  const q = new URLSearchParams(location.search).get('fcans');
  if (q) {
    history.replaceState(null, '', location.pathname);
    try {
      const a = JSON.parse(q), m = /^\/study\/(\d+)/.exec(location.pathname);
      const decks = await F.decks(), answers = await F.answers();
      const known = decks.some(d => d.cardIds.includes(a.cid));
      const dup = answers.some(x => x.cid === a.cid && Math.abs(x.ts - a.ts) < 2000);
      if (known && !dup && a.rating >= 1 && a.rating <= 4) {
        await F.queueAnswer({cid: a.cid, did: m ? +m[1] : 0, rating: a.rating, ms: a.ms || 0, ts: a.ts || Date.now()});
      }
    } catch (e) { /* a broken address: ignore it */ }
  }
  route();
}
start();
})();
