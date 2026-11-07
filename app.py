"""Flashcards: a web front-end on top of Anki's real engine (the `anki` package).

Each user gets their own Anki collection file. Scheduling, cloze, import and
export are all done by Anki's own Rust core, so behaviour matches Anki.
Licence: AGPL-3.0 (because it builds on Anki).
"""
import hashlib, html, json, os, re, secrets, tempfile, threading, time
from contextlib import contextmanager
from urllib.parse import quote

from fastapi import FastAPI, Form, Request, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, Response
from starlette.middleware.sessions import SessionMiddleware

from anki.collection import (Collection, ImportAnkiPackageRequest,
                             ExportAnkiPackageOptions, DeckIdLimit)
from anki.scheduler.v3 import CardAnswer
from anki.sound import SoundOrVideoTag

DATA = os.environ.get("DATA_DIR", os.path.join(os.path.dirname(__file__), "data"))
os.makedirs(DATA, exist_ok=True)
USERS_FILE = os.path.join(DATA, "users.json")
SECRET_FILE = os.path.join(DATA, "secret.key")
if not os.path.exists(SECRET_FILE):
    open(SECRET_FILE, "w").write(secrets.token_hex(32))

app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key=open(SECRET_FILE).read(),
                   max_age=60 * 60 * 24 * 90, same_site="lax")

# ---------- users ----------
def load_users():
    return json.load(open(USERS_FILE)) if os.path.exists(USERS_FILE) else {}

def hash_pw(pw, salt):
    return hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), 200_000).hex()

def create_user(name, pw):
    users = load_users()
    salt = secrets.token_hex(16)
    users[name] = {"salt": salt, "hash": hash_pw(pw, salt)}
    json.dump(users, open(USERS_FILE, "w"))

def check_user(name, pw):
    u = load_users().get(name)
    return bool(u) and secrets.compare_digest(u["hash"], hash_pw(pw, u["salt"]))

# ---------- collections (one open per user, guarded by a lock) ----------
_cols, _locks, _glock = {}, {}, threading.Lock()

@contextmanager
def collection(user):
    with _glock:
        lock = _locks.setdefault(user, threading.Lock())
    with lock:
        if user not in _cols:
            d = os.path.join(DATA, "users", user)
            os.makedirs(d, exist_ok=True)
            col = Collection(os.path.join(d, "collection.anki2"))
            if col.get_config("fsrs", None) is None:
                col.set_config("fsrs", True)
            _cols[user] = col
        yield _cols[user]

def select_deck(col, did):
    # Selecting a deck is an undoable step in Anki; skip it when nothing changes.
    if col.decks.get_current_id() != did:
        col.decks.select(did)

def current_user(request):
    u = request.session.get("user")
    return u if u in load_users() else None

def need_login(request):
    return RedirectResponse("/login", 303)

# ---------- page shell ----------
CSS = """
:root{--bg:#f6f6f4;--card:#fff;--ink:#1d1d1f;--muted:#6e6e73;--line:#e3e3e0;--accent:#2f6fde;
--again:#d64545;--hard:#c98a16;--good:#2e9b4f;--easy:#2f6fde}
@media (prefers-color-scheme:dark){:root{--bg:#161617;--card:#232325;--ink:#f2f2f2;--muted:#a1a1a6;--line:#38383b}}
*{box-sizing:border-box}body{margin:0;font:17px/1.45 -apple-system,system-ui,Segoe UI,Roboto,sans-serif;
background:var(--bg);color:var(--ink);-webkit-text-size-adjust:100%}
header{display:flex;gap:6px;align-items:center;padding:10px 16px;border-bottom:1px solid var(--line);
background:var(--card);position:sticky;top:0;z-index:5;flex-wrap:wrap}
header b{margin-right:auto;font-size:19px}header a{color:var(--ink);text-decoration:none;padding:8px 12px;border-radius:10px}
header a:hover,header a.on{background:var(--bg)}
main{max-width:860px;margin:0 auto;padding:18px 16px 60px}
.box{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px;margin-bottom:16px}
table{width:100%;border-collapse:collapse}td,th{padding:12px 8px;border-bottom:1px solid var(--line);text-align:left}
th{color:var(--muted);font-weight:500;font-size:14px}td.n{text-align:right;width:64px;font-variant-numeric:tabular-nums}
.new{color:var(--accent)}.lrn{color:var(--again)}.rev{color:var(--good)}
a.deck{color:var(--ink);text-decoration:none;font-weight:500;display:block}
input,select,textarea{font:inherit;padding:11px 12px;border:1px solid var(--line);border-radius:10px;
background:var(--bg);color:var(--ink);width:100%}textarea{min-height:90px}
label{display:block;margin:12px 0 6px;color:var(--muted);font-size:14px}
button,.btn{font:inherit;padding:11px 18px;border-radius:10px;border:1px solid var(--line);background:var(--card);
color:var(--ink);cursor:pointer;text-decoration:none;display:inline-block;min-height:44px}
.primary{background:var(--accent);border-color:var(--accent);color:#fff}
.danger{color:var(--again)}.muted{color:var(--muted)}.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
.flash{padding:12px 16px;border-radius:10px;background:#e7f4ea;color:#1d5e30;margin-bottom:16px}
.err{background:#fbe9e9;color:#8a1f1f}
"""

def page(title, body, user=None, active="", flash=""):
    nav = ""
    if user:
        items = [("decks", "/", "Decks"), ("add", "/add", "Add"), ("browse", "/browse", "Browse"),
                 ("import", "/import", "Import"), ("stats", "/stats", "Stats"), ("settings", "/settings", "Settings")]
        nav = "".join(f'<a href="{u}" class="{"on" if k == active else ""}">{t}</a>' for k, u, t in items)
        nav += '<a href="/logout" class="muted">Log out</a>'
    fl = ""
    if flash:
        cls = "flash err" if flash.startswith("!") else "flash"
        fl = f'<div class="{cls}">{html.escape(flash.lstrip("!"))}</div>'
    return HTMLResponse(f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes"><meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="Flashcards"><link rel="manifest" href="/manifest.json">
<title>{html.escape(title)}</title><style>{CSS}</style></head><body>
<header><b>Flashcards</b>{nav}</header><main>{fl}{body}</main></body></html>""")

def take_flash(request):
    return request.session.pop("flash", "")

def set_flash(request, msg):
    request.session["flash"] = msg

@app.get("/manifest.json")
def manifest():
    return {"name": "Flashcards", "short_name": "Flashcards", "start_url": "/", "display": "standalone",
            "background_color": "#f6f6f4", "theme_color": "#2f6fde"}

# ---------- auth ----------
@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    first = not load_users()
    title = "Create the first account" if first else "Log in"
    return page(title, f"""<div class="box" style="max-width:420px;margin:40px auto">
<h2 style="margin-top:0">{title}</h2><form method="post">
<label>Username</label><input name="username" autocapitalize="none" autocomplete="username" required>
<label>Password</label><input name="password" type="password" autocomplete="current-password" required>
<p><button class="primary" style="width:100%">{'Create account' if first else 'Log in'}</button></p></form></div>""",
                flash=take_flash(request))

@app.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...)):
    username = username.strip().lower()
    if not re.fullmatch(r"[a-z0-9_.-]{2,32}", username):
        set_flash(request, "!Username: 2-32 letters, numbers, dot, dash or underscore.")
        return RedirectResponse("/login", 303)
    if not load_users():
        create_user(username, password)
    if not check_user(username, password):
        set_flash(request, "!Wrong username or password.")
        return RedirectResponse("/login", 303)
    request.session["user"] = username
    return RedirectResponse("/", 303)

@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", 303)

# ---------- deck list ----------
@app.get("/", response_class=HTMLResponse)
def decks(request: Request):
    user = current_user(request)
    if not user: return need_login(request)
    with collection(user) as col:
        tree = col.sched.deck_due_tree()
        rows = []
        def walk(node, depth):
            for ch in node.children:
                if ch.name == "Default" and not ch.children and col.card_count() and \
                        not col.find_cards(f"did:{ch.deck_id}"):
                    continue
                pad = depth * 22
                rows.append(f"""<tr><td style="padding-left:{8 + pad}px">
<a class="deck" href="/deck/{ch.deck_id}">{html.escape(ch.name)}</a></td>
<td class="n new">{ch.new_count or '·'}</td><td class="n lrn">{ch.learn_count or '·'}</td>
<td class="n rev">{ch.review_count or '·'}</td></tr>""")
                walk(ch, depth + 1)
        walk(tree, 0)
    body = f"""<div class="box"><table><tr><th>Deck</th><th class="n">New</th><th class="n">Learn</th>
<th class="n">Due</th></tr>{''.join(rows) or '<tr><td colspan=4 class="muted">No decks yet.</td></tr>'}</table></div>
<div class="box"><form method="post" action="/deck/new" class="row">
<input name="name" placeholder="New deck name (use :: for subdecks)" style="flex:1" required>
<button>Create deck</button></form></div>"""
    return page("Decks", body, user, "decks", take_flash(request))

@app.post("/deck/new")
def deck_new(request: Request, name: str = Form(...)):
    user = current_user(request)
    if not user: return need_login(request)
    with collection(user) as col:
        col.decks.id(name.strip())
    return RedirectResponse("/", 303)

# ---------- deck overview + options ----------
@app.get("/deck/{did}", response_class=HTMLResponse)
def deck_overview(request: Request, did: int):
    user = current_user(request)
    if not user: return need_login(request)
    with collection(user) as col:
        deck = col.decks.get(did, default=False)
        if not deck: return RedirectResponse("/", 303)
        select_deck(col, did)
        new, lrn, rev = col.sched.counts()
        total = len(col.find_cards(f'"deck:{deck["name"]}"'))
        conf = col.decks.config_dict_for_deck_id(did) if not deck.get("dyn") else None
    opts = ""
    if conf:
        opts = f"""<div class="box"><h3 style="margin-top:0">Deck options</h3>
<form method="post" action="/deck/{did}/options">
<div class="row"><div style="flex:1"><label>New cards/day</label><input name="new_per_day" type="number" min="0"
value="{conf['new']['perDay']}"></div><div style="flex:1"><label>Maximum reviews/day</label>
<input name="rev_per_day" type="number" min="0" value="{conf['rev']['perDay']}"></div>
<div style="flex:1"><label>Desired retention</label><input name="retention" type="number" step="0.01" min="0.7"
max="0.99" value="{conf.get('desiredRetention', 0.9)}"></div></div>
<p class="muted" style="font-size:14px">Shared by all decks using the preset “{html.escape(conf['name'])}”.</p>
<button>Save options</button></form></div>"""
    body = f"""<div class="box" style="text-align:center"><h2 style="margin-top:0">{html.escape(deck['name'])}</h2>
<div class="row" style="justify-content:center;gap:28px;font-size:20px">
<span class="new">New <b>{new}</b></span><span class="lrn">Learning <b>{lrn}</b></span>
<span class="rev">To review <b>{rev}</b></span></div>
<p class="muted">{total} cards in deck</p>
{'<a class="btn primary" style="min-width:200px" href="/study/%d">Study now</a>' % did if new + lrn + rev
 else '<p>Congratulations! You have finished this deck for now.</p>'}
</div>{opts}
<div class="box row"><a class="btn" href="/browse?q=%22deck:{html.escape(deck['name'])}%22">Browse cards</a>
<a class="btn" href="/deck/{did}/export">Export .apkg</a>
<form method="post" action="/deck/{did}/rename" class="row" style="flex:1">
<input name="name" value="{html.escape(deck['name'])}" style="flex:1"><button>Rename</button></form>
<form method="post" action="/deck/{did}/delete" onsubmit="return confirm('Delete this deck and all its cards?')">
<button class="danger">Delete deck</button></form></div>"""
    return page(deck["name"], body, user, "decks", take_flash(request))

@app.post("/deck/{did}/options")
def deck_options(request: Request, did: int, new_per_day: int = Form(...), rev_per_day: int = Form(...),
                 retention: float = Form(0.9)):
    user = current_user(request)
    if not user: return need_login(request)
    with collection(user) as col:
        conf = col.decks.config_dict_for_deck_id(did)
        conf["new"]["perDay"] = max(0, new_per_day)
        conf["rev"]["perDay"] = max(0, rev_per_day)
        conf["desiredRetention"] = min(0.99, max(0.7, retention))
        col.decks.update_config(conf)
    set_flash(request, "Options saved.")
    return RedirectResponse(f"/deck/{did}", 303)

@app.post("/deck/{did}/rename")
def deck_rename(request: Request, did: int, name: str = Form(...)):
    user = current_user(request)
    if not user: return need_login(request)
    with collection(user) as col:
        col.decks.rename(did, name.strip())
    return RedirectResponse(f"/deck/{did}", 303)

@app.post("/deck/{did}/delete")
def deck_delete(request: Request, did: int):
    user = current_user(request)
    if not user: return need_login(request)
    with collection(user) as col:
        col.decks.remove([did])
    set_flash(request, "Deck deleted.")
    return RedirectResponse("/", 303)

@app.get("/deck/{did}/export")
def deck_export(request: Request, did: int):
    user = current_user(request)
    if not user: return need_login(request)
    with collection(user) as col:
        name = col.decks.name(did)
        out = os.path.join(tempfile.mkdtemp(), "deck.apkg")
        col.export_anki_package(out_path=out, options=ExportAnkiPackageOptions(
            with_scheduling=True, with_deck_configs=True, with_media=True, legacy=False),
            limit=DeckIdLimit(did))
    safe = re.sub(r"[^\w.-]+", "_", name) or "deck"
    return FileResponse(out, filename=f"{safe}.apkg", media_type="application/octet-stream")

# ---------- study ----------
def render_side(col, card, text):
    """Turn Anki card HTML into a self-contained document (media + audio)."""
    tags = card.question_av_tags() + card.answer_av_tags()
    def audio(m):
        side, idx = m.group(1), int(m.group(2))
        lst = card.question_av_tags() if side == "q" else card.answer_av_tags()
        if idx < len(lst) and isinstance(lst[idx], SoundOrVideoTag):
            f = html.escape(lst[idx].filename)
            return f'<audio controls src="/media/{f}" style="max-width:100%"></audio>'
        return ""
    text = re.sub(r"\[anki:play:(q|a):(\d+)\]", audio, text)
    return text

def card_doc(inner, night=False):
    return f"""<!doctype html><html class="{'night_mode' if night else ''}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><base href="/media/">
<style>html,body{{margin:0;padding:0}}body{{padding:20px;font-family:-apple-system,system-ui,sans-serif}}
img{{max-width:100%;height:auto}}.cloze{{font-weight:bold;color:#2f6fde}}
@media (prefers-color-scheme:dark){{body{{background:#232325;color:#f2f2f2}}.card{{background:transparent!important;color:#f2f2f2!important}}}}
</style></head><body class="card">{inner}</body></html>"""

# ---------- card actions (suspend, bury, flag, mark, reset, set due date) ----------
FLAGS = [(0, "No flag", "transparent"), (1, "Red", "#e5484d"), (2, "Orange", "#f08c00"), (3, "Green", "#2f9e44"),
         (4, "Blue", "#2f6fde"), (5, "Pink", "#e64980"), (6, "Turquoise", "#12b5b0"), (7, "Purple", "#8e44ec")]
FLAG_COLOR = {n: c for n, _, c in FLAGS}
FLAG_NAME = {n: t for n, t, _ in FLAGS}
# Undo labels the engine gives these actions; the study screen offers undo for them.
STUDY_UNDOABLE = ("answer", "suspend", "bury", "flag", "tag", "reset", "due date", "forget")

def card_state(card):
    if card.queue == -1: return "Suspended"
    if card.queue in (-2, -3): return "Buried"
    return {0: "New", 1: "Learning", 2: "Review", 3: "Learning"}.get(card.type if card.queue == 4 else card.queue, "")

def card_action(col, card, action, arg=""):
    """Run one Anki card action through the engine; returns a short message."""
    cid, nid = card.id, card.nid
    note_cids = [c for c in col.card_ids_of_note(nid)]
    if action == "suspend":
        col.sched.suspend_cards([cid]); return "Card suspended."
    if action == "suspend_note":
        col.sched.suspend_cards(note_cids); return "Note suspended."
    if action == "unsuspend":
        col.sched.unsuspend_cards([cid]); return "Card unsuspended."
    if action == "bury":
        col.sched.bury_cards([cid]); return "Card buried."
    if action == "bury_note":
        col.sched.bury_notes([nid]); return "Note buried."
    if action == "unbury":
        col.sched.unbury_cards([cid]); return "Card unburied."
    if action == "flag":
        flag = int(arg or 0)
        if not 0 <= flag <= 7: raise ValueError("Unknown flag.")
        if flag and card.user_flag() == flag:   # pressing the same flag again clears it, as in Anki
            flag = 0
        col.set_user_flag_for_cards(flag, [cid])
        return f"Flag: {FLAG_NAME[flag]}." if flag else "Flag removed."
    if action == "mark":
        if col.get_note(nid).has_tag("marked"):
            col.tags.bulk_remove([nid], "marked"); return "Note unmarked."
        col.tags.bulk_add([nid], "marked"); return "Note marked."
    if action == "reset":
        col.sched.schedule_cards_as_new([cid], restore_position=True, reset_counts=False)
        return "Card reset to new."
    if action == "due":
        days = (arg or "").strip().replace(" ", "")
        if not re.fullmatch(r"\d+(-\d+)?!?", days):
            raise ValueError("Due date: enter days like 0 (today), 1, 3-7, or 5! to also set the interval.")
        col.sched.set_due_date([cid], days)
        return "Due date set."
    raise ValueError("Unknown action.")

@app.post("/card/{cid}/action")
async def card_action_route(request: Request, cid: int):
    user = current_user(request)
    if not user: return need_login(request)
    form = await request.form()
    action, arg = str(form.get("action", "")), str(form.get("arg", ""))
    back = str(form.get("back", "/browse"))
    if not back.startswith("/") or back.startswith("//"): back = "/browse"
    ajax = request.headers.get("x-requested-with") == "fetch"
    with collection(user) as col:
        try:
            card = col.get_card(cid)
            msg, good = card_action(col, card, action, arg), True
            card.load()
            info = {"flag": card.user_flag(), "marked": col.get_note(card.nid).has_tag("marked"),
                    "queue": card.queue, "state": card_state(card)}
        except Exception as e:
            msg, good, info = str(e) or "That did not work.", False, {}
    if ajax:
        return {"ok": good, "msg": msg, **info}
    set_flash(request, msg if good else "!" + msg)
    return RedirectResponse(back, 303)

def flag_dot(flag, size=12):
    if not flag: return ""
    return (f'<span title="{FLAG_NAME[flag]} flag" style="display:inline-block;width:{size}px;height:{size}px;'
            f'border-radius:50%;background:{FLAG_COLOR[flag]};vertical-align:middle"></span>')

@app.get("/study/{did}", response_class=HTMLResponse)
def study(request: Request, did: int):
    user = current_user(request)
    if not user: return need_login(request)
    with collection(user) as col:
        select_deck(col, did)
        q = col.sched.get_queued_cards()
        if not q.cards:
            set_flash(request, "Congratulations! You have finished this deck for now.")
            return RedirectResponse(f"/deck/{did}", 303)
        qc = q.cards[0]
        card = col.get_card(qc.card.id)
        labels = [re.sub(r'[\u2068\u2069]', '', l) for l in col.sched.describe_next_states(qc.states)]
        question = render_side(col, card, card.question())
        answer = render_side(col, card, card.answer())
        counts = (q.new_count, q.learning_count, q.review_count)
        kind = {0: "new", 1: "lrn", 2: "rev", 3: "lrn"}.get(card.queue, "rev")
        undo_label = col.undo_status().undo
        if not any(k in (undo_label or "").lower() for k in STUDY_UNDOABLE):
            undo_label = ""
        flag, marked = card.user_flag(), card.note().has_tag("marked")
    qdoc, adoc = json.dumps(card_doc(question)), json.dumps(card_doc(answer))
    names = ["Again", "Hard", "Good", "Easy"]
    colors = ["again", "hard", "good", "easy"]
    btns = "".join(f"""<button class="ans" style="border-color:var(--{c});color:var(--{c})"
onclick="answer({i + 1})"><small>{html.escape(labels[i])}</small><br>{n}</button>"""
                   for i, (n, c) in enumerate(zip(names, colors)))
    undo = f'<form method="post" action="/study/{did}/undo"><button>Undo {html.escape(undo_label)}</button></form>' \
        if undo_label else ""
    flag_btns = "".join(f"""<button type="button" class="fl" onclick="act('flag',{n})" title="Ctrl+{n}">
<span class="dot" style="background:{c}"></span>{t}</button>""" for n, t, c in FLAGS[1:])
    menu = f"""<details class="more"><summary class="btn">More ▾</summary><div class="menu">
<button type="button" onclick="act('mark')">★ Mark note <kbd>*</kbd></button>
<button type="button" onclick="act('bury')">Bury card <kbd>-</kbd></button>
<button type="button" onclick="act('bury_note')">Bury note <kbd>=</kbd></button>
<button type="button" onclick="act('suspend')">Suspend card <kbd>@</kbd></button>
<button type="button" onclick="act('suspend_note')">Suspend note <kbd>!</kbd></button>
<button type="button" onclick="setDue()">Set due date… <kbd>Ctrl+Shift+D</kbd></button>
<button type="button" onclick="resetCard()">Reset to new… <kbd>Ctrl+Alt+N</kbd></button>
<div class="flags">{flag_btns}</div></div></details>"""
    body = f"""<style>main{{max-width:900px}}#frame{{width:100%;min-height:55vh;border:1px solid var(--line);
border-radius:14px;background:var(--card)}}.bar{{position:sticky;bottom:0;padding:12px 0;background:var(--bg)}}
.ans{{flex:1;min-height:64px;font-weight:600}}.ans small{{font-weight:400;opacity:.8}}
.counts span{{margin:0 6px;font-variant-numeric:tabular-nums}}.counts .cur{{text-decoration:underline}}
.more{{position:relative}}.more summary{{list-style:none}}.more summary::-webkit-details-marker{{display:none}}
.menu{{position:absolute;right:0;top:50px;z-index:10;background:var(--card);border:1px solid var(--line);border-radius:14px;
padding:8px;width:280px;box-shadow:0 8px 30px rgba(0,0,0,.18);display:flex;flex-direction:column;gap:4px}}
.menu button{{text-align:left;border:0;display:flex;justify-content:space-between;align-items:center}}
.menu button:hover{{background:var(--bg)}}.menu kbd{{color:var(--muted);font:13px ui-monospace,monospace}}
.flags{{display:grid;grid-template-columns:1fr 1fr;gap:2px;border-top:1px solid var(--line);padding-top:6px;margin-top:4px}}
.flags button{{justify-content:flex-start;gap:8px;padding:9px 10px;font-size:15px}}
.dot{{display:inline-block;width:12px;height:12px;border-radius:50%}}
#flag{{width:14px;height:14px}}#mark{{color:#c98a16;font-size:20px}}
#toast{{position:fixed;left:50%;bottom:110px;transform:translateX(-50%);background:var(--ink);color:var(--bg);
padding:10px 18px;border-radius:10px;opacity:0;transition:opacity .2s;pointer-events:none;z-index:20}}</style>
<div class="row" style="justify-content:space-between;margin-bottom:10px">
<a href="/deck/{did}" class="btn">← Deck</a>
<div class="counts"><span id="flag" class="dot" title="{FLAG_NAME[flag]} flag"
style="background:{FLAG_COLOR[flag]};{'' if flag else 'display:none'}"></span>
<span id="mark" title="Marked" style="{'' if marked else 'display:none'}">★</span>
<span class="new {'cur' if kind == 'new' else ''}">{counts[0]}</span>
<span class="lrn {'cur' if kind == 'lrn' else ''}">{counts[1]}</span>
<span class="rev {'cur' if kind == 'rev' else ''}">{counts[2]}</span></div>
<div class="row">{undo}<a class="btn" href="/edit/{card.nid}?back=/study/{did}">Edit</a>{menu}</div></div>
<div id="toast"></div>
<iframe id="frame" sandbox="allow-same-origin allow-scripts"></iframe>
<form id="f" method="post" action="/study/{did}/answer"><input type="hidden" name="card_id" value="{card.id}">
<input type="hidden" name="rating" id="rating"><input type="hidden" name="ms" id="ms"></form>
<div class="bar"><button id="show" class="primary" style="width:100%;min-height:64px" onclick="show()">Show answer</button>
<div id="btns" class="row" style="display:none;flex-wrap:nowrap">{btns}</div></div>
<script>
const Q={qdoc},A={adoc},t0=Date.now(),fr=document.getElementById('frame');let shown=false;
function fit(){{try{{const h=fr.contentDocument.documentElement.scrollHeight;fr.style.height=Math.max(h+10,window.innerHeight*.55)+'px'}}catch(e){{}}}}
function load(d){{fr.srcdoc=d;}} fr.onload=()=>{{fit();setTimeout(fit,300);
 fr.contentDocument.addEventListener('keydown',key);}};
function show(){{if(shown)return;shown=true;load(A);document.getElementById('show').style.display='none';
 document.getElementById('btns').style.display='flex';}}
function answer(r){{if(!shown)return;document.getElementById('rating').value=r;
 document.getElementById('ms').value=Date.now()-t0;document.getElementById('f').submit();}}
const FC={json.dumps(FLAG_COLOR)};
function toast(m){{const t=document.getElementById('toast');t.textContent=m;t.style.opacity=1;
 clearTimeout(t._h);t._h=setTimeout(()=>t.style.opacity=0,1600);}}
async function act(a,arg){{document.querySelector('.more').open=false;
 const fd=new FormData();fd.append('action',a);fd.append('arg',arg??'');
 const r=await(await fetch('/card/{card.id}/action',{{method:'POST',body:fd,headers:{{'X-Requested-With':'fetch'}}}})).json();
 toast(r.msg);if(!r.ok)return;
 if(['flag','mark'].includes(a)){{const f=document.getElementById('flag');f.style.display=r.flag?'':'none';
  f.style.background=FC[r.flag];document.getElementById('mark').style.display=r.marked?'':'none';}}
 else setTimeout(()=>location.replace('/study/{did}'),500);}}
function setDue(){{const d=prompt('Show this card in how many days? (0 = today, 3-7 = random range, add ! to also set the interval)','1');
 if(d!==null&&d.trim())act('due',d.trim());}}
function resetCard(){{if(confirm('Reset this card to new? Its review history is kept.'))act('reset');}}
function key(e){{if(e.ctrlKey&&!e.altKey&&/^Digit[1-7]$/.test(e.code)){{e.preventDefault();act('flag',+e.code.slice(5));return;}}
 if(e.ctrlKey&&e.shiftKey&&e.code==='KeyD'){{e.preventDefault();setDue();return;}}
 if(e.ctrlKey&&e.altKey&&e.code==='KeyN'){{e.preventDefault();resetCard();return;}}
 if(e.ctrlKey||e.metaKey||e.altKey)return;
 const k={{'-':'bury','=':'bury_note','@':'suspend','!':'suspend_note','*':'mark'}}[e.key];
 if(k){{e.preventDefault();act(k);return;}}
 if(e.key===' '||e.key==='Enter'){{if(e.target.tagName==='BUTTON'||e.target.tagName==='SUMMARY')return;
  e.preventDefault();shown?answer(3):show();}}
 else if('1234'.includes(e.key)&&e.key.length===1)answer(+e.key);}}
document.addEventListener('keydown',key);load(Q);
</script>"""
    return page("Study", body, user, "decks")

@app.post("/study/{did}/answer")
def study_answer(request: Request, did: int, card_id: int = Form(...), rating: int = Form(...), ms: int = Form(0)):
    user = current_user(request)
    if not user: return need_login(request)
    ratings = {1: CardAnswer.AGAIN, 2: CardAnswer.HARD, 3: CardAnswer.GOOD, 4: CardAnswer.EASY}
    with collection(user) as col:
        select_deck(col, did)
        q = col.sched.get_queued_cards()
        # Only answer if the card is still at the top of the queue (protects against double taps).
        if q.cards and q.cards[0].card.id == card_id and rating in ratings:
            card = col.get_card(card_id)
            card.timer_started = time.time() - max(0, min(ms, 3_600_000)) / 1000
            col.sched.answer_card(col.sched.build_answer(card=card, states=q.cards[0].states,
                                                          rating=ratings[rating]))
    return RedirectResponse(f"/study/{did}", 303)

@app.post("/study/{did}/undo")
def study_undo(request: Request, did: int):
    user = current_user(request)
    if not user: return need_login(request)
    with collection(user) as col:
        if any(k in (col.undo_status().undo or "").lower() for k in STUDY_UNDOABLE):
            col.undo()
    return RedirectResponse(f"/study/{did}", 303)

@app.get("/media/{name:path}")
def media(request: Request, name: str):
    user = current_user(request)
    if not user: return Response(status_code=401)
    with collection(user) as col:
        folder = col.media.dir()
    path = os.path.realpath(os.path.join(folder, name))
    if not path.startswith(os.path.realpath(folder) + os.sep) or not os.path.isfile(path):
        return Response(status_code=404)
    return FileResponse(path)

# ---------- add / edit ----------
def note_form(col, nt, did, values=None, action="/add", extra=""):
    values = values or {}
    decks = sorted(col.decks.all_names_and_ids(), key=lambda d: d.name)
    nts = col.models.all_names_and_ids()
    deck_opts = "".join(f'<option value="{d.id}" {"selected" if d.id == did else ""}>{html.escape(d.name)}</option>'
                        for d in decks)
    nt_opts = "".join(f'<option value="{m.id}" {"selected" if m.id == nt["id"] else ""}>{html.escape(m.name)}</option>'
                      for m in nts)
    fields = "".join(f"""<label>{html.escape(f['name'])}</label>
<textarea name="f{i}">{html.escape(values.get(f['name'], ''))}</textarea>""" for i, f in enumerate(nt["flds"]))
    hint = ""
    if nt["type"] == 1:
        hint = '<p class="muted" style="font-size:14px">Cloze: wrap hidden text like {{c1::answer}}, use c2, c3 for more cards.</p>'
    return f"""<form method="post" action="{action}" enctype="multipart/form-data">
<div class="row"><div style="flex:1"><label>Type</label><select name="ntid" onchange="location='/add?nt='+this.value+'&did='+this.form.did.value">{nt_opts}</select></div>
<div style="flex:1"><label>Deck</label><select name="did">{deck_opts}</select></div></div>
{fields}{hint}<label>Tags (space separated)</label><input name="tags" value="{html.escape(values.get('_tags', ''))}">
<label>Image or audio (optional, added to the end of the first field)</label><input type="file" name="upload" accept="image/*,audio/*">
<p class="row">{extra}<button class="primary">Save</button></p></form>"""

@app.get("/add", response_class=HTMLResponse)
def add_page(request: Request, nt: int = 0, did: int = 0):
    user = current_user(request)
    if not user: return need_login(request)
    with collection(user) as col:
        model = col.models.get(nt) if nt else col.models.current()
        did = did or col.get_config("curDeck", 1)
        body = f'<div class="box"><h2 style="margin-top:0">Add card</h2>{note_form(col, model, did)}</div>'
    return page("Add", body, user, "add", take_flash(request))

async def save_upload(col, upload):
    if upload is None or not upload.filename:
        return ""
    data = await upload.read()
    if not data:
        return ""
    name = col.media.write_data(os.path.basename(upload.filename), data)
    if (upload.content_type or "").startswith("audio"):
        return f"[sound:{name}]"
    return f'<img src="{html.escape(name)}">'

@app.post("/add")
async def add(request: Request):
    user = current_user(request)
    if not user: return need_login(request)
    form = await request.form()
    with collection(user) as col:
        model = col.models.get(int(form["ntid"]))
        note = col.new_note(model)
        for i, f in enumerate(model["flds"]):
            note[f["name"]] = str(form.get(f"f{i}", ""))
        extra = await save_upload(col, form.get("upload"))
        if extra:
            first = model["flds"][0]["name"]
            note[first] += extra
        note.tags = str(form.get("tags", "")).split()
        did = int(form["did"])
        try:
            if not note.fields[0].strip() and not extra:
                raise ValueError("The first field is empty.")
            col.add_note(note, did)
            col.models.set_current(model)
            col.set_config("curDeck", did)
            set_flash(request, f"Added {len(note.cards())} card(s).")
        except Exception as e:
            set_flash(request, f"!Could not add: {e}")
    return RedirectResponse(f"/add?nt={model['id']}&did={did}", 303)

@app.get("/edit/{nid}", response_class=HTMLResponse)
def edit_page(request: Request, nid: int, back: str = "/browse"):
    user = current_user(request)
    if not user: return need_login(request)
    if not back.startswith("/"): back = "/browse"
    with collection(user) as col:
        note = col.get_note(nid)
        model = note.note_type()
        values = {k: v for k, v in note.items()}
        values["_tags"] = " ".join(note.tags)
        did = note.cards()[0].did if note.cards() else 1
        form = note_form(col, model, did, values, action=f"/edit/{nid}?back={back}",
                         extra=f'<a class="btn" href="{html.escape(back)}">Cancel</a>')
        form = form.replace('onchange="location=', 'disabled data-x="')
        cards_box = cards_panel(col, note, f"/edit/{nid}?back={quote(back, safe='/')}")
    body = f"""<div class="box"><h2 style="margin-top:0">Edit note</h2>{form}
<form method="post" action="/note/{nid}/delete?back={html.escape(back)}" onsubmit="return confirm('Delete this note and its cards?')">
<button class="danger">Delete note</button></form></div>{cards_box}"""
    return page("Edit", body, user, "browse", take_flash(request))

def cards_panel(col, note, back):
    """Per-card actions (as in Anki's browser): suspend, bury, flag, reset, set due date; plus mark note."""
    marked = note.has_tag("marked")
    eb = html.escape(back)
    def btn(cid, action, label, cls="", arg="", confirm=""):
        oc = f' onsubmit="return confirm(\'{confirm}\')"' if confirm else ""
        return (f'<form method="post" action="/card/{cid}/action"{oc}><input type="hidden" name="action" value="{action}">'
                f'<input type="hidden" name="arg" value="{arg}"><input type="hidden" name="back" value="{eb}">'
                f'<button class="{cls}">{label}</button></form>')
    rows = []
    for card in note.cards():
        tmpl = card.template()["name"]
        flag = card.user_flag()
        state = card_state(card)
        due = ""
        if card.queue == 2 or (card.queue in (-1, -2, -3) and card.type == 2):
            days = card.due - col.sched.today
            due = "due today" if days == 0 else (f"due in {days} d" if days > 0 else f"overdue {-days} d")
        flag_opts = "".join(f'<option value="{n}" {"selected" if n == flag else ""}>{t}</option>' for n, t, _ in FLAGS)
        susp = btn(card.id, "unsuspend", "Unsuspend") if card.queue == -1 else btn(card.id, "suspend", "Suspend")
        bury = btn(card.id, "unbury", "Unbury") if card.queue in (-2, -3) else btn(card.id, "bury", "Bury")
        rows.append(f"""<div class="cardrow" style="border-left:6px solid {FLAG_COLOR[flag] if flag else 'var(--line)'}">
<div class="row" style="justify-content:space-between"><b>{html.escape(tmpl)}</b>
<span class="muted" style="font-size:14px">{flag_dot(flag)} {state}{' · ' + due if due else ''}
· <a href="/browse?q=cid:{card.id}" class="muted">{html.escape(col.decks.name(card.did))}</a></span></div>
<div class="row" style="margin-top:8px">{susp}{bury}
<form method="post" action="/card/{card.id}/action" class="row" style="gap:6px"><input type="hidden" name="action" value="flag">
<input type="hidden" name="back" value="{eb}"><select name="arg" aria-label="Flag" style="width:auto"
onchange="this.form.submit()">{flag_opts}</select></form>
<form method="post" action="/card/{card.id}/action" class="row" style="gap:6px"><input type="hidden" name="action" value="due">
<input type="hidden" name="back" value="{eb}"><input name="arg" placeholder="days, e.g. 1 or 3-7" style="width:150px"
aria-label="Due in days" required><button>Set due</button></form>
{btn(card.id, "reset", "Reset to new", "danger", confirm="Reset this card to new?")}</div></div>""")
    first = note.cards()[0].id if note.cards() else 0
    mark = btn(first, "mark", "★ Unmark note" if marked else "☆ Mark note") if first else ""
    return f"""<style>.cardrow{{padding:12px 14px;border:1px solid var(--line);border-radius:12px;margin-top:10px}}</style>
<div class="box"><div class="row" style="justify-content:space-between"><h3 style="margin:0">Cards</h3>{mark}</div>
{''.join(rows)}</div>"""

@app.post("/edit/{nid}")
async def edit(request: Request, nid: int, back: str = "/browse"):
    user = current_user(request)
    if not user: return need_login(request)
    if not back.startswith("/"): back = "/browse"
    form = await request.form()
    with collection(user) as col:
        note = col.get_note(nid)
        model = note.note_type()
        for i, f in enumerate(model["flds"]):
            note[f["name"]] = str(form.get(f"f{i}", ""))
        extra = await save_upload(col, form.get("upload"))
        if extra:
            note[model["flds"][0]["name"]] += extra
        note.tags = str(form.get("tags", "")).split()
        col.update_note(note)
        new_did = int(form.get("did", 0) or 0)
        if new_did:
            col.set_deck([c.id for c in note.cards()], new_did)
    set_flash(request, "Saved.")
    return RedirectResponse(back, 303)

@app.post("/note/{nid}/delete")
def note_delete(request: Request, nid: int, back: str = "/browse"):
    user = current_user(request)
    if not user: return need_login(request)
    with collection(user) as col:
        col.remove_notes([nid])
    set_flash(request, "Note deleted.")
    if back.startswith("/study") or not back.startswith("/"): back = back if back.startswith("/") else "/browse"
    return RedirectResponse(back, 303)

# ---------- browse ----------
def strip_html(s):
    s = re.sub(r"<style.*?</style>|<script.*?</script>", "", s, flags=re.S)
    s = re.sub(r"\[sound:[^\]]+\]", "🔊", s)
    return html.unescape(re.sub(r"<[^>]+>", " ", s)).strip()

# Row tints like Anki's browser: suspended = yellow, flagged = flag colour, marked = purple-ish.
BROWSE_ROW_CSS = "tr.susp td{background:rgba(240,200,40,.16)}tr.marked td{background:rgba(142,68,236,.10)}" + "".join(
    f"tr.flag{n} td{{background:{c}22}}" for n, _, c in FLAGS[1:])

@app.get("/browse", response_class=HTMLResponse)
def browse(request: Request, q: str = "", page_no: int = 0):
    user = current_user(request)
    if not user: return need_login(request)
    per = 100
    with collection(user) as col:
        try:
            nids = list(reversed(col.find_notes(q)))
            err = ""
        except Exception as e:
            nids, err = [], str(e)
        total = len(nids)
        rows = []
        for nid in nids[page_no * per:(page_no + 1) * per]:
            n = col.get_note(nid)
            cards = n.cards()
            deck = col.decks.name(cards[0].did) if cards else ""
            due, flag, cls = "", 0, ""
            if cards:
                states = [card_state(c) for c in cards]
                nsusp = states.count("Suspended")
                due = states[0]
                if nsusp == len(cards): due, cls = "Suspended", "susp"
                elif nsusp: due = f"{states[0] if states[0] != 'Suspended' else 'New'} · {nsusp} suspended"
                flag = next((c.user_flag() for c in cards if c.user_flag()), 0)
            marked = n.has_tag("marked")
            if flag: cls += f" flag{flag}"
            elif marked: cls += " marked"
            rows.append(f"""<tr class="{cls.strip()}"><td><a class="deck" href="/edit/{nid}?back={quote('/browse?q=' + q, safe='/')}">
{html.escape(strip_html(n.fields[0])[:90]) or '(empty)'}</a><span class="muted" style="font-size:14px">
{html.escape(strip_html(n.fields[1])[:90]) if len(n.fields) > 1 else ''}</span></td>
<td class="muted" style="font-size:14px">{html.escape(deck)}</td><td class="muted state" style="font-size:14px;white-space:nowrap">
{flag_dot(flag)}{' <span title="Marked" style="color:#c98a16">★</span>' if marked else ''} {due}</td></tr>""")
    nav = ""
    if total > per:
        last = (total - 1) // per
        nav = '<div class="row">' + (f'<a class="btn" href="/browse?q={html.escape(q)}&page_no={page_no - 1}">← Prev</a>' if page_no else "") \
            + f'<span class="muted">Page {page_no + 1} of {last + 1}</span>' \
            + (f'<a class="btn" href="/browse?q={html.escape(q)}&page_no={page_no + 1}">Next →</a>' if page_no < last else "") + "</div>"
    body = f"""<div class="box"><form class="row"><input name="q" value="{html.escape(q)}" style="flex:1"
placeholder="Search (Anki syntax: deck:French is:due tag:verb front:*cat*)"><button>Search</button></form>
<p class="muted" style="margin-bottom:0">{total} notes{(' · ' + html.escape(err)) if err else ''}</p></div>
<style>{BROWSE_ROW_CSS}</style>
<div class="box"><table><tr><th>Note</th><th>Deck</th><th>State</th></tr>{''.join(rows)}</table></div>{nav}"""
    return page("Browse", body, user, "browse", take_flash(request))

# ---------- import ----------
@app.get("/import", response_class=HTMLResponse)
def import_page(request: Request):
    user = current_user(request)
    if not user: return need_login(request)
    body = """<div class="box"><h2 style="margin-top:0">Import a deck</h2>
<p>Choose an Anki deck file (<b>.apkg</b>), for example a shared deck from ankiweb.net/shared or one exported from Anki.</p>
<form method="post" enctype="multipart/form-data"><input type="file" name="file" accept=".apkg,.zip" required>
<p><button class="primary">Import</button></p></form>
<p class="muted" style="font-size:14px">Large decks with lots of images can take a minute.</p></div>"""
    return page("Import", body, user, "import", take_flash(request))

@app.post("/import")
async def do_import(request: Request, file: UploadFile = File(...)):
    user = current_user(request)
    if not user: return need_login(request)
    if not (file.filename or "").lower().endswith((".apkg", ".zip")):
        set_flash(request, "!Please choose an .apkg file.")
        return RedirectResponse("/import", 303)
    tmp = os.path.join(tempfile.mkdtemp(), "import.apkg")
    with open(tmp, "wb") as fh:
        while chunk := await file.read(1 << 20):
            fh.write(chunk)
    with collection(user) as col:
        try:
            before = col.note_count()
            col.import_anki_package(ImportAnkiPackageRequest(package_path=tmp))
            set_flash(request, f"Imported {col.note_count() - before} new notes from {file.filename}.")
        except Exception as e:
            set_flash(request, f"!Import failed: {e}")
    os.remove(tmp)
    return RedirectResponse("/", 303)

# ---------- stats ----------
@app.get("/stats", response_class=HTMLResponse)
def stats(request: Request):
    user = current_user(request)
    if not user: return need_login(request)
    with collection(user) as col:
        day_cut = col.sched.day_cutoff
        today = col.db.first(
            "select count(), sum(case when ease=1 then 1 else 0 end), sum(time)/1000 from revlog where id > ?",
            (day_cut - 86400) * 1000)
        week = col.db.all(
            "select (id/1000 - ?)/86400 as d, count() from revlog where id > ? group by d",
            day_cut, (day_cut - 86400 * 7) * 1000)
        totals = col.db.first("select count(), sum(case when queue=0 then 1 else 0 end), "
                              "sum(case when queue=2 then 1 else 0 end), sum(case when queue=-1 then 1 else 0 end) from cards")
        due_tomorrow = len(col.find_cards("prop:due=1"))
    n, again, secs = today[0] or 0, today[1] or 0, today[2] or 0
    correct = f"{round(100 * (n - again) / n)}%" if n else "—"
    by_day = {int(d): c for d, c in week}
    mx = max(by_day.values() or [1])
    bars = "".join(f"""<div style="flex:1;text-align:center"><div style="height:120px;display:flex;align-items:flex-end">
<div style="width:100%;background:var(--accent);border-radius:6px 6px 0 0;height:{int(110 * by_day.get(d, 0) / mx) + 2}px"></div></div>
<small class="muted">{'Today' if d == -1 else f'{-d - 1}d ago'}</small><br><small>{by_day.get(d, 0)}</small></div>"""
                   for d in range(-7, 0))
    body = f"""<div class="box"><h3 style="margin-top:0">Today</h3><div class="row" style="gap:32px;font-size:18px">
<span>Studied <b>{n}</b> cards in <b>{secs // 60}</b> min</span><span>Correct <b>{correct}</b></span>
<span>Due tomorrow <b>{due_tomorrow}</b></span></div></div>
<div class="box"><h3 style="margin-top:0">Reviews, last 7 days</h3><div class="row" style="flex-wrap:nowrap;gap:6px">{bars}</div></div>
<div class="box"><h3 style="margin-top:0">Collection</h3><div class="row" style="gap:32px">
<span>Total cards <b>{totals[0] or 0}</b></span><span class="new">New <b>{totals[1] or 0}</b></span>
<span class="rev">Review <b>{totals[2] or 0}</b></span><span class="muted">Suspended <b>{totals[3] or 0}</b></span></div></div>"""
    return page("Stats", body, user, "stats")

# ---------- settings ----------
@app.get("/settings", response_class=HTMLResponse)
def settings(request: Request):
    user = current_user(request)
    if not user: return need_login(request)
    with collection(user) as col:
        fsrs = col.get_config("fsrs", True)
    users = load_users()
    body = f"""<div class="box"><h3 style="margin-top:0">Scheduler</h3><form method="post" action="/settings/fsrs" class="row">
<span style="flex:1">FSRS scheduling (Anki's modern algorithm) is <b>{'on' if fsrs else 'off'}</b>.</span>
<button>{'Turn off (use SM-2)' if fsrs else 'Turn on'}</button></form></div>
<div class="box"><h3 style="margin-top:0">Change password</h3><form method="post" action="/settings/password">
<label>New password</label><input name="pw" type="password" minlength="6" required><p><button>Change</button></p></form></div>
<div class="box"><h3 style="margin-top:0">Add another person</h3><p class="muted">Each person gets their own separate cards.
{len(users)} account(s) so far.</p><form method="post" action="/settings/adduser">
<div class="row"><input name="username" placeholder="Username" style="flex:1" autocapitalize="none" required>
<input name="pw" type="password" placeholder="Password" minlength="6" style="flex:1" required><button>Create</button></div></form></div>"""
    return page("Settings", body, user, "settings", take_flash(request))

@app.post("/settings/fsrs")
def toggle_fsrs(request: Request):
    user = current_user(request)
    if not user: return need_login(request)
    with collection(user) as col:
        col.set_config("fsrs", not col.get_config("fsrs", True))
    return RedirectResponse("/settings", 303)

@app.post("/settings/password")
def change_pw(request: Request, pw: str = Form(...)):
    user = current_user(request)
    if not user: return need_login(request)
    create_user(user, pw)
    set_flash(request, "Password changed.")
    return RedirectResponse("/settings", 303)

@app.post("/settings/adduser")
def add_user(request: Request, username: str = Form(...), pw: str = Form(...)):
    user = current_user(request)
    if not user: return need_login(request)
    username = username.strip().lower()
    if not re.fullmatch(r"[a-z0-9_.-]{2,32}", username) or username in load_users():
        set_flash(request, "!That username is invalid or taken.")
    else:
        create_user(username, pw)
        set_flash(request, f"Account '{username}' created.")
    return RedirectResponse("/settings", 303)

@app.get("/healthz")
def healthz():
    return {"ok": True}
