"""Interface languages.

    from flashcards.i18n import t, tn
    t("Decks")                                  -> "Mazos" (Spanish)
    t("Added {n} cards to {deck}.", n=3, deck=name)
    tn("{n} card", "{n} cards", n)              -> plural form for the current language

Keys are the English text itself, so a missing translation falls back to English. Each language
has a JSON file next to this module (`en.json`, `es.json`, ...). A value is a string, or for `tn`
a list of plural forms in the order of that language's plural rule (see PLURAL below). Keys must be
string literals (not variables) so `extract.py` can find them; `tests/test_i18n.py` checks every
key used in the code is in every language file.

The language for a request: the person's choice in Preferences (kept in users.json, so it works
before their collection is open and on the login page after they log in), else the browser's
Accept-Language, else English. `LangMiddleware` remembers the request; the language is worked out
the first time it is needed.

The engine's own words (`col.tr.*`) follow the same language: `TrProxy` replaces `col.tr` and sends
each call to a translation-only engine backend for the current language. Collections are also opened
with the person's language, so messages made inside the engine (errors, "Studied N cards") match.
"""
import contextvars, json, os, re, threading, weakref
from contextlib import contextmanager

HERE = os.path.dirname(os.path.abspath(__file__))

# code -> (name shown in the picker, language passed to the engine)
LANGUAGES = {
    "en": ("English", "en"),
    "es": ("Español", "es"),
    "fr": ("Français", "fr"),
    "de": ("Deutsch", "de"),
    "pt": ("Português", "pt_PT"),
    "it": ("Italiano", "it"),
    "ga": ("Gaeilge", "ga"),
    "pl": ("Polski", "pl"),
}
DEFAULT = "en"


def _pl_pl(n):
    if n == 1:
        return 0
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return 1
    return 2


def _pl_ga(n):
    if n == 1: return 0
    if n == 2: return 1
    if 3 <= n <= 6: return 2
    if 7 <= n <= 10: return 3
    return 4


# Plural rule per language: number -> index into the list of forms.
PLURAL = {
    "en": lambda n: 0 if n == 1 else 1,
    "es": lambda n: 0 if n == 1 else 1,
    "de": lambda n: 0 if n == 1 else 1,
    "it": lambda n: 0 if n == 1 else 1,
    "pt": lambda n: 0 if n == 1 else 1,
    "fr": lambda n: 0 if n in (0, 1) else 1,
    "ga": _pl_ga,
    "pl": _pl_pl,
}
PLURAL_FORMS = {"en": 2, "es": 2, "de": 2, "it": 2, "pt": 2, "fr": 2, "ga": 5, "pl": 3}

_catalogs, _cat_lock = {}, threading.Lock()


def catalog(code):
    with _cat_lock:
        if code not in _catalogs:
            path = os.path.join(HERE, f"{code}.json")
            try:
                with open(path, encoding="utf-8") as f:
                    _catalogs[code] = json.load(f)
            except (OSError, ValueError):
                _catalogs[code] = {}
        return _catalogs[code]


def reload():
    with _cat_lock:
        _catalogs.clear()


# ---------- which language this request uses ----------
# The contextvar holds a small dict per request: {"scope": asgi scope, "lang": resolved or None}.
_req = contextvars.ContextVar("flashcards_lang_req", default=None)
_user_lang_fn = None   # set by app.py: user name -> saved code or ""


def set_user_lang_lookup(fn):
    global _user_lang_fn
    _user_lang_fn = fn


def from_accept_language(header):
    """Best supported language from an Accept-Language header, or ''."""
    best, best_q = "", -1.0
    for i, part in enumerate((header or "").split(",")):
        bits = part.strip().split(";")
        tag = bits[0].strip().lower()
        if not tag or tag == "*":
            continue
        q = 1.0
        for b in bits[1:]:
            b = b.strip()
            if b.startswith("q="):
                try:
                    q = float(b[2:])
                except ValueError:
                    q = 0.0
        code = re.split(r"[-_]", tag)[0]
        if code in LANGUAGES and q > best_q and q > 0:
            best, best_q = code, q
    return best


def _resolve(state):
    scope = state.get("scope") or {}
    session = scope.get("session") or {}
    user = session.get("user") if isinstance(session, dict) else None
    if user and _user_lang_fn:
        try:
            code = _user_lang_fn(user)
        except Exception:
            code = ""
        if code in LANGUAGES:
            return code
    headers = dict(scope.get("headers") or [])
    accept = headers.get(b"accept-language", b"").decode("latin-1")
    return from_accept_language(accept) or DEFAULT


def current():
    """Language code for the current request (or the one set with use_lang)."""
    state = _req.get()
    if state is None:
        return DEFAULT
    if state.get("lang") is None:
        state["lang"] = _resolve(state)
    return state["lang"]


def request_default(scope):
    """The language a request would use without a saved choice (for the picker's "Automatic")."""
    headers = dict(scope.get("headers") or [])
    return from_accept_language(headers.get(b"accept-language", b"").decode("latin-1")) or DEFAULT


@contextmanager
def use_lang(code):
    """Run a block in a given language (background jobs, tests)."""
    token = _req.set({"scope": {}, "lang": code if code in LANGUAGES else DEFAULT})
    try:
        yield
    finally:
        _req.reset(token)


def forget_request_lang():
    """After the person changes language mid-request, work it out again."""
    state = _req.get()
    if state is not None:
        state["lang"] = None


class LangMiddleware:
    """Pure ASGI middleware: remembers the request so the language can be worked out lazily
    (after the session middleware has read the cookie)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        token = _req.set({"scope": scope, "lang": None})
        try:
            await self.app(scope, receive, send)
        finally:
            _req.reset(token)


# ---------- translating ----------
def _fmt(s, kw):
    if not kw:
        return s
    try:
        return s.format(**kw)
    except (KeyError, IndexError, ValueError):
        return s


def t(key, **kw):
    """Translate an English interface string. Placeholders use {name} and are filled from kw."""
    code = current()
    s = key
    if code != "en":
        v = catalog(code).get(key)
        if isinstance(v, str) and v:
            s = v
    return _fmt(s, kw)


def tn(singular, plural, n, **kw):
    """Plural-aware translation; {n} is available in the text."""
    kw.setdefault("n", n)
    code = current()
    forms = None
    if code != "en":
        v = catalog(code).get(singular)
        if isinstance(v, list) and v and all(isinstance(x, str) and x for x in v):
            forms = v
    if forms is None:
        code, forms = "en", [singular, plural]
    try:
        idx = PLURAL[code](abs(int(n)))
    except (TypeError, ValueError):
        idx = len(forms) - 1
    return _fmt(forms[min(idx, len(forms) - 1)], kw)


def html_lang():
    return current()


# ---------- the engine's translations ----------
_tr_backends, _tr_lock = {}, threading.Lock()


def engine_lang(code):
    return LANGUAGES.get(code, LANGUAGES[DEFAULT])[1]


def make_backend(code):
    from anki._backend import RustBackend
    return RustBackend(langs=[engine_lang(code)])


def engine_tr(code=None):
    """A Translations object (like col.tr) for a language; one translation backend per language."""
    from anki._backend import Translations
    code = code or current()
    with _tr_lock:
        if code not in _tr_backends:
            b = make_backend(code)
            _tr_backends[code] = (b, Translations(weakref.ref(b)))
        return _tr_backends[code][1]


class TrProxy:
    """Stands in for col.tr: each call uses the current request's language."""

    def __init__(self, own):
        self._own = own   # the collection's own Translations (its open-time language)

    def __getattr__(self, name):
        return getattr(engine_tr(), name)

    def __call__(self, *a, **kw):
        return engine_tr()(*a, **kw)


def install(col):
    """Make col.tr follow the request's language."""
    if not isinstance(col.tr, TrProxy):
        col.tr = TrProxy(col.tr)
    return col


def open_collection(path, code=None):
    """Open a collection whose engine speaks `code` (default: the current language)."""
    from anki.collection import Collection
    col = Collection(path, backend=make_backend(code or current()))
    return install(col)


def describe_next_states(col, states):
    """Answer-button intervals in the engine's own wording. The engine needs an open collection for
    this, so it uses the collection's backend, which is opened in the person's language."""
    return col.sched.describe_next_states(states)


def studied_today(col, cards, seconds):
    """The engine's "Studied N cards in M minutes" (needs the collection; see describe_next_states)."""
    return col._backend.studied_today_message(cards=cards, seconds=seconds)


def N_(s):
    """Marks a string for translation without translating it yet (translate later with t(value))."""
    return s
