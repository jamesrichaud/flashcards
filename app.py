"""Flashcards: a web front-end on top of Anki's real engine (the `anki` package).

Each user gets their own Anki collection file. Scheduling, cloze, import and
export are all done by Anki's own Rust core, so behaviour matches Anki.
Licence: AGPL-3.0 (because it builds on Anki).
"""
import hashlib, html, json, os, re, secrets, tempfile, threading, time, unicodedata
from contextlib import contextmanager
from urllib.parse import quote

from fastapi import FastAPI, Form, Request, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, Response
from starlette.middleware.sessions import SessionMiddleware

from anki.collection import Collection, ExportAnkiPackageOptions, DeckIdLimit
from anki.scheduler.v3 import CardAnswer
from anki.sound import SoundOrVideoTag
from anki.browser import BrowserConfig
from anki.config import Config
from anki.utils import ids2str

from flashcards.io_review import IO_REVIEW_JS

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

# Deck list, overview, rename/move/collapse/description/delete/export: flashcards/decks.py

# ---------- study ----------
VIDEO_EXTS = (".mp4", ".webm", ".mov", ".mkv", ".ogv", ".m4v", ".avi", ".mpg", ".mpeg")
PLAY_SVG = ('<svg viewBox="0 0 64 64" width="40" height="40"><circle cx="32" cy="32" r="29" fill="#fff" '
            'stroke="#888" stroke-width="2"/><path d="M24 18l22 14-22 14z" fill="#555"/></svg>')

def av_list(tags):
    """Anki's [sound:]/TTS tags as playable items for the study screen's player."""
    out = []
    for t in tags:
        if isinstance(t, SoundOrVideoTag):
            video = t.filename.lower().endswith(VIDEO_EXTS)
            out.append({"t": "video" if video else "sound", "src": "/media/" + quote(t.filename)})
        else:   # TTSTag: spoken by the browser's speech synthesis
            out.append({"t": "tts", "text": t.field_text, "lang": t.lang.replace("_", "-"),
                        "speed": t.speed or 1.0})
    return out

def render_side(col, card, text):
    """Turn Anki card HTML into a self-contained document: [anki:play] markers become
    Anki-style replay buttons (videos are shown inline)."""
    lists = {"q": card.question_av_tags(), "a": card.answer_av_tags()}
    def audio(m):
        side, idx = m.group(1), int(m.group(2))
        lst = lists[side]
        if idx >= len(lst):
            return ""
        tag = lst[idx]
        if isinstance(tag, SoundOrVideoTag) and tag.filename.lower().endswith(VIDEO_EXTS):
            return (f'<video controls playsinline src="/media/{html.escape(quote(tag.filename))}" '
                    f'style="max-width:100%"></video>')
        return (f'<a class="replay-button soundLink" href="#" title="Play" '
                f'onclick="parent.fcPlay&&parent.fcPlay(\'{side}\',{idx});return false;">{PLAY_SVG}</a>')
    return re.sub(r"\[anki:play:(q|a):(\d+)\]", audio, text)

# ---------- type in the answer ({{type:Field}}), ported from Anki's reviewer ----------
TYPE_PAT = re.compile(r"\[\[type:(.+?)\]\]")
TYPE_SLOT = "<!--typeans-->"

def type_answer_info(col, card, qtext):
    """None if the card has no type-in box; otherwise what to compare against."""
    m = TYPE_PAT.search(qtext)
    if not m:
        return None
    fld, cloze, combining = m.group(1), 0, True
    if fld.startswith("cloze:"):
        cloze, fld = card.ord + 1, fld.split(":", 1)[1]
    if fld.startswith("nc:"):
        combining, fld = False, fld.split(":", 1)[1]
    for f in card.note_type()["flds"]:
        if f["name"] == fld:
            expected = card.note()[fld]
            if cloze:
                expected = col.extract_cloze_for_typing(expected, cloze)
            return {"expected": expected, "font": f.get("font", "Arial"), "size": f.get("size", 20),
                    "combining": combining, "field": fld, "cloze": bool(cloze)}
    return {"expected": None, "field": fld, "cloze": bool(cloze)}

def type_question(info, text):
    if not info:
        return text
    if info["expected"] is None:
        warn = ("Please run Tools&gt;Empty Cards" if info["cloze"]
                else f"Type answer: unknown field {html.escape(info['field'])}")
        return TYPE_PAT.sub(warn, text)
    if not info["expected"]:
        return TYPE_PAT.sub("", text)
    box = (f'<center><input type="text" id="typeans" autocomplete="off" autocapitalize="off" spellcheck="false" '
           f'style="font-family:\'{html.escape(info["font"])}\';font-size:{int(info["size"])}px"></center>')
    return TYPE_PAT.sub(lambda m: box, text)

def type_answer(info, text):
    """Answer side: leave a slot that the page fills with the engine's comparison."""
    if not info or not info["expected"]:
        return TYPE_PAT.sub("", text)
    had_hr = "<hr id=answer>" in text
    if had_hr:
        text = text.replace("<hr id=answer>", "")
    slot = (("<hr id=answer>" if had_hr else "") + f'<span style="font-family:\'{html.escape(info["font"])}\';'
            f'font-size:{int(info["size"])}px">{TYPE_SLOT}</span>')
    return TYPE_PAT.sub(lambda m: slot, text)

# MathJax for \( \) and \[ \] in cards, set up like Anki's reviewer (MathJax 3; loaded only when
# a card uses it). Once typeset, the frame gets an "fc-resize" event so its page can resize it.
MATHJAX_PAT = re.compile(r"\\[(\[]")
MATHJAX = r"""<script>window.MathJax={tex:{displayMath:[["\\[","\\]"]],processEscapes:false,
processEnvironments:false,processRefs:false},startup:{typeset:true,pageReady(){
return MathJax.startup.defaultPageReady().then(function(){try{window.frameElement&&
window.frameElement.dispatchEvent(new Event("fc-resize"))}catch(e){}})}}};</script>
<script async src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-svg-full.js"></script>"""

def card_doc(inner, night=False):
    mj = MATHJAX if MATHJAX_PAT.search(inner) else ""
    if "anki.imageOcclusion" in inner:   # Image Occlusion cards: draw the masks (flashcards/io_review.py)
        mj += IO_REVIEW_JS
    return f"""<!doctype html><html class="{'night_mode' if night else ''}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><base href="/media/">
<style>html,body{{margin:0;padding:0}}body{{padding:20px;font-family:-apple-system,system-ui,sans-serif}}
img{{max-width:100%;height:auto}}.cloze{{font-weight:bold;color:#2f6fde}}
.replay-button{{display:inline-block;vertical-align:middle;margin:4px}}.replay-button svg{{display:block}}
#typeans{{width:100%;box-sizing:border-box;padding:10px 12px;border:1px solid #bbb;border-radius:10px}}
code#typeans{{display:inline-block;width:auto;border:0;padding:0;font-family:inherit}}
.typeGood{{background-color:#afa;color:black}}.typeBad{{background-color:#faa;color:black}}
.typeMissed{{background-color:#ccc;color:black}}
@media (prefers-color-scheme:dark){{body{{background:#232325;color:#f2f2f2}}.card{{background:transparent!important;color:#f2f2f2!important}}
input#typeans{{background:#161617;color:#f2f2f2;border-color:#555}}.replay-button circle{{fill:#333;stroke:#777}}
.replay-button path{{fill:#ddd}}}}
</style>{mj}</head><body class="card">{inner}</body></html>"""

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

# ---------- card info (Anki's "Card Info" window) ----------
def fmt_span(secs):
    """Time span the way Anki writes it: 45 seconds, 10 minutes, 3 days, 2.1 months, 1.5 years."""
    secs = abs(secs)
    for unit, size in (("year", 365 * 86400), ("month", 30 * 86400), ("day", 86400),
                       ("hour", 3600), ("minute", 60)):
        if secs >= size:
            v = round(secs / size, 1 if unit in ("year", "month") else 0)
            v = int(v) if v == int(v) else v
            return f"{v} {unit}{'' if v == 1 else 's'}"
    v = round(secs) if secs >= 1 else round(secs, 1)
    return f"{v} second{'' if v == 1 else 's'}"

def fmt_date(ts, with_time=False):
    return time.strftime("%Y-%m-%d %H:%M" if with_time else "%Y-%m-%d", time.localtime(ts))

REVIEW_KIND = {0: ("Learn", "lrn"), 1: ("Review", "rev"), 2: ("Relearn", "lrn"), 3: ("Filtered", "new"),
               4: ("Manual", "muted"), 5: ("Rescheduled", "muted")}
RATING = {0: ("—", "muted"), 1: ("Again", "again"), 2: ("Hard", "hard"), 3: ("Good", "good"), 4: ("Easy", "easy")}

def card_info_html(col, cid):
    """Card statistics from the engine's card_stats call, laid out like Anki's Card Info."""
    s = col.card_stats_data(cid)
    card = col.get_card(cid)
    rows = [("Added", fmt_date(s.added))]
    if s.HasField("first_review"): rows.append(("First review", fmt_date(s.first_review)))
    if s.HasField("latest_review"): rows.append(("Latest review", fmt_date(s.latest_review)))
    if s.interval: rows.append(("Interval", fmt_span(s.interval * 86400)))
    fsrs = bool(col.get_config("fsrs", False)) and s.HasField("memory_state")
    if fsrs:
        rows.append(("Stability", fmt_span(s.memory_state.stability * 86400)))
        rows.append(("Difficulty", f"{round((s.memory_state.difficulty - 1) / 9 * 100)}%"))
        if s.HasField("fsrs_retrievability"):
            rows.append(("Retrievability", f"{round(s.fsrs_retrievability * 100)}%"))
    elif s.ease:
        rows.append(("Ease", f"{s.ease // 10}%"))
    paused = " (suspended)" if card.queue == -1 else " (buried)" if card.queue in (-2, -3) else ""
    if s.HasField("due_date"):
        rows.append(("Due", fmt_date(s.due_date) + paused))
    elif s.HasField("due_position"):
        rows.append(("Position", f"{s.due_position}{paused}"))
    rows += [("Reviews", str(s.reviews)), ("Lapses", str(s.lapses))]
    if s.reviews:
        rows += [("Average time", fmt_span(s.average_secs)), ("Total time", fmt_span(s.total_secs))]
    rows += [("Card type", s.card_type), ("Note type", s.notetype),
             ("Deck", s.deck + (f" (from {s.original_deck})" if s.HasField("original_deck") else "")),
             ("Preset", s.preset), ("Card ID", str(s.card_id)), ("Note ID", str(s.note_id))]
    if s.custom_data: rows.append(("Custom data", s.custom_data))
    stats = "".join(f'<tr><th>{k}</th><td>{html.escape(v)}</td></tr>' for k, v in rows)
    has_mem = bool(col.get_config("fsrs", False)) and any(e.HasField("memory_state") for e in s.revlog)
    hist = []
    for e in s.revlog:   # newest first, as in Anki
        kind, kcls = REVIEW_KIND.get(e.review_kind, ("?", "muted"))
        rating, rcls = RATING.get(e.button_chosen, ("?", "muted"))
        ivl = fmt_span(e.interval) if e.interval else "—"
        if has_mem:
            extra = (f'<td>{fmt_span(e.memory_state.stability * 86400)}</td>'
                     f'<td>{round((e.memory_state.difficulty - 1) / 9 * 100)}%</td>') \
                if e.HasField("memory_state") else "<td>—</td><td>—</td>"
        else:
            extra = f'<td>{e.ease // 10}%</td>' if e.ease else "<td>—</td>"
        hist.append(f"""<tr><td style="white-space:nowrap">{fmt_date(e.time, True)}</td>
<td class="{kcls}">{kind}</td><td style="color:var(--{rcls})">{rating}</td><td>{ivl}</td>{extra}
<td>{fmt_span(e.taken_secs) if e.taken_secs else '—'}</td></tr>""")
    cols = "<th>Stability</th><th>Difficulty</th>" if has_mem else "<th>Ease</th>"
    history = (f"""<div style="overflow-x:auto"><table class="hist"><tr><th>Date</th><th>Type</th><th>Rating</th>
<th>Interval</th>{cols}<th>Time</th></tr>{''.join(hist)}</table></div>""" if hist
               else '<p class="muted">This card has not been reviewed yet.</p>')
    return stats, history, s.note_id

@app.get("/card/{cid}/info", response_class=HTMLResponse)
def card_info(request: Request, cid: int, back: str = ""):
    user = current_user(request)
    if not user: return need_login(request)
    if not back.startswith("/") or back.startswith("//"): back = ""
    with collection(user) as col:
        try:
            card = col.get_card(cid)
        except Exception:
            set_flash(request, "!That card was not found.")
            return RedirectResponse("/browse", 303)
        stats, history, nid = card_info_html(col, cid)
        title = strip_html(card.question())[:80]
    back_btn = f'<a class="btn" href="{html.escape(back)}">← Back</a>' if back else ""
    edit_back = quote(f"/card/{cid}/info" + (f"?back={quote(back, safe='/')}" if back else ""), safe="/")
    body = f"""<style>.info th{{width:40%;font-size:15px}}.info td,.info th{{padding:9px 8px}}
.hist td,.hist th{{padding:9px 8px;font-size:15px}}</style>
<div class="row" style="justify-content:space-between;margin-bottom:12px">{back_btn}
<a class="btn" href="/edit/{nid}?back={edit_back}">Edit note</a></div>
<div class="box"><h2 style="margin-top:0">Card info</h2>
<p class="muted" style="margin-top:-6px">{html.escape(title) or '(empty)'}</p>
<table class="info">{stats}</table></div>
<div class="box"><h3 style="margin-top:0">Review history</h3>{history}</div>"""
    return page("Card info", body, user, "browse", take_flash(request))

def js(value):
    """JSON for inline <script>: no '<' so card HTML or URL text can't close the script tag."""
    return json.dumps(value).replace("<", "\\u003c")

def study_undo_label(col):
    label = col.undo_status().undo
    return label if any(k in (label or "").lower() for k in STUDY_UNDOABLE) else ""

def fmt_wait(secs):
    mins = max(1, round(secs / 60))
    return f"{mins} minute{'s' if mins != 1 else ''}" if mins < 60 else fmt_span(secs)

def study_waiting(request, user, did, secs, undo_label, flash):
    """Anki's 'learning cards due later' state: nothing to show yet, but cards answered
    Again come back in this session once their step is up, so wait and reload."""
    undo = (f'<form method="post" action="/study/{did}/undo"><button>Undo {html.escape(undo_label)}</button></form>'
            if undo_label else "")
    body = f"""<div class="row" style="justify-content:space-between;margin-bottom:10px">
<a href="/deck/{did}" class="btn">\u2190 Deck</a>{undo}</div>
<div class="box" style="text-align:center"><h2 style="margin-top:0">Nothing due right now</h2>
<p>The next learning card will be ready in <b id="wait">{fmt_wait(secs)}</b>.</p>
<p class="muted">Keep this screen open and it will carry on by itself.</p>
<a class="btn primary" href="/study/{did}">Check again</a></div>
<script>setTimeout(()=>location.replace('/study/{did}'),{(int(secs) + 1) * 1000});</script>"""
    return page("Study", body, user, "decks", flash)

@app.get("/study/{did}", response_class=HTMLResponse)
def study(request: Request, did: int, cid: int = 0, shown: int = 0, ms: int = 0, typed: str = ""):
    user = current_user(request)
    if not user: return need_login(request)
    flash = take_flash(request)
    with collection(user) as col:
        select_deck(col, did)
        q = col.sched.get_queued_cards()
        if not q.cards:
            info = col.sched.congratulations_info()
            if 0 < info.secs_until_next_learn < 86400 and info.learn_remaining:
                return study_waiting(request, user, did, info.secs_until_next_learn, study_undo_label(col), flash)
            done = "Congratulations! You have finished this deck for now."
            set_flash(request, f"{flash.lstrip('!')} {done}" if flash else done)
            return RedirectResponse(f"/deck/{did}", 303)
        qc = q.cards[0]
        card = col.get_card(qc.card.id)
        labels = [re.sub(r'[\u2068\u2069]', '', l) for l in col.sched.describe_next_states(qc.states)]
        qtext = card.question()
        tinfo = type_answer_info(col, card, qtext)
        question = render_side(col, card, type_question(tinfo, qtext))
        answer = render_side(col, card, type_answer(tinfo, card.answer()))
        has_type = bool(tinfo and tinfo["expected"])
        counts = (q.new_count, q.learning_count, q.review_count)
        kind = {0: "new", 1: "lrn", 2: "rev", 3: "lrn"}.get(card.queue, "rev")
        undo_label = study_undo_label(col)
        flag, marked = card.user_flag(), card.note().has_tag("marked")
        conf = col.decks.config_dict_for_deck_id(card.current_deck_id())
        av = {"q": av_list(card.question_av_tags()), "a": av_list(card.answer_av_tags()),
              "autoplay": bool(conf.get("autoplay", True)), "replayq": bool(conf.get("replayq", True))}
    # Coming back from Edit or Card info: same card, same side, timer carries on.
    resume = cid == card.id
    start_shown = resume and bool(shown)
    elapsed = max(0, min(ms, 3_600_000)) if resume else 0
    qdoc, adoc = js(card_doc(question)), js(card_doc(answer))
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
<button type="button" onclick="info()">Card info <kbd>I</kbd></button>
<button type="button" onclick="replay()">Replay audio <kbd>R</kbd></button>
<div class="flags">{flag_btns}</div></div></details>"""
    has_audio = any(x["t"] != "video" for x in av["q"] + av["a"])
    replay_btn = (f'<button type="button" id="replay" onclick="replay()" title="Replay audio (R)" '
                  f'aria-label="Replay audio">🔊</button>' if has_audio else "")
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
<div class="row">{undo}{replay_btn}<a class="btn" href="/edit/{card.nid}?back=/study/{did}"
onclick="edit();return false;">Edit</a>{menu}</div></div>
<div id="toast"></div>
<iframe id="frame" sandbox="allow-same-origin allow-scripts"></iframe>
<form id="f" method="post" action="/study/{did}/answer"><input type="hidden" name="card_id" value="{card.id}">
<input type="hidden" name="rating" id="rating"><input type="hidden" name="ms" id="ms"></form>
<div class="bar"><button id="show" class="primary" style="width:100%;min-height:64px" onclick="show()">Show answer</button>
<div id="btns" class="row" style="display:none;flex-wrap:nowrap">{btns}</div></div>
<script>
const Q={qdoc},A={adoc},t0=Date.now()-{elapsed},fr=document.getElementById('frame');let shown=false,busy=false,quiet={js(resume)};
const AV={js(av)},HAS_TYPE={js(has_type)},SLOT={js(TYPE_SLOT)};let typed={js(typed[:1000])};
function fit(){{try{{const h=fr.contentDocument.documentElement.scrollHeight;fr.style.height=Math.max(h+10,window.innerHeight*.55)+'px'}}catch(e){{}}}}
function load(d){{fr.srcdoc=d;}} fr.addEventListener('fc-resize',fit);fr.onload=()=>{{fit();setTimeout(fit,300);
 fr.contentDocument.addEventListener('keydown',key);
 const ti=fr.contentDocument.getElementById('typeans');if(ti&&ti.tagName==='INPUT'){{ti.value=typed;ti.focus();}}}};
// ---- audio: a small queue player, like Anki's (sound files and TTS) ----
let playing=[],cur=null;
function stopAudio(){{playing=[];if(cur&&cur.pause)cur.pause();cur=null;
 if(window.speechSynthesis)speechSynthesis.cancel();}}
function playList(list){{stopAudio();playing=list.filter(x=>x.t!=='video');next();}}
function next(){{const it=playing.shift();if(!it){{cur=null;return;}}
 if(it.t==='tts'){{if(!window.speechSynthesis){{next();return;}}const u=new SpeechSynthesisUtterance(it.text);
  if(it.lang)u.lang=it.lang;u.rate=it.speed||1;u.onend=next;u.onerror=next;cur=null;speechSynthesis.speak(u);return;}}
 cur=new Audio(it.src);cur.onended=next;cur.onerror=next;
 cur.play().catch(e=>{{if(e.name==='NotAllowedError'){{playing=[];toast('Tap 🔊 to play the audio');}}else next();}});}}
function fcPlay(side,i){{const it=AV[side][i];if(it)playList([it]);}}
function sideTags(){{return shown?(AV.replayq?AV.q.concat(AV.a):AV.a):AV.q;}}
function replay(){{document.querySelector('.more').open=false;playList(sideTags());}}
async function show(){{if(shown||busy)return;busy=true;let d=A;
 if(HAS_TYPE){{try{{const ti=fr.contentDocument.getElementById('typeans');if(ti&&ti.tagName==='INPUT')typed=ti.value;}}catch(e){{}}
  let out='';try{{const fd=new FormData();fd.append('card_id','{card.id}');fd.append('typed',typed);
   out=(await(await fetch('/study/{did}/typeans',{{method:'POST',body:fd}})).json()).html;}}catch(e){{}}
  d=A.replace(SLOT,()=>out);}}
 shown=true;busy=false;load(d);document.getElementById('show').style.display='none';
 document.getElementById('btns').style.display='flex';if(AV.autoplay&&!quiet)playList(AV.a);quiet=false;}}
function answer(r){{if(!shown)return;stopAudio();document.getElementById('rating').value=r;
 document.getElementById('ms').value=Date.now()-t0;document.getElementById('f').submit();}}
function here(){{if(!shown){{try{{const ti=fr.contentDocument.getElementById('typeans');if(ti&&ti.tagName==='INPUT')typed=ti.value;}}catch(e){{}}}}
 return '?cid={card.id}'+(shown?'&shown=1':'')+'&ms='+(Date.now()-t0)+(typed?'&typed='+encodeURIComponent(typed):'');}}
function edit(){{stopAudio();location.href='/edit/{card.nid}?back=/study/{did}'+encodeURIComponent(here());}}
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
function info(){{stopAudio();location.href='/card/{card.id}/info?back=/study/{did}'+encodeURIComponent(here());}}
function resetCard(){{if(confirm('Reset this card to new? Its review history is kept.'))act('reset');}}
function key(e){{const tg=e.target.tagName;
 if(tg==='INPUT'||tg==='TEXTAREA'||tg==='SELECT'){{if(e.key==='Enter'&&e.target.id==='typeans'){{e.preventDefault();show();}}return;}}
 if(e.ctrlKey&&!e.altKey&&/^Digit[1-7]$/.test(e.code)){{e.preventDefault();act('flag',+e.code.slice(5));return;}}
 if(e.ctrlKey&&e.shiftKey&&e.code==='KeyD'){{e.preventDefault();setDue();return;}}
 if(e.ctrlKey&&e.altKey&&e.code==='KeyN'){{e.preventDefault();resetCard();return;}}
 if(e.ctrlKey||e.metaKey||e.altKey)return;
 if(e.key==='i'||e.key==='I'){{e.preventDefault();info();return;}}
 if(e.key==='r'||e.key==='R'||e.key==='F5'){{e.preventDefault();replay();return;}}
 if(e.key==='e'||e.key==='E'){{e.preventDefault();edit();return;}}
 const k={{'-':'bury','=':'bury_note','@':'suspend','!':'suspend_note','*':'mark'}}[e.key];
 if(k){{e.preventDefault();act(k);return;}}
 if(e.key===' '||e.key==='Enter'){{if(e.target.tagName==='BUTTON'||e.target.tagName==='SUMMARY')return;
  e.preventDefault();shown?answer(3):show();}}
 else if('1234'.includes(e.key)&&e.key.length===1)answer(+e.key);}}
document.addEventListener('keydown',key);
if({js(start_shown)})show();else{{load(Q);if(AV.autoplay&&!quiet)playList(AV.q);quiet=false;}}
const FL={js(flash.lstrip('!'))};if(FL)toast(FL);
</script>"""
    return page("Study", body, user, "decks")

@app.post("/study/{did}/typeans")
def study_typeans(request: Request, did: int, card_id: int = Form(...), typed: str = Form("")):
    """Compare a typed answer with the card's {{type:Field}} using the engine (as Anki's reviewer does)."""
    user = current_user(request)
    if not user: return Response(status_code=401)
    with collection(user) as col:
        try:
            card = col.get_card(card_id)
        except Exception:
            return {"html": ""}
        info = type_answer_info(col, card, card.question())
        if not info or not info["expected"]:
            return {"html": ""}
        return {"html": col.compare_answer(info["expected"], typed[:5000], info["combining"])}

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
            ans = col.sched.build_answer(card=card, states=q.cards[0].states, rating=ratings[rating])
            leech = col.sched.state_is_leech(ans.new_state)
            col.sched.answer_card(ans)
            if leech:   # same notice as Anki's reviewer
                card.load()
                msg = "Card was a leech."
                if card.queue < 0:
                    msg += " It has been suspended."
                set_flash(request, msg)
    return RedirectResponse(f"/study/{did}", 303)

@app.post("/study/{did}/undo")
def study_undo(request: Request, did: int):
    user = current_user(request)
    if not user: return need_login(request)
    with collection(user) as col:
        if any(k in (col.undo_status().undo or "").lower() for k in STUDY_UNDOABLE):
            col.undo()
    return RedirectResponse(f"/study/{did}", 303)

# Types for card media that Python's mimetypes table may not know on every system.
MEDIA_TYPES = {".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".aac": "audio/aac", ".oga": "audio/ogg",
               ".ogg": "audio/ogg", ".opus": "audio/ogg", ".weba": "audio/webm", ".wav": "audio/wav",
               ".flac": "audio/flac", ".spx": "audio/ogg", ".amr": "audio/amr", ".3gp": "video/3gpp",
               ".mp4": "video/mp4", ".m4v": "video/mp4", ".webm": "video/webm", ".ogv": "video/ogg",
               ".mov": "video/quicktime", ".mkv": "video/x-matroska", ".avi": "video/x-msvideo",
               ".mpg": "video/mpeg", ".mpeg": "video/mpeg", ".png": "image/png", ".jpg": "image/jpeg",
               ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp", ".avif": "image/avif",
               ".svg": "image/svg+xml", ".bmp": "image/bmp", ".ico": "image/x-icon", ".tif": "image/tiff",
               ".tiff": "image/tiff", ".css": "text/css", ".js": "text/javascript", ".json": "application/json",
               ".woff": "font/woff", ".woff2": "font/woff2", ".ttf": "font/ttf", ".otf": "font/otf"}
MEDIA_SANDBOX = (".html", ".htm", ".xhtml", ".svg", ".xml", ".pdf")

@app.get("/media/{name:path}")
def media(request: Request, name: str):
    user = current_user(request)
    if not user: return Response(status_code=401)
    with collection(user) as col:
        folder = col.media.dir()
    root = os.path.realpath(folder) + os.sep
    path = os.path.realpath(os.path.join(folder, name))
    if not os.path.isfile(path):   # Anki stores names in NFC; a browser may send another form
        path = os.path.realpath(os.path.join(folder, unicodedata.normalize("NFC", name)))
    if not path.startswith(root) or not os.path.isfile(path):
        return Response(status_code=404)
    ext = os.path.splitext(path)[1].lower()
    headers = {"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-cache"}
    if ext in MEDIA_SANDBOX:   # page-like files from shared decks must not run scripts as this app
        headers["Content-Security-Policy"] = "sandbox"
    # FileResponse answers Range requests (206), which Safari needs to play audio and video.
    return FileResponse(path, media_type=MEDIA_TYPES.get(ext), headers=headers)

# ---------- add / edit (routes in flashcards/editor.py) ----------
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
{btn(card.id, "reset", "Reset to new", "danger", confirm="Reset this card to new?")}
<a class="btn" href="/card/{card.id}/info?back={html.escape(quote(back, safe='/'))}">Card info</a></div></div>""")
    first = note.cards()[0].id if note.cards() else 0
    mark = btn(first, "mark", "★ Unmark note" if marked else "☆ Mark note") if first else ""
    return f"""<style>.cardrow{{padding:12px 14px;border:1px solid var(--line);border-radius:12px;margin-top:10px}}</style>
<div class="box"><div class="row" style="justify-content:space-between"><h3 style="margin:0">Cards</h3>{mark}</div>
{''.join(rows)}</div>"""

@app.post("/note/{nid}/delete")
def note_delete(request: Request, nid: int, back: str = "/browse"):
    user = current_user(request)
    if not user: return need_login(request)
    with collection(user) as col:
        col.remove_notes([nid])
    set_flash(request, "Note deleted.")
    return RedirectResponse(safe_back(back), 303)

# ---------- browse ----------
def strip_html(s):
    s = re.sub(r"<style.*?</style>|<script.*?</script>", "", s, flags=re.S)
    s = re.sub(r"\[sound:[^\]]+\]", "🔊", s)
    return html.unescape(re.sub(r"<[^>]+>", " ", s)).strip()

# Row tints like Anki's browser: suspended = yellow, flagged = flag colour, marked = purple-ish, buried = grey.
BROWSE_ROW_CSS = ("tr.susp td{background:rgba(240,200,40,.16)}tr.marked td{background:rgba(142,68,236,.10)}"
                  "tr.buried td{background:rgba(128,128,128,.14)}") + "".join(
    f"tr.flag{n} td{{background:{c}22}}" for n, _, c in FLAGS[1:])
# Engine row colours (BrowserRow.Color) -> CSS class.
ROW_COLOR_CLASS = {1: "marked", 2: "susp", 10: "buried", **{2 + n: f"flag{n}" for n in range(1, 8)}}
# Columns shown until the user picks their own (Anki's defaults plus the scheduling numbers).
BROWSE_DEFAULT_COLS = {
    "cards": ["noteFld", "template", "deck", "cardDue", "cardIvl", "cardEase", "cardReps", "cardLapses"],
    "notes": ["noteFld", "note", "deck", "cardDue", "cardIvl", "cardEase", "cardReps", "cardLapses", "noteTags"],
}
BROWSE_PER_PAGE = 100
BROWSE_MODE_KEY = "flashcardsBrowseMode"   # our own key: this app defaults to notes mode

def browse_mode(col, mode=""):
    """Cards or notes mode. The engine's browser_row_for_id reads Anki's notes-mode flag, so keep it in sync."""
    if mode in ("cards", "notes"):
        if col.get_config(BROWSE_MODE_KEY, "notes") != mode:
            col.set_config(BROWSE_MODE_KEY, mode)
    else:
        mode = col.get_config(BROWSE_MODE_KEY, "notes")
    if col.get_config_bool(Config.Bool.BROWSER_TABLE_SHOW_NOTES_MODE) != (mode == "notes"):
        col.set_config_bool(Config.Bool.BROWSER_TABLE_SHOW_NOTES_MODE, mode == "notes")
    return mode

def browse_columns(col, notes):
    """Active columns for the mode, synced into the engine so browser_row_for_id uses them."""
    key = BrowserConfig.active_columns_key(notes)
    valid = {c.key for c in col.all_browser_columns()}
    cols = [c for c in (col.get_config(key, None) or []) if c in valid]
    if not cols:
        cols = BROWSE_DEFAULT_COLS["notes" if notes else "cards"]
        (col.set_browser_note_columns if notes else col.set_browser_card_columns)(cols)
    else:
        (col.load_browser_note_columns if notes else col.load_browser_card_columns)()
    return cols

def browse_search(col, q, notes, sort="", rev=None):
    """Run the search sorted like Anki's browser. Sort column/direction are kept in Anki's own config keys."""
    skey, rkey = BrowserConfig.sort_column_key(notes), BrowserConfig.sort_backwards_key(notes)
    if sort and col.get_browser_column(sort):
        if col.get_config(skey, None) != sort: col.set_config(skey, sort)
        if rev is not None and bool(col.get_config(rkey, False)) != rev: col.set_config(rkey, rev)
    sort = col.get_config(skey, "noteFld")
    column = col.get_browser_column(sort)
    sortable = column is not None and (column.sorting_notes if notes else column.sorting_cards)
    backwards = bool(col.get_config(rkey, False))
    find = col.find_notes if notes else col.find_cards
    ids = list(find(q, order=column if sortable else False, reverse=backwards))
    return ids, (sort if sortable else ""), backwards

def browse_url(q="", page_no=0, **extra):
    params = [("q", q)] + ([("page_no", page_no)] if page_no else []) + [(k, v) for k, v in extra.items() if v != ""]
    return "/browse?" + "&".join(f"{k}={quote(str(v), safe='')}" for k, v in params)

def note_state(col, cards):
    """State text, flag and row class for a note, summarising its cards."""
    if not cards: return "", 0, ""
    states = [card_state(c) for c in cards]
    nsusp = states.count("Suspended")
    text, cls = states[0], ""
    if nsusp == len(cards): text, cls = "Suspended", "susp"
    elif nsusp: text = f"{states[0] if states[0] != 'Suspended' else 'New'} · {nsusp} suspended"
    return text, next((c.user_flag() for c in cards if c.user_flag()), 0), cls

@app.get("/browse", response_class=HTMLResponse)
def browse(request: Request, q: str = "", page_no: int = 0, mode: str = "", sort: str = "", rev: str = ""):
    user = current_user(request)
    if not user: return need_login(request)
    per = BROWSE_PER_PAGE
    with collection(user) as col:
        mode = browse_mode(col, mode)
        notes = mode == "notes"
        cols = browse_columns(col, notes)
        all_cols = {c.key: c for c in col.all_browser_columns()}
        try:
            ids, sort, backwards = browse_search(col, q, notes, sort, (rev == "1") if rev in ("0", "1") else None)
            err = ""
        except Exception as e:
            ids, err, backwards = [], str(e), False
        total = len(ids)
        page_no = max(0, min(page_no, (total - 1) // per if total else 0))
        here = browse_url(q, page_no)
        qback = html.escape(quote(here, safe="/"))
        rows = []
        for id_ in ids[page_no * per:(page_no + 1) * per]:
            try:
                cells, color, _, _ = col.browser_row_for_id(id_)
                cells = [re.sub(r"[⁨⁩]", "", t) for t, _, _ in cells]
            except Exception:   # removed between search and render
                continue
            if notes:
                n = col.get_note(id_)
                cards = n.cards()
                state, flag, cls = note_state(col, cards)
                marked, nid, info_cid = n.has_tag("marked"), id_, (cards[0].id if cards else 0)
                if flag: cls += f" flag{flag}"
                elif marked: cls += " marked"
            else:
                card = col.get_card(id_)
                state, flag, nid, info_cid = card_state(card), card.user_flag(), card.nid, id_
                marked = "marked" in [t.lower() for t in card.note().tags]
                cls = ROW_COLOR_CLASS.get(color, "")
            tds = []
            for i, (key, text) in enumerate(zip(cols, cells)):
                right = all_cols[key].alignment == 1 if key in all_cols else False
                txt = html.escape(text[:120])
                if i == 0:
                    txt = f'<a class="deck" href="/edit/{nid}?back={qback}">{txt or "(empty)"}</a>'
                tds.append(f'<td class="{"num" if right else ""}">{txt}</td>')
            info = (f'<a class="info-link" title="Card info" aria-label="Card info" '
                    f'href="/card/{info_cid}/info?back={qback}">ⓘ</a>') if info_cid else ""
            rows.append(f"""<tr class="{cls.strip()}"><td class="sel"><input type="checkbox" name="ids" value="{id_}"
aria-label="Select"></td>{''.join(tds)}<td class="muted state">{flag_dot(flag)}{' <span title="Marked" style="color:#c98a16">★</span>' if marked else ''} {state}</td><td>{info}</td></tr>""")
        heads = []
        for key in cols:
            c = all_cols.get(key)
            label = (c.notes_mode_label if notes else c.cards_mode_label) if c else key
            can_sort = c is not None and (c.sorting_notes if notes else c.sorting_cards)
            if not can_sort:
                heads.append(f"<th>{html.escape(label)}</th>"); continue
            on = key == sort
            # The engine sorts each column its own natural way; "backwards" flips it.
            asc = (c.sorting_notes if notes else c.sorting_cards) == 1
            arrow = (" ▼" if asc == backwards else " ▲") if on else ""
            url = browse_url(q, 0, sort=key, rev=("0" if backwards else "1") if on else "0")
            heads.append(f'<th class="{"num" if c.alignment == 1 else ""}"><a class="sort{" on" if on else ""}" '
                         f'href="{html.escape(url)}">{html.escape(label)}{arrow}</a></th>')
        decks = sorted(col.decks.all_names_and_ids(include_filtered=False), key=lambda d: d.name)
        fields = sorted({f["name"] for m in col.models.all() for f in m["flds"]}, key=str.lower)
        col_opts = "".join(
            f'<label class="chk"><input type="checkbox" name="cols" value="{c.key}" {"checked" if c.key in cols else ""}>'
            f'{html.escape(c.notes_mode_label if notes else c.cards_mode_label)}</label>'
            for c in sorted(all_cols.values(), key=lambda c: (c.notes_mode_label if notes else c.cards_mode_label)))
    nav = ""
    if total > per:
        last = (total - 1) // per
        nav = '<div class="row">' + (f'<a class="btn" href="{html.escape(browse_url(q, page_no - 1))}">← Prev</a>' if page_no else "") \
            + f'<span class="muted">Page {page_no + 1} of {last + 1}</span>' \
            + (f'<a class="btn" href="{html.escape(browse_url(q, page_no + 1))}">Next →</a>' if page_no < last else "") + "</div>"
    unit = "notes" if notes else "cards"
    seg = "".join(f'<a class="btn{" primary" if m == mode else ""}" href="{html.escape(browse_url(q, 0, mode=m))}">{m.title()}</a>'
                  for m in ("cards", "notes"))
    deck_opts = "".join(f'<option value="{d.id}">{html.escape(d.name)}</option>' for d in decks)
    flag_opts = "".join(f'<option value="{n}">{t}</option>' for n, t, _ in FLAGS)
    field_opts = "".join(f'<option value="{html.escape(f)}">{html.escape(f)}</option>' for f in fields)
    eq = html.escape(q)
    body = f"""<style>main{{max-width:1240px}}{BROWSE_ROW_CSS}
.info-link{{display:inline-flex;align-items:center;justify-content:center;min-width:44px;min-height:44px;font-size:20px;
color:var(--muted);text-decoration:none;border-radius:10px}}.info-link:hover{{background:var(--bg)}}
.btable td,.btable th{{padding:8px 8px;font-size:15px;vertical-align:middle}}.btable td.num,.btable th.num{{text-align:right;
white-space:nowrap;font-variant-numeric:tabular-nums}}.btable .state{{font-size:14px;white-space:nowrap}}
.btable td.sel,.btable th.sel{{width:44px;padding:0 4px}}.sel input,.chk input{{width:22px;height:22px;margin:0;accent-color:var(--accent)}}
a.sort{{color:var(--muted);text-decoration:none}}a.sort.on{{color:var(--ink);font-weight:600}}
.seg{{gap:0}}.seg .btn{{border-radius:0}}.seg .btn:first-child{{border-radius:10px 0 0 10px}}.seg .btn:last-child{{border-radius:0 10px 10px 0}}
.chk{{display:flex;gap:8px;align-items:center;margin:0;padding:8px 6px;color:var(--ink);font-size:15px;min-height:44px}}
.colgrid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:0 12px}}
details.panel>summary{{cursor:pointer;list-style:none;display:inline-block}}details.panel>summary::-webkit-details-marker{{display:none}}
.bar{{position:sticky;top:62px;z-index:4}}.arg{{display:none}}.arg.show{{display:flex}}.bar select,.bar input[type=text],
.bar input[type=number]{{width:auto;min-height:44px}}</style>
<div class="box"><form class="row" action="/browse"><input name="q" value="{eq}" style="flex:1;min-width:220px"
placeholder="Search (Anki syntax: deck:French is:due tag:verb front:*cat*)"><button>Search</button></form>
<div class="row" style="margin-top:10px;justify-content:space-between">
<span class="muted">{total} {unit}{(' · ' + html.escape(err)) if err else ''}</span>
<div class="row"><div class="row seg" role="group" aria-label="Show">{seg}</div>
<a class="btn" href="/filtered/new?search={quote(q)}" title="Create a filtered deck from this search">Filtered deck</a>
<a class="btn" href="/export?q={quote(q)}" title="Export the notes in this search">Export</a>
<details class="panel"><summary class="btn">Columns ▾</summary></details></div></div>
<form id="colsform" method="post" action="/browse/columns" style="display:none;margin-top:10px">
<input type="hidden" name="mode" value="{mode}"><input type="hidden" name="back" value="{html.escape(here)}">
<div class="colgrid">{col_opts}</div><p class="row" style="margin-bottom:0"><button class="primary">Save columns</button>
<button name="reset" value="1">Default columns</button></p></form></div>
<form id="bulk" method="post" action="/browse/bulk">
<input type="hidden" name="mode" value="{mode}"><input type="hidden" name="q" value="{eq}">
<input type="hidden" name="back" value="{html.escape(here)}">
<div class="box bar"><div class="row">
<b id="nsel">0 selected</b>
<label class="chk" style="padding:0 6px"><input type="checkbox" name="all" value="1" id="allq">All {total} {unit} in this search</label>
<select name="op" id="op" aria-label="Action">
<option value="">Action…</option><option value="deck">Change deck</option><option value="add_tags">Add tags</option>
<option value="remove_tags">Remove tags</option><option value="suspend">Suspend</option><option value="unsuspend">Unsuspend</option>
<option value="flag">Flag</option><option value="due">Set due date</option><option value="reset">Reset to new</option>
<option value="reposition">Reposition new cards</option><option value="notetype">Change note type</option>
<option value="delete">Delete notes</option></select>
<span class="arg row" data-op="deck"><select name="deck" aria-label="Deck">{deck_opts}</select></span>
<span class="arg row" data-op="add_tags remove_tags"><input type="text" name="tags" placeholder="tags, space separated" aria-label="Tags"></span>
<span class="arg row" data-op="flag"><select name="flag" aria-label="Flag">{flag_opts}</select></span>
<span class="arg row" data-op="due"><input type="text" name="days" placeholder="days: 0, 1, 3-7, 5!" aria-label="Due in days" style="width:170px"></span>
<span class="arg row" data-op="reposition"><label class="chk" style="padding:0">Start <input type="number" name="start" value="0" min="0" style="width:90px;height:auto"></label>
<label class="chk" style="padding:0">Step <input type="number" name="step" value="1" min="1" style="width:80px;height:auto"></label>
<label class="chk"><input type="checkbox" name="randomize" value="1">Randomize</label>
<label class="chk"><input type="checkbox" name="shift" value="1">Shift existing</label></span>
<button class="primary" id="apply">Apply</button></div>
<details class="panel" style="margin-top:10px"><summary class="btn">Find and replace ▾</summary>
<div class="row" style="margin-top:10px"><input type="text" name="find" placeholder="Find" aria-label="Find" style="flex:1;min-width:160px">
<input type="text" name="replace" placeholder="Replace with" aria-label="Replace with" style="flex:1;min-width:160px">
<select name="field" aria-label="In"><option value="">All fields</option><option value="__tags__">Tags</option>{field_opts}</select>
<label class="chk"><input type="checkbox" name="regex" value="1">Regex</label>
<label class="chk"><input type="checkbox" name="match_case" value="1">Match case</label>
<button name="do_replace" value="1">Replace</button></div>
<p class="muted" style="font-size:14px;margin-bottom:0">Works on the selected notes (or every note in the search when “All” is ticked).</p></details></div>
<div class="box" style="overflow-x:auto;padding:8px"><table class="btable"><tr><th class="sel"><input type="checkbox" id="selall"
aria-label="Select all on this page"></th>{''.join(heads)}<th>State</th><th></th></tr>{''.join(rows)}</table></div></form>{nav}
<script>
const bulk=document.getElementById('bulk'),op=document.getElementById('op'),allq=document.getElementById('allq');
const boxes=()=>[...bulk.querySelectorAll('input[name=ids]')];
function count(){{const n=allq.checked?{total}:boxes().filter(b=>b.checked).length;
 document.getElementById('nsel').textContent=n+' selected';}}
document.getElementById('selall').onchange=e=>{{boxes().forEach(b=>b.checked=e.target.checked);count();}};
bulk.addEventListener('change',e=>{{if(e.target.name==='ids'||e.target===allq)count();}});
op.onchange=()=>document.querySelectorAll('.arg').forEach(a=>a.classList.toggle('show',a.dataset.op.split(' ').includes(op.value)));
bulk.onsubmit=e=>{{const n=allq.checked?{total}:boxes().filter(b=>b.checked).length;
 const rep=e.submitter&&e.submitter.name==='do_replace';
 if(!n){{e.preventDefault();alert('Select some {unit} first (tick the boxes, or “All”).');return;}}
 if(!rep&&!op.value){{e.preventDefault();alert('Choose an action.');return;}}
 if(!rep&&op.value==='delete'&&!confirm('Delete the notes of the '+n+' selected {unit}? This removes all their cards.'))e.preventDefault();
 if(!rep&&op.value==='reset'&&!confirm('Reset '+n+' {unit} to new? Review history is kept.'))e.preventDefault();}};
bulk.querySelectorAll('input[name=find],input[name=replace]').forEach(i=>i.addEventListener('keydown',e=>{{
 if(e.key==='Enter'){{e.preventDefault();bulk.querySelector('button[name=do_replace]').click();}}}}));
const cp=document.querySelector('details.panel');cp.ontoggle=()=>document.getElementById('colsform').style.display=cp.open?'block':'none';
</script>"""
    return page("Browse", body, user, "browse", take_flash(request))

def safe_back(back, default="/browse"):
    return back if back.startswith("/") and not back.startswith("//") else default

@app.post("/browse/columns")
async def browse_set_columns(request: Request):
    user = current_user(request)
    if not user: return need_login(request)
    form = await request.form()
    notes = form.get("mode") == "notes"
    with collection(user) as col:
        valid = {c.key for c in col.all_browser_columns()}
        cols = [c for c in form.getlist("cols") if c in valid]
        if form.get("reset") or not cols:
            cols = BROWSE_DEFAULT_COLS["notes" if notes else "cards"]
        (col.set_browser_note_columns if notes else col.set_browser_card_columns)(cols)
    set_flash(request, "Columns saved.")
    return RedirectResponse(safe_back(str(form.get("back", ""))), 303)

def bulk_targets(col, form):
    """(card ids, note ids) the bulk action applies to: the ticked rows, or the whole search."""
    notes = form.get("mode") == "notes"
    if form.get("all"):
        q = str(form.get("q", ""))
        ids = list(col.find_notes(q) if notes else col.find_cards(q))
    else:
        ids = [int(i) for i in form.getlist("ids") if str(i).isdigit()]
    if not ids: return [], []
    if notes:
        nids = col.db.list(f"select id from notes where id in {ids2str(ids)}")
        cids = col.db.list(f"select id from cards where nid in {ids2str(nids)}")
    else:
        cids = col.db.list(f"select id from cards where id in {ids2str(ids)}")
        nids = col.db.list(f"select distinct nid from cards where id in {ids2str(cids)}")
    return cids, nids

def bulk_action(col, form, cids, nids):
    """Run one of the browser's bulk actions through the engine; returns a short message."""
    plural = lambda n, w: f"{n} {w}{'' if n == 1 else 's'}"
    if form.get("do_replace"):
        find, repl = str(form.get("find", "")), str(form.get("replace", ""))
        if not find: raise ValueError("Enter the text to find.")
        regex, case = bool(form.get("regex")), bool(form.get("match_case"))
        field = str(form.get("field", ""))
        if regex:
            try: re.compile(find)
            except re.error as e: raise ValueError(f"Bad regular expression: {e}")
        if field == "__tags__":
            n = col.tags.find_and_replace(nids, find, repl, regex, case).count
        else:
            n = col.find_and_replace(note_ids=nids, search=find, replacement=repl, regex=regex,
                                     field_name=field or None, match_case=case).count
        return f"{plural(n, 'note')} updated."
    op = str(form.get("op", ""))
    if op == "deck":
        did = int(form.get("deck", 0) or 0)
        if not col.decks.get(did, default=False) or col.decks.is_filtered(did):
            raise ValueError("Choose a normal deck.")
        col.set_deck(cids, did)
        return f"Moved {plural(len(cids), 'card')} to {col.decks.name(did)}."
    if op in ("add_tags", "remove_tags"):
        tags = " ".join(str(form.get("tags", "")).split())
        if not tags: raise ValueError("Enter one or more tags.")
        if op == "add_tags":
            n = col.tags.bulk_add(nids, tags).count; return f"Tags added to {plural(n, 'note')}."
        n = col.tags.bulk_remove(nids, tags).count; return f"Tags removed from {plural(n, 'note')}."
    if op == "suspend":
        col.sched.suspend_cards(cids); return f"{plural(len(cids), 'card')} suspended."
    if op == "unsuspend":
        col.sched.unsuspend_cards(cids); return f"{plural(len(cids), 'card')} unsuspended."
    if op == "flag":
        flag = int(form.get("flag", 0) or 0)
        if not 0 <= flag <= 7: raise ValueError("Unknown flag.")
        col.set_user_flag_for_cards(flag, cids)
        return f"Flag {FLAG_NAME[flag].lower()} set on {plural(len(cids), 'card')}." if flag else "Flags removed."
    if op == "due":
        days = str(form.get("days", "")).strip().replace(" ", "")
        if not re.fullmatch(r"\d+(-\d+)?!?", days):
            raise ValueError("Due date: enter days like 0 (today), 1, 3-7, or 5! to also set the interval.")
        col.sched.set_due_date(cids, days)
        return f"Due date set for {plural(len(cids), 'card')}."
    if op == "reset":
        col.sched.schedule_cards_as_new(cids, restore_position=True, reset_counts=False)
        return f"{plural(len(cids), 'card')} reset to new."
    if op == "reposition":
        new = col.db.list(f"select id from cards where type=0 and id in {ids2str(cids)} order by due, ord")
        if not new: raise ValueError("Only new cards can be repositioned.")
        start, step = int(form.get("start", 0) or 0), int(form.get("step", 1) or 1)
        if start < 0 or step < 1: raise ValueError("Start must be 0 or more and step 1 or more.")
        n = col.sched.reposition_new_cards(new, starting_from=start, step_size=step,
                                           randomize=bool(form.get("randomize")),
                                           shift_existing=bool(form.get("shift"))).count
        return f"{plural(n, 'new card')} repositioned."
    if op == "delete":
        n = col.remove_notes(nids).count
        return f"{plural(n, 'note')} deleted."
    raise ValueError("Choose an action.")

@app.post("/browse/bulk")
async def browse_bulk(request: Request):
    user = current_user(request)
    if not user: return need_login(request)
    form = await request.form()
    back = safe_back(str(form.get("back", "")))
    with collection(user) as col:
        try:
            cids, nids = bulk_targets(col, form)
            if not cids and not nids: raise ValueError("Nothing selected.")
            if form.get("op") == "notetype" and not form.get("do_replace"):
                # Anki's Change Note Type dialog: its own page, which comes back here afterwards.
                sel = f"q={quote(str(form.get('q', '')))}" if form.get("all") else f"nids={','.join(map(str, nids))}"
                return RedirectResponse(f"/notetypes/change?{sel}&back={quote(back)}", 303)
            msg = bulk_action(col, form, cids, nids)
        except Exception as e:
            msg = "!" + (str(e) or "That did not work.")
    set_flash(request, msg)
    return RedirectResponse(back, 303)

# ---------- settings ----------
@app.get("/settings", response_class=HTMLResponse)
def settings(request: Request):
    user = current_user(request)
    if not user: return need_login(request)
    with collection(user) as col:
        fsrs = col.get_config("fsrs", True)
    users = load_users()
    body = f"""<div class="box row" style="justify-content:space-between"><h3 style="margin:0">Note types</h3>
<a class="btn" href="/notetypes">Manage note types</a></div>
<div class="box row" style="justify-content:space-between"><div><h3 style="margin:0">Media</h3>
<span class="muted">Find missing and unused pictures and sounds.</span></div>
<a class="btn" href="/check-media">Check media</a></div>
<div class="box"><h3 style="margin-top:0">Scheduler</h3><form method="post" action="/settings/fsrs" class="row">
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

# ---------- route modules ----------
from flashcards import decks as deck_list  # noqa: E402
app.include_router(deck_list.router)
from flashcards import deck_options  # noqa: E402  (imports helpers from this module)
app.include_router(deck_options.router)
from flashcards import custom_study  # noqa: E402
app.include_router(custom_study.router)
from flashcards import filtered_decks  # noqa: E402
app.include_router(filtered_decks.router)
from flashcards import notetypes  # noqa: E402
app.include_router(notetypes.router)
from flashcards import editor  # noqa: E402
app.include_router(editor.router)
from flashcards import image_occlusion  # noqa: E402
app.include_router(image_occlusion.router)
from flashcards import import_export  # noqa: E402
app.include_router(import_export.router)
from flashcards import media as media_check  # noqa: E402
app.include_router(media_check.router)
from flashcards import stats as stats_page  # noqa: E402
app.include_router(stats_page.router)
