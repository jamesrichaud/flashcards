"""Add-ons: small server-side Python add-ons for this web app (not desktop Anki add-ons).

An add-on is a Python file `<DATA>/addons/<id>.py` (or a folder `<DATA>/addons/<id>/` with an
`__init__.py`). New add-ons start disabled; the Add-ons page (`/addons`, linked from Settings)
turns them on and off for the whole server. The on/off list is kept in `<DATA>/addons.json`.

Disabled add-ons are never imported: the page reads their name and description with `ast`.
Everything an add-on may define is optional:

    NAME = "Shown on the Add-ons page"        # default: the file name
    DESCRIPTION = "One line about it"          # default: the module docstring's first line

    def card_render(text, card, kind, col):    # kind is "question" or "answer"
        return text                            # the card side's HTML, changed or not

    def after_answer(col, card, rating):       # rating 1 Again, 2 Hard, 3 Good, 4 Easy
        ...

    from fastapi import APIRouter
    router = APIRouter()                       # extra pages, served under /addon/<id>/...
    @router.get("/")
    def home(request): ...                     # use flashcards.addons.page()/user() helpers
    MENU = [("Open my page", "/")]             # links shown on the Add-ons page

    def deck_overview(col, did):               # extra HTML under the deck overview's counts
        return '<div class="box">...</div>'
    def study_page(col, card):                 # extra HTML/script at the end of the study screen
        return "<script>...</script>"          # (it may call the page's show(), answer(n), act(...))
    def browse_tools(col, mode):               # extra HTML in Browse's action bar; a button with
        return "..."                           # formaction="/addon/<id>/..." posts the selection

An add-on that fails (on import or in a hook) never stops studying: the error is logged,
shown on the Add-ons page, and the card/answer carries on as if the add-on wasn't there.
An add-on whose file changes is loaded again on the next request.
"""
import ast, html, importlib.util, json, os, re, sys, threading, traceback

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

import app as core
from flashcards.i18n import t, N_

router = APIRouter()

ID_PAT = re.compile(r"[A-Za-z0-9_-]{1,64}")
ANKI_DESKTOP_PAT = re.compile(r"^\s*(from|import)\s+aqt\b", re.M)
HOOKS = ("card_render", "after_answer", "deck_overview", "study_page", "browse_tools")
HOOK_LABELS = {"card_render": N_("card render"), "after_answer": N_("after answer"),   # shown on the Add-ons page
               "deck_overview": N_("deck overview"), "study_page": N_("study screen"), "browse_tools": N_("Browse")}


def addons_dir():
    return os.path.join(core.DATA, "addons")


def state_file():
    return os.path.join(core.DATA, "addons.json")


_lock = threading.RLock()
_loaded = {}    # id -> {"mtime": float, "module": module or None, "app": FastAPI or None}
_errors = {}    # id -> last error text


# ---------- discovery (no code is run) ----------
BUNDLED_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bundled_addons")


def search_dirs():
    """The server's own add-on folder first (so a local copy wins), then the add-ons shipped with the app."""
    return [addons_dir(), BUNDLED_DIR]


def source_path(aid):
    for d in search_dirs():
        for p in (os.path.join(d, aid + ".py"), os.path.join(d, aid, "__init__.py")):
            if os.path.isfile(p):
                return p
    return None


def mtime(path):
    """Newest change time of the add-on's file, or of any .py file in its folder."""
    if os.path.basename(path) != "__init__.py":
        return os.path.getmtime(path)
    folder = os.path.dirname(path)
    return max(os.path.getmtime(os.path.join(folder, f)) for f in os.listdir(folder) if f.endswith(".py"))


def addon_ids():
    out = set()
    for d in search_dirs():
        if not os.path.isdir(d):
            continue
        for f in os.listdir(d):
            aid = f[:-3] if f.endswith(".py") else f
            if ID_PAT.fullmatch(aid) and not aid.startswith(("_", ".")) and source_path(aid):
                out.add(aid)
    return sorted(out, key=str.lower)


def describe(aid):
    """Name, description and kind of an add-on, read without running it."""
    path = source_path(aid)
    info = {"id": aid, "name": aid, "description": "", "desktop": False, "broken": ""}
    folder = os.path.dirname(path) if path.endswith("__init__.py") else None
    try:
        src = open(path, encoding="utf-8").read()
    except (OSError, UnicodeDecodeError) as e:
        info["broken"] = t("Can't read the file: {error}", error=e)
        return info
    # Desktop Anki add-ons use Anki's Qt GUI (aqt) and usually ship a manifest.json/config.json.
    if ANKI_DESKTOP_PAT.search(src) or (folder and os.path.isfile(os.path.join(folder, "manifest.json"))):
        info["desktop"] = True
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        info["broken"] = t("Syntax error on line {line}: {error}", line=e.lineno, error=e.msg)
        return info
    doc = ast.get_docstring(tree)
    if doc:
        info["description"] = doc.strip().splitlines()[0]
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in ("NAME", "DESCRIPTION"):
                    info["name" if target.id == "NAME" else "description"] = node.value.value.strip()
    return info


# ---------- enabled list ----------
def enabled_ids():
    try:
        data = json.load(open(state_file()))
        return [a for a in data.get("enabled", []) if isinstance(a, str) and ID_PAT.fullmatch(a)]
    except (OSError, ValueError, AttributeError):
        return []


def set_enabled(aid, on):
    with _lock:
        ids = [a for a in enabled_ids() if a != aid]
        if on:
            ids.append(aid)
        tmp = state_file() + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"enabled": sorted(ids)}, f)
        os.replace(tmp, state_file())
        _loaded.pop(aid, None)
        _errors.pop(aid, None)
        sys.modules.pop(module_name(aid), None)


# ---------- loading ----------
def module_name(aid):
    return "fc_addon_" + aid.replace("-", "_")


def log_error(aid, where):
    text = traceback.format_exc()
    _errors[aid] = f"{where}: {text.strip().splitlines()[-1]}"
    print(f"[add-on {aid}] error in {where}:\n{text}", file=sys.stderr, flush=True)


def load(aid):
    """The add-on's module (imported once, again when its file changes), or None."""
    path = source_path(aid)
    if not path:
        return None
    try:
        mt = mtime(path)
    except (OSError, ValueError):
        return None
    with _lock:
        cur = _loaded.get(aid)
        if cur and cur["mtime"] == mt:
            return cur["module"]
        _errors.pop(aid, None)
        entry = {"mtime": mt, "module": None, "app": None}
        _loaded[aid] = entry
        if describe(aid)["desktop"]:
            _errors[aid] = t("This is a desktop Anki add-on; it can't run here.")
            return None
        name = module_name(aid)
        try:
            folder = os.path.dirname(path) if path.endswith("__init__.py") else None
            spec = importlib.util.spec_from_file_location(
                name, path, submodule_search_locations=[folder] if folder else None)
            mod = importlib.util.module_from_spec(spec)
            sys.modules[name] = mod
            spec.loader.exec_module(mod)
        except BaseException:
            sys.modules.pop(name, None)
            log_error(aid, "loading")
            return None
        entry["module"] = mod
        r = getattr(mod, "router", None)
        if r is not None:
            sub = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)
            try:
                sub.include_router(r, prefix=f"/addon/{aid}")
                entry["app"] = sub
            except Exception:
                log_error(aid, "router")
        return mod


def active():
    """Loaded modules of the enabled add-ons, in name order."""
    out = []
    for aid in sorted(enabled_ids(), key=str.lower):
        mod = load(aid)
        if mod is not None:
            out.append((aid, mod))
    return out


# ---------- hooks (called by the app) ----------
def card_render(col, card, kind, text):
    """Pass a card side's HTML through every enabled add-on's card_render."""
    for aid, mod in active():
        fn = getattr(mod, "card_render", None)
        if not callable(fn):
            continue
        try:
            out = fn(text, card, kind, col)
        except Exception:
            log_error(aid, "card_render")
            continue
        if isinstance(out, str):
            text = out
        elif out is not None:
            _errors[aid] = t("card_render: must return the card's HTML as text")
    return text


def after_answer(col, card, rating):
    for aid, mod in active():
        fn = getattr(mod, "after_answer", None)
        if callable(fn):
            try:
                fn(col, card, rating)
            except Exception:
                log_error(aid, "after_answer")


def html_hook(name, col, *args):
    """Join the HTML that enabled add-ons return from a page hook (deck_overview, study_page,
    browse_tools). A hook that fails or returns something other than text adds nothing."""
    out = []
    for aid, mod in active():
        fn = getattr(mod, name, None)
        if not callable(fn):
            continue
        try:
            got = fn(col, *args)
        except Exception:
            log_error(aid, name)
            continue
        if isinstance(got, str):
            out.append(got)
        elif got is not None:
            _errors[aid] = t("{hook}: must return HTML as text", hook=name)
    return "".join(out)


# ---------- helpers for add-on pages ----------
def user(request):
    """Logged-in user name (always set inside add-on pages)."""
    return core.current_user(request)


def page(request, title, body, flash=""):
    """A page in the app's own look, for add-on routes."""
    return core.page(title, body, user(request), "settings", flash)


def collection(request):
    """`with addons.collection(request) as col:` the user's collection, locked as usual."""
    return core.collection(user(request))


# ---------- add-on pages: /addon/<id>/... ----------
class Dispatcher:
    """ASGI app mounted at /addon: sends the request to the enabled add-on's own router.
    Runs inside the app's session middleware, so add-on pages see request.session."""

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return
        path = scope.get("path", "")
        m = re.match(r"/addon/([^/]+)(/.*)?$", path)
        aid = m.group(1) if m else ""
        sub = None
        if m and ID_PAT.fullmatch(aid) and aid in enabled_ids() and load(aid) is not None:
            sub = (_loaded.get(aid) or {}).get("app")
        if sub is None:
            raise HTTPException(404)    # the app's normal 404 page
        session = scope.get("session") or {}
        public = getattr(sys.modules.get(module_name(aid)), "PUBLIC_PATHS", ())
        rest = (m.group(2) or "/") if m else "/"
        if session.get("user") not in core.load_users() and rest not in public:
            await RedirectResponse("/login", 303)(scope, receive, send)
            return
        inner = dict(scope, root_path="", path=path, app=sub)
        inner.pop("router", None)
        inner.pop("route", None)
        inner.pop("endpoint", None)
        started = False

        async def track(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)
        try:
            await sub(inner, receive, track)
        except Exception:
            log_error(aid, f"page {path}")
            if started:     # the add-on's app already sent its own 500
                return
            text = html.escape(t("The add-on “{name}” failed. See the {link} for the error."), False)
            link = f'<a href="/addons">{html.escape(t("Add-ons page"), False)}</a>'
            text = text.replace("{name}", html.escape(aid)).replace("{link}", link)
            await HTMLResponse(core.page(t("Add-on error"), f'<div class="box"><p>{text}</p></div>',
                                         session.get("user")).body, 500)(scope, receive, send)


# ---------- the Add-ons page ----------
def note_html():
    e = lambda s: html.escape(s, False)
    text = e(t("Add-ons here are small Python files on the server, written for this web app.\nPut a file such as "
               "{file} in the {folder} folder inside the app's data folder,\nthen turn it on below. Add-ons run with "
               "the server's full rights and apply to everyone using this server,\nso only add files you trust. "
               "{desktop}: they are made for\nAnki's desktop window, which this app doesn't have."))
    desktop = "<b>" + e(t("Desktop Anki add-ons (from ankiweb.net) don't run here")) + "</b>"
    text = (text.replace("{file}", "<code>my_addon.py</code>").replace("{folder}", "<code>addons</code>")
            .replace("{desktop}", desktop))
    return f'<p class="muted">{text}</p>'


def addons_body():
    ids = addon_ids()
    on = set(enabled_ids())
    rows = []
    e = lambda s: html.escape(s, False)   # translated text in an HTML text node
    for aid in ids:
        info = describe(aid)
        enabled = aid in on
        mod = load(aid) if enabled else None
        err = _errors.get(aid) if enabled else ""
        status = []
        if info["desktop"]:
            status.append('<span class="danger">' + e(t("Desktop Anki add-on: can't run here.")) + '</span>')
        elif info["broken"]:
            status.append(f'<span class="danger">{html.escape(info["broken"])}</span>')
        elif err:
            status.append(f'<span class="danger">{html.escape(t("Error: {error}", error=err))}</span>')
        if mod is not None:
            hooks = [t(HOOK_LABELS[h]) for h in HOOKS if callable(getattr(mod, h, None))]
            if (_loaded.get(aid) or {}).get("app"):
                hooks.append(t("pages"))
            if hooks:
                status.append(f'<span class="muted">{e(t("Uses: {hooks}", hooks=", ".join(hooks)))}</span>')
            links = []
            for item in getattr(mod, "MENU", None) or []:
                try:
                    label, sub = item
                    sub = "/" + str(sub).lstrip("/")
                except (TypeError, ValueError):
                    continue
                links.append(f'<a class="btn" href="/addon/{aid}{html.escape(sub)}">{html.escape(t(str(label)))}</a>')
            if links:
                status.append('<span class="row">' + "".join(links) + "</span>")
        can_enable = enabled or not (info["desktop"] or info["broken"])
        btn = (f'<form method="post" action="/addons/{aid}/{"disable" if enabled else "enable"}" style="margin:0">'
               f'<button{" class=primary" if not enabled else ""}>{e(t("Turn off") if enabled else t("Turn on"))}</button></form>'
               if can_enable else "")
        desc = f'<br><span class="muted">{html.escape(t(info["description"]))}</span>' if info["description"] else ""
        state = f'<b class="rev">{e(t("On"))}</b>' if enabled else f'<span class="muted">{e(t("Off"))}</span>'
        rows.append(f"""<div class="box addon" id="addon-{aid}"><div class="row" style="justify-content:space-between">
<div style="flex:1;min-width:200px"><h3 style="margin:0">{html.escape(t(info["name"]))} · {state}</h3>
<span class="muted">{html.escape(aid)}</span>{desc}</div>{btn}</div>
{"".join(f'<p style="margin:10px 0 0">{s}</p>' for s in status)}</div>""")
    if not rows:
        rows.append(f'<div class="box"><p style="margin:0">{e(t("No add-ons installed."))}</p></div>')
    return f'<div class="box">{note_html()}</div>{"".join(rows)}'


@router.get("/addons", response_class=HTMLResponse)
def addons_page(request: Request):
    u = core.current_user(request)
    if not u: return core.need_login(request)
    return core.page(t("Add-ons"), addons_body(), u, "settings", core.take_flash(request))


@router.post("/addons/{aid}/{action}")
def addons_toggle(request: Request, aid: str, action: str):
    u = core.current_user(request)
    if not u: return core.need_login(request)
    if action not in ("enable", "disable") or not ID_PAT.fullmatch(aid) or aid not in addon_ids():
        core.set_flash(request, "!" + t("No such add-on."))
        return RedirectResponse("/addons", 303)
    info = describe(aid)
    if action == "enable":
        if info["desktop"] or info["broken"]:
            core.set_flash(request, "!" + t("“{name}” can't be turned on.", name=info["name"]))
            return RedirectResponse("/addons", 303)
        set_enabled(aid, True)
        load(aid)
        err = _errors.get(aid)
        core.set_flash(request, "!" + t("“{name}” is on, but failed to load: {error}", name=info["name"], error=err)
                       if err else t("“{name}” is on.", name=info["name"]))
    else:
        set_enabled(aid, False)
        core.set_flash(request, t("“{name}” is off.", name=info["name"]))
    return RedirectResponse("/addons", 303)
