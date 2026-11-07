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
from flashcards import i18n
from flashcards.i18n import t, tn, N_

DATA = os.environ.get("DATA_DIR", os.path.join(os.path.dirname(__file__), "data"))
os.makedirs(DATA, exist_ok=True)
USERS_FILE = os.path.join(DATA, "users.json")
SECRET_FILE = os.path.join(DATA, "secret.key")
if not os.path.exists(SECRET_FILE):
    open(SECRET_FILE, "w").write(secrets.token_hex(32))

app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key=open(SECRET_FILE).read(),
                   max_age=60 * 60 * 24 * 90, same_site="lax")
app.add_middleware(i18n.LangMiddleware)  # language per request (flashcards/i18n)

# ---------- users ----------
def load_users():
    return json.load(open(USERS_FILE)) if os.path.exists(USERS_FILE) else {}

def hash_pw(pw, salt):
    return hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), 200_000).hex()

def save_users(users):
    tmp = USERS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(users, f)
    os.replace(tmp, USERS_FILE)

def create_user(name, pw):
    users = load_users()
    salt = secrets.token_hex(16)
    users[name] = dict(users.get(name, {}), salt=salt, hash=hash_pw(pw, salt))  # keeps the language
    save_users(users)

# Interface language per person (Preferences > Language). "" = follow the browser.
def user_lang(name):
    code = load_users().get(name, {}).get("lang", "")
    return code if code in i18n.LANGUAGES else ""

def set_user_lang(name, code):
    users = load_users()
    if name not in users:
        return
    if code in i18n.LANGUAGES:
        users[name]["lang"] = code
    else:
        users[name].pop("lang", None)
    save_users(users)

i18n.set_user_lang_lookup(user_lang)

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
            col = i18n.open_collection(os.path.join(d, "collection.anki2"))  # engine in the person's language
            if col.get_config("fsrs", None) is None:
                col.set_config("fsrs", True)
            _cols[user] = col
        maintenance.maybe_daily_backup(user, _cols[user])  # once a day; never raises
        yield _cols[user]

def reopen_collection(user):
    """Close a person's collection so the next request opens it again (after a language change,
    so the engine's own messages use the new language)."""
    with _glock:
        lock = _locks.setdefault(user, threading.Lock())
    with lock:
        col = _cols.pop(user, None)
        if col is not None:
            try:
                col.close()
            except Exception:
                pass

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
:root{color-scheme:light dark}:root.light{color-scheme:light}:root.dark{color-scheme:dark}
@media (prefers-color-scheme:dark){:root:not(.light){--bg:#161617;--card:#232325;--ink:#f2f2f2;--muted:#a1a1a6;--line:#38383b}}
:root.dark{--bg:#161617;--card:#232325;--ink:#f2f2f2;--muted:#a1a1a6;--line:#38383b}
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
form.fc-ur{margin:0}form.fc-ur button{min-width:44px;padding:8px 10px;font-size:20px;line-height:1;border-color:transparent;background:none}
form.fc-ur button:hover{background:var(--bg)}
#fc-toast{position:fixed;left:50%;top:70px;transform:translateX(-50%);background:var(--ink);color:var(--bg);max-width:90vw;
padding:10px 18px;border-radius:10px;opacity:0;transition:opacity .2s;pointer-events:none;z-index:30}#fc-toast.on{opacity:1}
"""

# Theme (Anki's Preferences > Appearance > Theme). Kept in the collection's config so it
# follows the user to every device. "system" follows the device's light/dark setting.
THEMES = ("system", "light", "dark")
THEME_KEY = "flashcardsTheme"

def theme_of(col):
    theme = col.get_config(THEME_KEY, "system")
    return theme if theme in THEMES else "system"

def user_theme(user):
    """Theme for page(). Read without the per-user lock, like the undo status (page() is
    often called while the request holds it; config reads are read-only engine calls)."""
    col = _cols.get(user) if user else None
    if col is None:
        return "system"
    try:
        return theme_of(col)
    except Exception:
        return "system"

def page(title, body, user=None, active="", flash=""):
    nav = ""
    if user:
        items = [("decks", "/", t("Decks")), ("add", "/add", t("Add")), ("browse", "/browse", t("Browse")),
                 ("import", "/import", t("Import")), ("stats", "/stats", t("Stats")),
                 ("settings", "/settings", t("Settings"))]
        nav = "".join(f'<a href="{u}" class="{"on" if k == active else ""}">{label}</a>' for k, u, label in items)
        nav += undo_mod.header_buttons(user) + f'<a href="/logout" class="muted">{t("Log out")}</a>'
    fl = ""
    if flash:
        cls = "flash err" if flash.startswith("!") else "flash"
        fl = f'<div class="{cls}">{html.escape(flash.lstrip("!"))}</div>'
    theme = user_theme(user)
    root = f' class="{theme}"' if theme in ("light", "dark") else ""
    lang = i18n.current()
    root += "" if lang == "en" else f' lang="{lang}"'
    return HTMLResponse(f"""<!doctype html><html{root}><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes"><meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="Flashcards"><link rel="manifest" href="/manifest.json">
{touch_mod.head_html(theme)}<title>{html.escape(title)}</title><style>{CSS}{touch_mod.CSS}</style></head><body>
<header><b>Flashcards</b>{nav}</header><main>{fl}{body}</main>{undo_mod.SCRIPT + sync_mod.live_script(user) if user else ""}
{touch_mod.BODY_SCRIPT}</body></html>""")

def take_flash(request):
    return request.session.pop("flash", "")

def set_flash(request, msg):
    request.session["flash"] = msg

def h(s):
    """Escape translated text for an HTML text node (quotes are left alone)."""
    return html.escape(s, quote=False)

def ht(template, **markup):
    """A translated sentence with HTML in its placeholders: escape the text, then put the markup in.
    Call as ht(t("Ready in {time}."), time="<b>...</b>") (t() without arguments leaves {time} in)."""
    s = h(template)
    try:
        return s.format(**markup)
    except (KeyError, IndexError, ValueError):
        return s

# Manifest, icons, offline page and HTML error pages: flashcards/touch.py

# ---------- auth ----------
@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    first = not load_users()
    title = t("Create the first account") if first else t("Log in")
    button = t("Create account") if first else t("Log in")
    return page(title, f"""<div class="box" style="max-width:420px;margin:40px auto">
<h2 style="margin-top:0">{h(title)}</h2><form method="post">
<label>{h(t("Username"))}</label><input name="username" autocapitalize="none" autocomplete="username" required>
<label>{h(t("Password"))}</label><input name="password" type="password" autocomplete="current-password" required>
<p><button class="primary" style="width:100%">{h(button)}</button></p></form></div>""",
                flash=take_flash(request))

@app.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...)):
    username = username.strip().lower()
    if not re.fullmatch(r"[a-z0-9_.-]{2,32}", username):
        set_flash(request, "!" + t("Username: 2-32 letters, numbers, dot, dash or underscore."))
        return RedirectResponse("/login", 303)
    if not load_users():
        create_user(username, password)
    if not check_user(username, password):
        set_flash(request, "!" + t("Wrong username or password."))
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
    for tag in tags:
        if isinstance(tag, SoundOrVideoTag):
            video = tag.filename.lower().endswith(VIDEO_EXTS)
            out.append({"t": "video" if video else "sound", "src": "/media/" + quote(tag.filename)})
        else:   # TTSTag: spoken by the browser's speech synthesis
            out.append({"t": "tts", "text": tag.field_text, "lang": tag.lang.replace("_", "-"),
                        "speed": tag.speed or 1.0})
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
        return (f'<a class="replay-button soundLink" href="#" title="{html.escape(t("Play"))}" '
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
        warn = (h(t("Please run Tools>Empty Cards")) if info["cloze"]
                else h(t("Type answer: unknown field {field}", field=info["field"])))
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

CARD_DARK_CSS = """body{background:#232325;color:#f2f2f2}.card{background:transparent!important;color:#f2f2f2!important}
input#typeans{background:#161617;color:#f2f2f2;border-color:#555}.replay-button circle{fill:#333;stroke:#777}
.replay-button path{fill:#ddd}img.latex{filter:invert(100%)}"""
# Like Anki, cards in dark mode get the night_mode / nightMode classes so card CSS can style them.
NIGHT_JS = """<script>if(matchMedia('(prefers-color-scheme:dark)').matches){
document.documentElement.classList.add('night_mode');document.documentElement.classList.add('nightMode');}</script>"""

def card_doc(inner, theme="system"):
    """A card side as a full page for the study/preview iframe. theme: system, light or dark."""
    mj = MATHJAX if MATHJAX_PAT.search(inner) else ""
    if "anki.imageOcclusion" in inner:   # Image Occlusion cards: draw the masks (flashcards/io_review.py)
        mj += IO_REVIEW_JS
    if theme == "dark":
        dark, cls, head = CARD_DARK_CSS, "night_mode nightMode", '<meta name="color-scheme" content="dark">'
    elif theme == "light":
        dark, cls, head = "", "", '<meta name="color-scheme" content="light">'
    else:
        dark, cls, head = "@media (prefers-color-scheme:dark){" + CARD_DARK_CSS + "}", "", NIGHT_JS
    return f"""<!doctype html><html class="{cls}"><head><meta charset="utf-8">{head}
<meta name="viewport" content="width=device-width,initial-scale=1"><base href="/media/">
<style>html,body{{margin:0;padding:0}}body{{padding:20px;font-family:-apple-system,system-ui,sans-serif}}
img{{max-width:100%;height:auto}}.cloze{{font-weight:bold;color:#2f6fde}}
.replay-button{{display:inline-block;vertical-align:middle;margin:4px}}.replay-button svg{{display:block}}
#typeans{{width:100%;box-sizing:border-box;padding:10px 12px;border:1px solid #bbb;border-radius:10px}}
code#typeans{{display:inline-block;width:auto;border:0;padding:0;font-family:inherit}}
.typeGood{{background-color:#afa;color:black}}.typeBad{{background-color:#faa;color:black}}
.typeMissed{{background-color:#ccc;color:black}}
img.latex{{vertical-align:middle}}
{dark}
</style>{mj}</head><body class="card">{inner}</body></html>"""

# ---------- card actions (suspend, bury, flag, mark, reset, set due date) ----------
# Names are marked with N_ and translated where shown: t(FLAG_NAME[n]).
FLAGS = [(0, N_("No flag"), "transparent"), (1, N_("Red"), "#e5484d"), (2, N_("Orange"), "#f08c00"),
         (3, N_("Green"), "#2f9e44"), (4, N_("Blue"), "#2f6fde"), (5, N_("Pink"), "#e64980"),
         (6, N_("Turquoise"), "#12b5b0"), (7, N_("Purple"), "#8e44ec")]
FLAG_COLOR = {n: c for n, _, c in FLAGS}
FLAG_NAME = {n: name for n, name, _ in FLAGS}
# Undo labels the engine gives these actions; the study screen offers undo for them.
STUDY_UNDOABLE = ("answer", "suspend", "bury", "flag", "tag", "reset", "due date", "forget")

CARD_STATES = {0: N_("New"), 1: N_("Learning"), 2: N_("Review"), 3: N_("Learning")}

def card_state(card):
    """English state name (a key: show it with t(card_state(card)))."""
    if card.queue == -1: return N_("Suspended")
    if card.queue in (-2, -3): return N_("Buried")
    return CARD_STATES.get(card.type if card.queue == 4 else card.queue, "")

def card_action(col, card, action, arg=""):
    """Run one Anki card action through the engine; returns a short message."""
    cid, nid = card.id, card.nid
    note_cids = [c for c in col.card_ids_of_note(nid)]
    if action == "suspend":
        col.sched.suspend_cards([cid]); return t("Card suspended.")
    if action == "suspend_note":
        col.sched.suspend_cards(note_cids); return t("Note suspended.")
    if action == "unsuspend":
        col.sched.unsuspend_cards([cid]); return t("Card unsuspended.")
    if action == "bury":
        col.sched.bury_cards([cid]); return t("Card buried.")
    if action == "bury_note":
        col.sched.bury_notes([nid]); return t("Note buried.")
    if action == "unbury":
        col.sched.unbury_cards([cid]); return t("Card unburied.")
    if action == "flag":
        flag = int(arg or 0)
        if not 0 <= flag <= 7: raise ValueError(t("Unknown flag."))
        if flag and card.user_flag() == flag:   # pressing the same flag again clears it, as in Anki
            flag = 0
        col.set_user_flag_for_cards(flag, [cid])
        return t("Flag: {flag}.", flag=t(FLAG_NAME[flag])) if flag else t("Flag removed.")
    if action == "mark":
        if col.get_note(nid).has_tag("marked"):
            col.tags.bulk_remove([nid], "marked"); return t("Note unmarked.")
        col.tags.bulk_add([nid], "marked"); return t("Note marked.")
    if action == "reset":
        col.sched.schedule_cards_as_new([cid], restore_position=True, reset_counts=False)
        return t("Card reset to new.")
    if action == "due":
        days = (arg or "").strip().replace(" ", "")
        if not re.fullmatch(r"\d+(-\d+)?!?", days):
            raise ValueError(t("Due date: enter days like 0 (today), 1, 3-7, or 5! to also set the interval."))
        col.sched.set_due_date([cid], days)
        return t("Due date set.")
    raise ValueError(t("Unknown action."))

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
            msg, good, info = str(e) or t("That did not work."), False, {}
    if ajax:
        return {"ok": good, "msg": msg, "stamp": sync_mod.stamp(user), **info}
    set_flash(request, msg if good else "!" + msg)
    return RedirectResponse(back, 303)

def flag_dot(flag, size=12):
    if not flag: return ""
    return (f'<span title="{html.escape(t("{flag} flag", flag=t(FLAG_NAME[flag])))}" style="display:inline-block;width:{size}px;height:{size}px;'
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
            return _span_text(unit, v)
    v = round(secs) if secs >= 1 else round(secs, 1)
    return _span_text("second", v)

def _span_text(unit, v):
    """'3 days', '2.1 months' in the current language (fractions take their own wording)."""
    if isinstance(v, float):
        if unit == "year": return t("{n} years", n=v)
        if unit == "month": return t("{n} months", n=v)
        return t("{n} seconds", n=v)
    if unit == "year": return tn("{n} year", "{n} years", v)
    if unit == "month": return tn("{n} month", "{n} months", v)
    if unit == "day": return tn("{n} day", "{n} days", v)
    if unit == "hour": return tn("{n} hour", "{n} hours", v)
    if unit == "minute": return tn("{n} minute", "{n} minutes", v)
    return tn("{n} second", "{n} seconds", v)

def fmt_date(ts, with_time=False):
    return time.strftime("%Y-%m-%d %H:%M" if with_time else "%Y-%m-%d", time.localtime(ts))

# Labels are marked with N_ and translated where shown.
REVIEW_KIND = {0: (N_("Learn"), "lrn"), 1: (N_("Review"), "rev"), 2: (N_("Relearn"), "lrn"),
               3: (N_("Filtered"), "new"), 4: (N_("Manual"), "muted"), 5: (N_("Rescheduled"), "muted")}
RATING = {0: ("—", "muted"), 1: (N_("Again"), "again"), 2: (N_("Hard"), "hard"), 3: (N_("Good"), "good"),
          4: (N_("Easy"), "easy")}

def card_info_html(col, cid):
    """Card statistics from the engine's card_stats call, laid out like Anki's Card Info."""
    s = col.card_stats_data(cid)
    card = col.get_card(cid)
    rows = [(t("Added"), fmt_date(s.added))]
    if s.HasField("first_review"): rows.append((t("First review"), fmt_date(s.first_review)))
    if s.HasField("latest_review"): rows.append((t("Latest review"), fmt_date(s.latest_review)))
    if s.interval: rows.append((t("Interval"), fmt_span(s.interval * 86400)))
    fsrs = bool(col.get_config("fsrs", False)) and s.HasField("memory_state")
    if fsrs:
        rows.append((t("Stability"), fmt_span(s.memory_state.stability * 86400)))
        rows.append((t("Difficulty"), f"{round((s.memory_state.difficulty - 1) / 9 * 100)}%"))
        if s.HasField("fsrs_retrievability"):
            rows.append((t("Retrievability"), f"{round(s.fsrs_retrievability * 100)}%"))
    elif s.ease:
        rows.append((t("Ease"), f"{s.ease // 10}%"))
    def paused(value):
        if card.queue == -1: return t("{value} (suspended)", value=value)
        if card.queue in (-2, -3): return t("{value} (buried)", value=value)
        return value
    if s.HasField("due_date"):
        rows.append((t("Due"), paused(fmt_date(s.due_date))))
    elif s.HasField("due_position"):
        rows.append((t("Position"), paused(str(s.due_position))))
    rows += [(t("Reviews"), str(s.reviews)), (t("Lapses"), str(s.lapses))]
    if s.reviews:
        rows += [(t("Average time"), fmt_span(s.average_secs)), (t("Total time"), fmt_span(s.total_secs))]
    deck = (t("{deck} (from {original})", deck=s.deck, original=s.original_deck)
            if s.HasField("original_deck") else s.deck)
    rows += [(t("Card type"), s.card_type), (t("Note type"), s.notetype), (t("Deck"), deck),
             (t("Preset"), s.preset), (t("Card ID"), str(s.card_id)), (t("Note ID"), str(s.note_id))]
    if s.custom_data: rows.append((t("Custom data"), s.custom_data))
    stats = "".join(f'<tr><th>{h(k)}</th><td>{html.escape(v)}</td></tr>' for k, v in rows)
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
<td class="{kcls}">{h(t(kind))}</td><td style="color:var(--{rcls})">{h(t(rating))}</td><td>{ivl}</td>{extra}
<td>{fmt_span(e.taken_secs) if e.taken_secs else '—'}</td></tr>""")
    cols = (f"<th>{h(t('Stability'))}</th><th>{h(t('Difficulty'))}</th>" if has_mem
            else f"<th>{h(t('Ease'))}</th>")
    history = (f"""<div style="overflow-x:auto"><table class="hist"><tr><th>{h(t("Date"))}</th><th>{h(t("Type"))}</th><th>{h(t("Rating"))}</th>
<th>{h(t("Interval"))}</th>{cols}<th>{h(t("Time"))}</th></tr>{''.join(hist)}</table></div>""" if hist
               else f'<p class="muted">{h(t("This card has not been reviewed yet."))}</p>')
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
            set_flash(request, "!" + t("That card was not found."))
            return RedirectResponse("/browse", 303)
        stats, history, nid = card_info_html(col, cid)
        title = strip_html(card.question())[:80]
    back_btn = f'<a class="btn" href="{html.escape(back)}">{h(t("← Back"))}</a>' if back else ""
    edit_back = quote(f"/card/{cid}/info" + (f"?back={quote(back, safe='/')}" if back else ""), safe="/")
    body = f"""<style>.info th{{width:40%;font-size:15px}}.info td,.info th{{padding:9px 8px}}
.hist td,.hist th{{padding:9px 8px;font-size:15px}}</style>
<div class="row" style="justify-content:space-between;margin-bottom:12px">{back_btn}
<a class="btn" href="/edit/{nid}?back={edit_back}">{h(t("Edit note"))}</a></div>
<div class="box"><h2 style="margin-top:0">{h(t("Card info"))}</h2>
<p class="muted" style="margin-top:-6px">{html.escape(title) or h(t("(empty)"))}</p>
<table class="info">{stats}</table></div>
<div class="box"><h3 style="margin-top:0">{h(t("Review history"))}</h3>{history}</div>"""
    return page(t("Card info"), body, user, "browse", take_flash(request))

def js(value):
    """JSON for inline <script>: no '<' so card HTML or URL text can't close the script tag."""
    return json.dumps(value).replace("<", "\\u003c")

def study_undo_label(col):
    label = col.undo_status().undo
    return label if any(k in (label or "").lower() for k in STUDY_UNDOABLE) else ""

def fmt_wait(secs):
    mins = max(1, round(secs / 60))
    return tn("{n} minute", "{n} minutes", mins) if mins < 60 else fmt_span(secs)

def study_undo_button(did, undo_label):
    if not undo_label:
        return ""
    return (f'<form method="post" action="/study/{did}/undo"><button>'
            f'{html.escape(t("Undo {action}", action=undo_label))}</button></form>')

def study_waiting(request, user, did, secs, undo_label, flash):
    """Anki's 'learning cards due later' state: nothing to show yet, but cards answered
    Again come back in this session once their step is up, so wait and reload."""
    undo = study_undo_button(did, undo_label)
    wait = ht(t("The next learning card will be ready in {time}."), time=f'<b id="wait">{h(fmt_wait(secs))}</b>')
    body = f"""<div class="row" style="justify-content:space-between;margin-bottom:10px">
<a href="/deck/{did}" class="btn">{h(t("\u2190 Deck"))}</a>{undo}</div>
<div class="box" style="text-align:center"><h2 style="margin-top:0">{h(t("Nothing due right now"))}</h2>
<p>{wait}</p>
<p class="muted">{h(t("Keep this screen open and it will carry on by itself."))}</p>
<a class="btn primary" href="/study/{did}">{h(t("Check again"))}</a></div>
<script>setTimeout(()=>location.replace('/study/{did}'),{(int(secs) + 1) * 1000});</script>"""
    return page(t("Study"), body, user, "decks", flash)

# Timebox (Preferences > Timebox time limit), like Anki's collection.startTimebox/timeboxReached,
# but kept per browser session: one collection is shared by all of a user's devices.
def timebox_clock():
    return time.time()

def timebox_start(request, did, force=False):
    tb = request.session.get("timebox")
    if force or not isinstance(tb, dict) or tb.get("did") != did:
        request.session["timebox"] = {"did": did, "start": timebox_clock(), "n": 0}

def timebox_answered(request, col, did):
    """Count an answer; once the limit has passed, return Anki's message and start again."""
    tb = request.session.get("timebox")
    if not isinstance(tb, dict) or tb.get("did") != did:
        timebox_start(request, did, True)
        tb = request.session["timebox"]
    tb = dict(tb, n=int(tb.get("n", 0)) + 1)
    request.session["timebox"] = tb
    limit = col.get_preferences().reviewing.time_limit_secs
    elapsed = timebox_clock() - float(tb.get("start", 0))
    if not limit or elapsed < limit:
        return ""
    timebox_start(request, did, True)
    mins = max(1, round(elapsed / 60))
    msg = f"{col.tr.studying_card_studied_in(count=tb['n'])} {col.tr.studying_minute(count=mins)}"
    return re.sub(r"[⁨⁩]", "", msg)

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
            done = t("Congratulations! You have finished this deck for now.")
            set_flash(request, f"{flash.lstrip('!')} {done}" if flash else done)
            return RedirectResponse(f"/deck/{did}", 303)
        qc = q.cards[0]
        card = col.get_card(qc.card.id)
        labels = [re.sub(r'[\u2068\u2069]', '', l) for l in i18n.describe_next_states(col, qc.states)]
        qtext = card.question()
        tinfo = type_answer_info(col, card, qtext)
        question = render_side(col, card, type_question(tinfo, addons.card_render(col, card, "question", qtext)))
        answer = render_side(col, card, type_answer(tinfo, addons.card_render(col, card, "answer", card.answer())))
        has_type = bool(tinfo and tinfo["expected"])
        counts = (q.new_count, q.learning_count, q.review_count)
        kind = {0: "new", 1: "lrn", 2: "rev", 3: "lrn"}.get(card.queue, "rev")
        undo_label = study_undo_label(col)
        flag, marked = card.user_flag(), card.note().has_tag("marked")
        conf = col.decks.config_dict_for_deck_id(card.current_deck_id())
        av = {"q": av_list(card.question_av_tags()), "a": av_list(card.answer_av_tags()),
              "autoplay": bool(conf.get("autoplay", True)), "replayq": bool(conf.get("replayq", True))}
        rprefs = col.get_preferences().reviewing
        show_counts, show_times = rprefs.show_remaining_due_counts, rprefs.show_intervals_on_buttons
        theme = theme_of(col)
        tap = touch_mod.tap_zones(col)
        wb = whiteboard.settings(col)
    timebox_start(request, did)
    # Coming back from Edit or Card info: same card, same side, timer carries on.
    resume = cid == card.id
    start_shown = resume and bool(shown)
    elapsed = max(0, min(ms, 3_600_000)) if resume else 0
    qdoc, adoc = js(card_doc(question, theme)), js(card_doc(answer, theme))
    names = [t("Again"), t("Hard"), t("Good"), t("Easy")]
    colors = ["again", "hard", "good", "easy"]
    btns = "".join(f"""<button class="ans" style="border-color:var(--{c});color:var(--{c})"
onclick="answer({i + 1})">{f'<small>{html.escape(labels[i])}</small><br>' if show_times else ''}{h(n)}</button>"""
                   for i, (n, c) in enumerate(zip(names, colors)))
    undo = study_undo_button(did, undo_label)
    flag_btns = "".join(f"""<button type="button" class="fl" onclick="act('flag',{n})" title="Ctrl+{n}">
<span class="dot" style="background:{c}"></span>{h(t(name))}</button>""" for n, name, c in FLAGS[1:])
    menu = f"""<details class="more"><summary class="btn">{h(t("More ▾"))}</summary><div class="menu">
<button type="button" onclick="act('mark')">{h(t("★ Mark note"))} <kbd>*</kbd></button>
<button type="button" onclick="act('bury')">{h(t("Bury card"))} <kbd>-</kbd></button>
<button type="button" onclick="act('bury_note')">{h(t("Bury note"))} <kbd>=</kbd></button>
<button type="button" onclick="act('suspend')">{h(t("Suspend card"))} <kbd>@</kbd></button>
<button type="button" onclick="act('suspend_note')">{h(t("Suspend note"))} <kbd>!</kbd></button>
<button type="button" onclick="setDue()">{h(t("Set due date…"))} <kbd>Ctrl+Shift+D</kbd></button>
<button type="button" onclick="resetCard()">{h(t("Reset to new…"))} <kbd>Ctrl+Alt+N</kbd></button>
<button type="button" onclick="info()">{h(t("Card info"))} <kbd>I</kbd></button>
<button type="button" onclick="replay()">{h(t("Replay audio"))} <kbd>R</kbd></button>
<div class="flags">{flag_btns}</div></div></details>"""
    has_audio = any(x["t"] != "video" for x in av["q"] + av["a"])
    replay_btn = (f'<button type="button" id="replay" onclick="replay()" title="{html.escape(t("Replay audio (R)"))}" '
                  f'aria-label="{html.escape(t("Replay audio"))}">🔊</button>' if has_audio else "")
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
#toast{{position:fixed;left:50%;bottom:calc(110px + env(safe-area-inset-bottom));transform:translateX(-50%);
background:var(--ink);color:var(--bg);
padding:10px 18px;border-radius:10px;opacity:0;transition:opacity .2s;pointer-events:none;z-index:20}}
html,body{{overscroll-behavior:none}}.bar{{padding-bottom:max(12px,env(safe-area-inset-bottom))}}
.ans,#show{{-webkit-user-select:none;user-select:none;-webkit-touch-callout:none}}
#taphint{{font-size:13px;text-align:center;margin:0 0 6px}}{whiteboard.CSS}</style>
<div class="row" style="justify-content:space-between;margin-bottom:10px">
<a href="/deck/{did}" class="btn">{h(t("← Deck"))}</a>
<div class="counts"><span id="flag" class="dot" title="{html.escape(t("{flag} flag", flag=t(FLAG_NAME[flag])))}"
style="background:{FLAG_COLOR[flag]};{'' if flag else 'display:none'}"></span>
<span id="mark" title="{html.escape(t("Marked"))}" style="{'' if marked else 'display:none'}">★</span>
{f'''<span class="new {'cur' if kind == 'new' else ''}">{counts[0]}</span>
<span class="lrn {'cur' if kind == 'lrn' else ''}">{counts[1]}</span>
<span class="rev {'cur' if kind == 'rev' else ''}">{counts[2]}</span>''' if show_counts else ''}</div>
<div class="row">{undo}{replay_btn}{whiteboard.toggle_html(wb)}<a class="btn" href="/edit/{card.nid}?back=/study/{did}"
onclick="edit();return false;">{h(t("Edit"))}</a>{menu}</div></div>
<div id="toast"></div>{whiteboard.bar_html(wb)}
<div id="wbwrap"><iframe id="frame" sandbox="allow-same-origin allow-scripts"></iframe><canvas id="wb" style="display:none"></canvas></div>
<form id="f" method="post" action="/study/{did}/answer"><input type="hidden" name="card_id" value="{card.id}">
<input type="hidden" name="rating" id="rating"><input type="hidden" name="ms" id="ms"></form>
<div class="bar">{f'<p id="taphint" class="muted">{h(t("Tap the card to show the answer"))}</p>' if tap else ''}<button id="show" class="primary" style="width:100%;min-height:64px" onclick="show()">{h(t("Show answer"))}</button>
<div id="btns" class="row" style="display:none;flex-wrap:nowrap">{btns}</div></div>
<script>
const Q={qdoc},A={adoc},t0=Date.now()-{elapsed},fr=document.getElementById('frame');let shown=false,busy=false,quiet={js(resume)};
const AV={js(av)},HAS_TYPE={js(has_type)},SLOT={js(TYPE_SLOT)},TAP={js(tap)};let typed={js(typed[:1000])},sent=false,shownAt=0;
function fit(){{try{{const h=fr.contentDocument.documentElement.scrollHeight;fr.style.height=Math.max(h+10,window.innerHeight*.55)+'px'}}catch(e){{}}}}
function load(d){{fr.srcdoc=d;}} fr.addEventListener('fc-resize',fit);fr.onload=()=>{{fit();setTimeout(fit,300);
 fr.contentDocument.addEventListener('keydown',key);
 fr.contentDocument.addEventListener('keydown',e=>window.fcUndoKey&&window.fcUndoKey(e));
 const ti=fr.contentDocument.getElementById('typeans');if(ti&&ti.tagName==='INPUT'){{ti.value=typed;ti.focus();}}
 if(TAP)tapZones(fr.contentDocument);}};
// ---- optional tap zones: a tap shows the answer, then left half = Again, right half = Good.
// Only a short, still tap counts: scrolls, swipes, long presses and taps on links/inputs don't.
function tapZones(doc){{let sx=0,sy=0,st=0,on=false;
 doc.addEventListener('pointerdown',e=>{{on=e.isPrimary&&(e.pointerType!=='mouse'||e.button===0);
  sx=e.clientX;sy=e.clientY;st=Date.now();}},true);
 doc.addEventListener('pointercancel',()=>{{on=false;}},true);
 doc.addEventListener('pointerup',e=>{{if(!on)return;on=false;
  if(Math.abs(e.clientX-sx)>10||Math.abs(e.clientY-sy)>10||Date.now()-st>600)return;
  const t=e.target;
  if(t&&t.closest&&t.closest('a,button,input,textarea,select,label,audio,video,summary,[contenteditable],[onclick]'))return;
  const sel=doc.getSelection&&doc.getSelection();if(sel&&!sel.isCollapsed)return;
  if(!shown){{show();return;}}
  if(Date.now()-shownAt<400)return;  // a quick double tap must not show and answer at once
  answer(e.clientX<doc.documentElement.clientWidth/2?1:3);}},true);}}
// ---- a tap on the whiteboard goes to the card: its links/buttons/inputs, else the tap zones
function wbTap(x,y){{const d=fr.contentDocument;if(!d)return;x-=fr.clientLeft;y-=fr.clientTop;
 const el=d.elementFromPoint(x,y),c=el&&el.closest&&el.closest('a,button,input,textarea,select,label,audio,video,summary,[contenteditable],[onclick]');
 if(c){{if(c.matches('input,textarea,select,[contenteditable]'))c.focus();
  else if(c.matches('audio,video'))c.paused?c.play().catch(()=>{{}}):c.pause();else c.click();return;}}
 if(!TAP)return;if(!shown){{show();return;}}
 if(Date.now()-shownAt<400)return;answer(x<d.documentElement.clientWidth/2?1:3);}}
// ---- audio: a small queue player, like Anki's (sound files and TTS) ----
let playing=[],cur=null;
function stopAudio(){{playing=[];if(cur&&cur.pause)cur.pause();cur=null;
 if(window.speechSynthesis)speechSynthesis.cancel();}}
function playList(list){{stopAudio();playing=list.filter(x=>x.t!=='video');next();}}
function next(){{const it=playing.shift();if(!it){{cur=null;return;}}
 if(it.t==='tts'){{if(!window.speechSynthesis){{next();return;}}const u=new SpeechSynthesisUtterance(it.text);
  if(it.lang)u.lang=it.lang;u.rate=it.speed||1;u.onend=next;u.onerror=next;cur=null;speechSynthesis.speak(u);return;}}
 cur=new Audio(it.src);cur.onended=next;cur.onerror=next;
 cur.play().catch(e=>{{if(e.name==='NotAllowedError'){{playing=[];toast({js(t("Tap 🔊 to play the audio"))});}}else next();}});}}
function fcPlay(side,i){{const it=AV[side][i];if(it)playList([it]);}}
function sideTags(){{return shown?(AV.replayq?AV.q.concat(AV.a):AV.a):AV.q;}}
function replay(){{document.querySelector('.more').open=false;playList(sideTags());}}
async function show(){{if(shown||busy)return;busy=true;let d=A;
 if(HAS_TYPE){{try{{const ti=fr.contentDocument.getElementById('typeans');if(ti&&ti.tagName==='INPUT')typed=ti.value;}}catch(e){{}}
  let out='';try{{const fd=new FormData();fd.append('card_id','{card.id}');fd.append('typed',typed);
   out=(await(await fetch('/study/{did}/typeans',{{method:'POST',body:fd}})).json()).html;}}catch(e){{}}
  d=A.replace(SLOT,()=>out);}}
 shown=true;shownAt=Date.now();busy=false;load(d);
 const th=document.getElementById('taphint');if(th)th.textContent={js(t("Tap the card: left half Again · right half Good"))};document.getElementById('show').style.display='none';
 document.getElementById('btns').style.display='flex';if(AV.autoplay&&!quiet)playList(AV.a);quiet=false;}}
function answer(r){{if(!shown||sent)return;sent=true;stopAudio();document.getElementById('rating').value=r;
 document.getElementById('ms').value=Date.now()-t0;document.getElementById('f').submit();}}
function here(){{if(!shown){{try{{const ti=fr.contentDocument.getElementById('typeans');if(ti&&ti.tagName==='INPUT')typed=ti.value;}}catch(e){{}}}}
 return '?cid={card.id}'+(shown?'&shown=1':'')+'&ms='+(Date.now()-t0)+(typed?'&typed='+encodeURIComponent(typed):'');}}
function edit(){{stopAudio();location.href='/edit/{card.nid}?back=/study/{did}'+encodeURIComponent(here());}}
const FC={json.dumps(FLAG_COLOR)};
function toast(m){{const t=document.getElementById('toast');t.textContent=m;t.style.opacity=1;
 clearTimeout(t._h);t._h=setTimeout(()=>t.style.opacity=0,1600);}}
async function act(a,arg){{document.querySelector('.more').open=false;
 const fd=new FormData();fd.append('action',a);fd.append('arg',arg??'');
 let r;try{{r=await(await fetch('/card/{card.id}/action',
  {{method:'POST',body:fd,headers:{{'X-Requested-With':'fetch'}}}})).json();}}
 catch(e){{toast({js(t("Couldn't reach the server. Check the connection and try again."))});return;}}
 toast(r.msg);if(!r.ok)return;
 if(['flag','mark'].includes(a)){{if(r.stamp&&window.fcStampSeen)fcStampSeen(r.stamp);const f=document.getElementById('flag');f.style.display=r.flag?'':'none';
  f.style.background=FC[r.flag];document.getElementById('mark').style.display=r.marked?'':'none';}}
 else setTimeout(()=>location.replace('/study/{did}'),500);}}
function setDue(){{const d=prompt({js(t("Show this card in how many days? (0 = today, 3-7 = random range, add ! to also set the interval)"))},'1');
 if(d!==null&&d.trim())act('due',d.trim());}}
function info(){{stopAudio();location.href='/card/{card.id}/info?back=/study/{did}'+encodeURIComponent(here());}}
function resetCard(){{if(confirm({js(t("Reset this card to new? Its review history is kept."))}))act('reset');}}
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
window.FC_AFTER_UNDO='/study/{did}';
window.addEventListener('pageshow',e=>{{if(e.persisted)sent=false;}});
</script>{whiteboard.study_script(wb)}"""
    return page(t("Study"), body, user, "decks")

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
            addons.after_answer(col, card, rating)   # enabled add-ons (flashcards/addons.py); never raises
            msgs = []
            if leech:   # same notice as Anki's reviewer
                card.load()
                msgs.append(t("Card was a leech. It has been suspended.") if card.queue < 0
                            else t("Card was a leech."))
            tb = timebox_answered(request, col, did)
            if tb:
                msgs.append(tb)
            if msgs:
                set_flash(request, " ".join(msgs))
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
        oc = f' onsubmit="return confirm({html.escape(js(confirm))})"' if confirm else ""
        return (f'<form method="post" action="/card/{cid}/action"{oc}><input type="hidden" name="action" value="{action}">'
                f'<input type="hidden" name="arg" value="{arg}"><input type="hidden" name="back" value="{eb}">'
                f'<button class="{cls}">{h(label)}</button></form>')
    rows = []
    for card in note.cards():
        tmpl = card.template()["name"]
        flag = card.user_flag()
        state = t(card_state(card)) if card_state(card) else ""
        due = ""
        if card.queue == 2 or (card.queue in (-1, -2, -3) and card.type == 2):
            days = card.due - col.sched.today
            due = (t("due today") if days == 0 else
                   (t("due in {n} d", n=days) if days > 0 else t("overdue {n} d", n=-days)))
        flag_opts = "".join(f'<option value="{n}" {"selected" if n == flag else ""}>{h(t(name))}</option>'
                            for n, name, _ in FLAGS)
        susp = (btn(card.id, "unsuspend", t("Unsuspend")) if card.queue == -1
                else btn(card.id, "suspend", t("Suspend")))
        bury = btn(card.id, "unbury", t("Unbury")) if card.queue in (-2, -3) else btn(card.id, "bury", t("Bury"))
        rows.append(f"""<div class="cardrow" style="border-left:6px solid {FLAG_COLOR[flag] if flag else 'var(--line)'}">
<div class="row" style="justify-content:space-between"><b>{html.escape(tmpl)}</b>
<span class="muted" style="font-size:14px">{flag_dot(flag)} {h(state)}{' · ' + h(due) if due else ''}
· <a href="/browse?q=cid:{card.id}" class="muted">{html.escape(col.decks.name(card.did))}</a></span></div>
<div class="row" style="margin-top:8px">{susp}{bury}
<form method="post" action="/card/{card.id}/action" class="row" style="gap:6px"><input type="hidden" name="action" value="flag">
<input type="hidden" name="back" value="{eb}"><select name="arg" aria-label="{html.escape(t("Flag"))}" style="width:auto"
onchange="this.form.submit()">{flag_opts}</select></form>
<form method="post" action="/card/{card.id}/action" class="row" style="gap:6px"><input type="hidden" name="action" value="due">
<input type="hidden" name="back" value="{eb}"><input name="arg" placeholder="{html.escape(t("days, e.g. 1 or 3-7"))}" style="width:150px"
aria-label="{html.escape(t("Due in days"))}" required><button>{h(t("Set due"))}</button></form>
{btn(card.id, "reset", t("Reset to new"), "danger", confirm=t("Reset this card to new?"))}
<a class="btn" href="/card/{card.id}/info?back={html.escape(quote(back, safe='/'))}">{h(t("Card info"))}</a></div></div>""")
    first = note.cards()[0].id if note.cards() else 0
    mark = btn(first, "mark", t("★ Unmark note") if marked else t("☆ Mark note")) if first else ""
    return f"""<style>.cardrow{{padding:12px 14px;border:1px solid var(--line);border-radius:12px;margin-top:10px}}</style>
<div class="box"><div class="row" style="justify-content:space-between"><h3 style="margin:0">{h(t("Cards"))}</h3>{mark}</div>
{''.join(rows)}</div>"""

@app.post("/note/{nid}/delete")
def note_delete(request: Request, nid: int, back: str = "/browse"):
    user = current_user(request)
    if not user: return need_login(request)
    with collection(user) as col:
        col.remove_notes([nid])
    set_flash(request, t("Note deleted."))
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
    text, cls = (t(states[0]) if states[0] else ""), ""
    if nsusp == len(cards): text, cls = t("Suspended"), "susp"
    elif nsusp: text = t("{state} · {n} suspended", state=t(states[0] if states[0] != "Suspended" else "New"), n=nsusp)
    return text, next((c.user_flag() for c in cards if c.user_flag()), 0), cls

@app.get("/browse", response_class=HTMLResponse)
def browse(request: Request, q: str = "", page_no: int = 0, mode: str = "", sort: str = "", rev: str = "",
           add: str = "", join: str = ""):
    user = current_user(request)
    if not user: return need_login(request)
    per = BROWSE_PER_PAGE
    with collection(user) as col:
        if add:   # Ctrl/Shift-click in the sidebar: AND/OR the item onto the current search
            try:
                return RedirectResponse(browse_url(sidebar_mod.joined(col, q, add, join)), 303)
            except Exception:
                return RedirectResponse(browse_url(add), 303)
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
        empty, info_label, select_label = h(t("(empty)")), html.escape(t("Card info")), html.escape(t("Select"))
        marked_star = f' <span title="{html.escape(t("Marked"))}" style="color:#c98a16">★</span>'
        rows = []
        for id_ in ids[page_no * per:(page_no + 1) * per]:
            try:
                cells, color, _, _ = col.browser_row_for_id(id_)
                cells = [re.sub(r"[⁨⁩]", "", text) for text, _, _ in cells]
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
                state = t(state) if state else ""
                marked = "marked" in [tag.lower() for tag in card.note().tags]
                cls = ROW_COLOR_CLASS.get(color, "")
            tds = []
            for i, (key, text) in enumerate(zip(cols, cells)):
                right = all_cols[key].alignment == 1 if key in all_cols else False
                txt = html.escape(text[:120])
                if i == 0:
                    txt = f'<a class="deck" href="/edit/{nid}?back={qback}">{txt or empty}</a>'
                tds.append(f'<td class="{"num" if right else ""}">{txt}</td>')
            info = (f'<a class="info-link" title="{info_label}" aria-label="{info_label}" '
                    f'href="/card/{info_cid}/info?back={qback}">ⓘ</a>') if info_cid else ""
            rows.append(f"""<tr class="{cls.strip()}"><td class="sel"><input type="checkbox" name="ids" value="{id_}"
aria-label="{select_label}"></td>{''.join(tds)}<td class="muted state">{flag_dot(flag)}{marked_star if marked else ''} {h(state)}</td><td>{info}</td></tr>""")
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
        side = sidebar_mod.sidebar_html(col, q)
    nav = ""
    if total > per:
        last = (total - 1) // per
        nav = '<div class="row">' + (f'<a class="btn" href="{html.escape(browse_url(q, page_no - 1))}">{h(t("← Prev"))}</a>' if page_no else "") \
            + f'<span class="muted">{h(t("Page {page} of {pages}", page=page_no + 1, pages=last + 1))}</span>' \
            + (f'<a class="btn" href="{html.escape(browse_url(q, page_no + 1))}">{h(t("Next →"))}</a>' if page_no < last else "") + "</div>"
    # Texts that name the rows: notes in notes mode, cards in cards mode (whole sentences for translators).
    # English has always said "1 notes" here, so both English forms are the same (tests compare it).
    if notes:
        count_text = tn("{n} notes", "{n} notes", total)
        all_text = tn("All {n} notes in this search", "All {n} notes in this search", total)
        js_none = t("Select some notes first (tick the boxes, or “All”).")
        js_delete = t("Delete the notes of the {n} selected notes? This removes all their cards.")
        js_reset = t("Reset {n} notes to new? Review history is kept.")
    else:
        count_text = tn("{n} cards", "{n} cards", total)
        all_text = tn("All {n} cards in this search", "All {n} cards in this search", total)
        js_none = t("Select some cards first (tick the boxes, or “All”).")
        js_delete = t("Delete the notes of the {n} selected cards? This removes all their cards.")
        js_reset = t("Reset {n} cards to new? Review history is kept.")
    seg_labels = {"cards": t("Cards"), "notes": t("Notes")}
    seg = "".join(f'<a class="btn{" primary" if m == mode else ""}" href="{html.escape(browse_url(q, 0, mode=m))}">{h(seg_labels[m])}</a>'
                  for m in ("cards", "notes"))
    deck_opts = "".join(f'<option value="{d.id}">{html.escape(d.name)}</option>' for d in decks)
    flag_opts = "".join(f'<option value="{n}">{h(t(name))}</option>' for n, name, _ in FLAGS)
    field_opts = "".join(f'<option value="{html.escape(f)}">{html.escape(f)}</option>' for f in fields)
    eq = html.escape(q)
    body = f"""<style>{BROWSE_ROW_CSS}
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
.bar{{position:sticky;top:calc(65px + env(safe-area-inset-top));z-index:4}}.arg{{display:none}}.arg.show{{display:flex}}.bar select,.bar input[type=text],
.bar input[type=number]{{width:auto;min-height:44px}}</style>
<div class="box"><form class="row" action="/browse">{sidebar_mod.OPEN_BUTTON}<input name="q" value="{eq}" style="flex:1;min-width:220px"
placeholder="{html.escape(t("Search (Anki syntax: deck:French is:due tag:verb front:*cat*)"))}"><button>{h(t("Search"))}</button></form>
<div class="row" style="margin-top:10px;justify-content:space-between">
<span class="muted">{h(count_text)}{(' · ' + html.escape(err)) if err else ''}</span>
<div class="row"><div class="row seg" role="group" aria-label="{html.escape(t("Show"))}">{seg}</div>
<a class="btn" href="/filtered/new?search={quote(q)}" title="{html.escape(t("Create a filtered deck from this search"))}">{h(t("Filtered deck"))}</a>
<a class="btn" href="/export?q={quote(q)}" title="{html.escape(t("Export the notes in this search"))}">{h(t("Export"))}</a>
<a class="btn" href="/browse/dupes?search={quote(q)}" title="{html.escape(t("Find notes with the same field"))}">{h(t("Find duplicates"))}</a>
<details class="panel"><summary class="btn">{h(t("Columns ▾"))}</summary></details></div></div>
<form id="colsform" method="post" action="/browse/columns" style="display:none;margin-top:10px">
<input type="hidden" name="mode" value="{mode}"><input type="hidden" name="back" value="{html.escape(here)}">
<div class="colgrid">{col_opts}</div><p class="row" style="margin-bottom:0"><button class="primary">{h(t("Save columns"))}</button>
<button name="reset" value="1">{h(t("Default columns"))}</button></p></form></div>
<form id="bulk" method="post" action="/browse/bulk">
<input type="hidden" name="mode" value="{mode}"><input type="hidden" name="q" value="{eq}">
<input type="hidden" name="back" value="{html.escape(here)}">
<div class="box bar"><div class="row">
<b id="nsel">{h(t("{n} selected", n=0))}</b>
<label class="chk" style="padding:0 6px"><input type="checkbox" name="all" value="1" id="allq">{h(all_text)}</label>
<select name="op" id="op" aria-label="{html.escape(t("Action"))}">
<option value="">{h(t("Action…"))}</option><option value="deck">{h(t("Change deck"))}</option><option value="add_tags">{h(t("Add tags"))}</option>
<option value="remove_tags">{h(t("Remove tags"))}</option><option value="suspend">{h(t("Suspend"))}</option><option value="unsuspend">{h(t("Unsuspend"))}</option>
<option value="flag">{h(t("Flag"))}</option><option value="due">{h(t("Set due date"))}</option><option value="reset">{h(t("Reset to new"))}</option>
<option value="reposition">{h(t("Reposition new cards"))}</option><option value="notetype">{h(t("Change note type"))}</option>
<option value="delete">{h(t("Delete notes"))}</option></select>
<span class="arg row" data-op="deck"><select name="deck" aria-label="{html.escape(t("Deck"))}">{deck_opts}</select></span>
<span class="arg row" data-op="add_tags remove_tags"><input type="text" name="tags" placeholder="{html.escape(t("tags, space separated"))}" aria-label="{html.escape(t("Tags"))}"></span>
<span class="arg row" data-op="flag"><select name="flag" aria-label="{html.escape(t("Flag"))}">{flag_opts}</select></span>
<span class="arg row" data-op="due"><input type="text" name="days" placeholder="{html.escape(t("days: 0, 1, 3-7, 5!"))}" aria-label="{html.escape(t("Due in days"))}" style="width:170px"></span>
<span class="arg row" data-op="reposition"><label class="chk" style="padding:0">{h(t("Start"))} <input type="number" name="start" value="0" min="0" style="width:90px;height:auto"></label>
<label class="chk" style="padding:0">{h(t("Step"))} <input type="number" name="step" value="1" min="1" style="width:80px;height:auto"></label>
<label class="chk"><input type="checkbox" name="randomize" value="1">{h(t("Randomize"))}</label>
<label class="chk"><input type="checkbox" name="shift" value="1">{h(t("Shift existing"))}</label></span>
<button class="primary" id="apply">{h(t("Apply"))}</button></div>
<details class="panel" style="margin-top:10px"><summary class="btn">{h(t("Find and replace ▾"))}</summary>
<div class="row" style="margin-top:10px"><input type="text" name="find" placeholder="{html.escape(t("Find"))}" aria-label="{html.escape(t("Find"))}" style="flex:1;min-width:160px">
<input type="text" name="replace" placeholder="{html.escape(t("Replace with"))}" aria-label="{html.escape(t("Replace with"))}" style="flex:1;min-width:160px">
<select name="field" aria-label="{html.escape(t("In"))}"><option value="">{h(t("All fields"))}</option><option value="__tags__">{h(t("Tags"))}</option>{field_opts}</select>
<label class="chk"><input type="checkbox" name="regex" value="1">{h(t("Regex"))}</label>
<label class="chk"><input type="checkbox" name="match_case" value="1">{h(t("Match case"))}</label>
<button name="do_replace" value="1">{h(t("Replace"))}</button></div>
<p class="muted" style="font-size:14px;margin-bottom:0">{h(t("Works on the selected notes (or every note in the search when “All” is ticked)."))}</p></details></div>
<div class="box" style="overflow-x:auto;padding:8px"><table class="btable"><tr><th class="sel"><input type="checkbox" id="selall"
aria-label="{html.escape(t("Select all on this page"))}"></th>{''.join(heads)}<th>{h(t("State"))}</th><th></th></tr>{''.join(rows)}</table></div></form>{nav}
<script>
const bulk=document.getElementById('bulk'),op=document.getElementById('op'),allq=document.getElementById('allq');
const boxes=()=>[...bulk.querySelectorAll('input[name=ids]')];
const MSG={js({"sel": t("{n} selected"), "none": js_none, "choose": t("Choose an action."),
              "del": js_delete, "reset": js_reset})};
function count(){{const n=allq.checked?{total}:boxes().filter(b=>b.checked).length;
 document.getElementById('nsel').textContent=MSG.sel.replace('{{n}}',n);}}
document.getElementById('selall').onchange=e=>{{boxes().forEach(b=>b.checked=e.target.checked);count();}};
bulk.addEventListener('change',e=>{{if(e.target.name==='ids'||e.target===allq)count();}});
op.onchange=()=>document.querySelectorAll('.arg').forEach(a=>a.classList.toggle('show',a.dataset.op.split(' ').includes(op.value)));
bulk.onsubmit=e=>{{const n=allq.checked?{total}:boxes().filter(b=>b.checked).length;
 const rep=e.submitter&&e.submitter.name==='do_replace';
 if(!n){{e.preventDefault();alert(MSG.none);return;}}
 if(!rep&&!op.value){{e.preventDefault();alert(MSG.choose);return;}}
 if(!rep&&op.value==='delete'&&!confirm(MSG.del.replace('{{n}}',n)))e.preventDefault();
 if(!rep&&op.value==='reset'&&!confirm(MSG.reset.replace('{{n}}',n)))e.preventDefault();}};
bulk.querySelectorAll('input[name=find],input[name=replace]').forEach(i=>i.addEventListener('keydown',e=>{{
 if(e.key==='Enter'){{e.preventDefault();bulk.querySelector('button[name=do_replace]').click();}}}}));
const cp=document.querySelector('details.panel');cp.ontoggle=()=>document.getElementById('colsform').style.display=cp.open?'block':'none';
</script>"""
    return page(t("Browse"), sidebar_mod.layout(side, body), user, "browse", take_flash(request))

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
    set_flash(request, t("Columns saved."))
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
    if form.get("do_replace"):
        find, repl = str(form.get("find", "")), str(form.get("replace", ""))
        if not find: raise ValueError(t("Enter the text to find."))
        regex, case = bool(form.get("regex")), bool(form.get("match_case"))
        field = str(form.get("field", ""))
        if regex:
            try: re.compile(find)
            except re.error as e: raise ValueError(t("Bad regular expression: {error}", error=str(e)))
        if field == "__tags__":
            n = col.tags.find_and_replace(nids, find, repl, regex, case).count
        else:
            n = col.find_and_replace(note_ids=nids, search=find, replacement=repl, regex=regex,
                                     field_name=field or None, match_case=case).count
        return tn("{n} note updated.", "{n} notes updated.", n)
    op = str(form.get("op", ""))
    nc = len(cids)
    if op == "deck":
        did = int(form.get("deck", 0) or 0)
        if not col.decks.get(did, default=False) or col.decks.is_filtered(did):
            raise ValueError(t("Choose a normal deck."))
        col.set_deck(cids, did)
        return tn("Moved {n} card to {deck}.", "Moved {n} cards to {deck}.", nc, deck=col.decks.name(did))
    if op in ("add_tags", "remove_tags"):
        tags = " ".join(str(form.get("tags", "")).split())
        if not tags: raise ValueError(t("Enter one or more tags."))
        if op == "add_tags":
            n = col.tags.bulk_add(nids, tags).count
            return tn("Tags added to {n} note.", "Tags added to {n} notes.", n)
        n = col.tags.bulk_remove(nids, tags).count
        return tn("Tags removed from {n} note.", "Tags removed from {n} notes.", n)
    if op == "suspend":
        col.sched.suspend_cards(cids); return tn("{n} card suspended.", "{n} cards suspended.", nc)
    if op == "unsuspend":
        col.sched.unsuspend_cards(cids); return tn("{n} card unsuspended.", "{n} cards unsuspended.", nc)
    if op == "flag":
        flag = int(form.get("flag", 0) or 0)
        if not 0 <= flag <= 7: raise ValueError(t("Unknown flag."))
        col.set_user_flag_for_cards(flag, cids)
        if not flag:
            return t("Flags removed.")
        return tn("Flag {flag} set on {n} card.", "Flag {flag} set on {n} cards.", nc,
                  flag=t(FLAG_NAME[flag]).lower())
    if op == "due":
        days = str(form.get("days", "")).strip().replace(" ", "")
        if not re.fullmatch(r"\d+(-\d+)?!?", days):
            raise ValueError(t("Due date: enter days like 0 (today), 1, 3-7, or 5! to also set the interval."))
        col.sched.set_due_date(cids, days)
        return tn("Due date set for {n} card.", "Due date set for {n} cards.", nc)
    if op == "reset":
        col.sched.schedule_cards_as_new(cids, restore_position=True, reset_counts=False)
        return tn("{n} card reset to new.", "{n} cards reset to new.", nc)
    if op == "reposition":
        new = col.db.list(f"select id from cards where type=0 and id in {ids2str(cids)} order by due, ord")
        if not new: raise ValueError(t("Only new cards can be repositioned."))
        start, step = int(form.get("start", 0) or 0), int(form.get("step", 1) or 1)
        if start < 0 or step < 1: raise ValueError(t("Start must be 0 or more and step 1 or more."))
        n = col.sched.reposition_new_cards(new, starting_from=start, step_size=step,
                                           randomize=bool(form.get("randomize")),
                                           shift_existing=bool(form.get("shift"))).count
        return tn("{n} new card repositioned.", "{n} new cards repositioned.", n)
    if op == "delete":
        n = col.remove_notes(nids).count
        return tn("{n} note deleted.", "{n} notes deleted.", n)
    raise ValueError(t("Choose an action."))

@app.post("/browse/bulk")
async def browse_bulk(request: Request):
    user = current_user(request)
    if not user: return need_login(request)
    form = await request.form()
    back = safe_back(str(form.get("back", "")))
    with collection(user) as col:
        try:
            cids, nids = bulk_targets(col, form)
            if not cids and not nids: raise ValueError(t("Nothing selected."))
            if form.get("op") == "notetype" and not form.get("do_replace"):
                # Anki's Change Note Type dialog: its own page, which comes back here afterwards.
                sel = f"q={quote(str(form.get('q', '')))}" if form.get("all") else f"nids={','.join(map(str, nids))}"
                return RedirectResponse(f"/notetypes/change?{sel}&back={quote(back)}", 303)
            msg = bulk_action(col, form, cids, nids)
        except Exception as e:
            msg = "!" + (str(e) or t("That did not work."))
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
    fsrs_state = f"<b>{h(t('on') if fsrs else t('off'))}</b>"
    body = f"""<div class="box row" style="justify-content:space-between"><div><h3 style="margin:0">{h(t("Sync"))}</h3>
<span class="muted">{h(t("Your devices always share the same cards. Sync with AnkiDroid, AnkiMobile or desktop Anki."))}</span></div>
<a class="btn" href="/sync">{h(t("Sync"))}</a></div>
<div class="box row" style="justify-content:space-between"><div><h3 style="margin:0">{h(t("Preferences"))}</h3>
<span class="muted">{h(t("Next day starts at, learn ahead, timebox, review screen, theme."))}</span></div>
<a class="btn" href="/preferences">{h(t("Preferences"))}</a></div>
<div class="box row" style="justify-content:space-between"><div><h3 style="margin:0">{h(t("Study reminders"))}</h3>
<span class="muted">{h(t("A daily notification on your iPad or Chromebook when cards are due."))}</span></div>
<a class="btn" href="/reminders">{h(t("Reminders"))}</a></div>
<div class="box row" style="justify-content:space-between"><div><h3 style="margin:0">{h(t("Add-ons"))}</h3>
<span class="muted">{h(t("Small server add-ons for this app. Desktop Anki add-ons don't run here."))}</span></div>
<a class="btn" href="/addons">{h(t("Add-ons"))}</a></div>
<div class="box row" style="justify-content:space-between"><h3 style="margin:0">{h(t("Note types"))}</h3>
<a class="btn" href="/notetypes">{h(t("Manage note types"))}</a></div>
<div class="box row" style="justify-content:space-between"><div><h3 style="margin:0">{h(t("Media"))}</h3>
<span class="muted">{h(t("Find missing and unused pictures and sounds."))}</span></div>
<a class="btn" href="/check-media">{h(t("Check media"))}</a></div>
<div class="box row" style="justify-content:space-between"><div><h3 style="margin:0">{h(t("Check database"))}</h3>
<span class="muted">{h(t("Find and fix problems in your collection."))}</span></div>
<a class="btn" href="/check-database">{h(t("Check database"))}</a></div>
<div class="box row" style="justify-content:space-between"><div><h3 style="margin:0">{h(t("Empty cards"))}</h3>
<span class="muted">{h(t("Find and delete cards with a blank front."))}</span></div>
<a class="btn" href="/empty-cards">{h(t("Empty cards"))}</a></div>
<div class="box row" style="justify-content:space-between"><div><h3 style="margin:0">{h(t("Backups"))}</h3>
<span class="muted">{html.escape(maintenance.latest_backup_line(user))}</span></div>
<a class="btn" href="/backups">{h(t("Backups and restore"))}</a></div>
<div class="box"><h3 style="margin-top:0">{h(t("Scheduler"))}</h3><form method="post" action="/settings/fsrs" class="row">
<span style="flex:1">{ht(t("FSRS scheduling (Anki's modern algorithm) is {state}."), state=fsrs_state)}</span>
<button>{h(t("Turn off (use SM-2)") if fsrs else t("Turn on"))}</button></form></div>
<div class="box"><h3 style="margin-top:0">{h(t("Change password"))}</h3><form method="post" action="/settings/password">
<label>{h(t("New password"))}</label><input name="pw" type="password" minlength="6" required><p><button>{h(t("Change"))}</button></p></form></div>
<div class="box"><h3 style="margin-top:0">{h(t("Add another person"))}</h3><p class="muted">{h(t("Each person gets their own separate cards."))}
{h(tn("{n} account(s) so far.", "{n} account(s) so far.", len(users)))}</p><form method="post" action="/settings/adduser">
<div class="row"><input name="username" placeholder="{html.escape(t("Username"))}" style="flex:1" autocapitalize="none" required>
<input name="pw" type="password" placeholder="{html.escape(t("Password"))}" minlength="6" style="flex:1" required><button>{h(t("Create"))}</button></div></form></div>"""
    return page(t("Settings"), body, user, "settings", take_flash(request))

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
    set_flash(request, t("Password changed."))
    return RedirectResponse("/settings", 303)

@app.post("/settings/adduser")
def add_user(request: Request, username: str = Form(...), pw: str = Form(...)):
    user = current_user(request)
    if not user: return need_login(request)
    username = username.strip().lower()
    if not re.fullmatch(r"[a-z0-9_.-]{2,32}", username) or username in load_users():
        set_flash(request, "!" + t("That username is invalid or taken."))
    else:
        create_user(username, pw)
        set_flash(request, t("Account '{name}' created.", name=username))
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
from flashcards import shared_decks  # noqa: E402
app.include_router(shared_decks.router)
from flashcards import media as media_check  # noqa: E402
app.include_router(media_check.router)
from flashcards import stats as stats_page  # noqa: E402
app.include_router(stats_page.router)
from flashcards import undo as undo_mod  # noqa: E402  (page() uses it for the header buttons)
app.include_router(undo_mod.router)
from flashcards import preferences as preferences_page  # noqa: E402
app.include_router(preferences_page.router)
from flashcards import maintenance  # noqa: E402  (collection() uses it for the daily backup)
app.include_router(maintenance.router)
from flashcards import sync as sync_mod  # noqa: E402  (page() adds its live-refresh script)
app.include_router(sync_mod.router)
from flashcards import touch as touch_mod  # noqa: E402  (page() adds its head tags; error pages)
app.include_router(touch_mod.router)
from flashcards import browser_sidebar as sidebar_mod  # noqa: E402  (browse() adds the sidebar)
app.include_router(sidebar_mod.router)
from flashcards import addons  # noqa: E402  (study screen calls its card_render/after_answer hooks)
app.include_router(addons.router)
app.mount("/addon", addons.Dispatcher())
from flashcards import whiteboard  # noqa: E402  (study screen's drawing layer)
app.include_router(whiteboard.router)
from flashcards import reminders  # noqa: E402  (daily study reminders by Web Push; scheduler thread)
app.include_router(reminders.router)
app.router.on_startup.append(reminders.start)
from flashcards import find_dupes  # noqa: E402  (Browse > Find duplicates)
app.include_router(find_dupes.router)
from flashcards import latex as latex_mod  # noqa: E402  (LaTeX images: safe generation, source fallback)
