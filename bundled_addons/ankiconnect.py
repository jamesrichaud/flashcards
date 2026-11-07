"""Lets other apps add and read cards (AnkiConnect-compatible API, protected by a personal key).

Like the desktop add-on "AnkiConnect": tools such as Yomitan, browser extensions and scripts can
send cards straight into your collection. Each person makes their own key on the add-on's page.
Endpoint: POST <server>/addon/ankiconnect/ with {"action": ..., "version": 6, "key": ..., "params": {...}}.
"""
import base64
import binascii
import hashlib
import html
import json
import os
import secrets
import threading

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

import app as core
from flashcards import addons

NAME = "AnkiConnect (API for other apps)"
MENU = [("My API key", "/settings")]
PUBLIC_PATHS = ("/",)          # the API itself checks the personal key instead of a login
API_VERSION = 6
_lock = threading.Lock()
CORS = {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type", "Access-Control-Max-Age": "86400"}


# ---------- keys: stored hashed, one per person ----------
def keys_file():
    return os.path.join(core.DATA, "ankiconnect_keys.json")


def load_keys():
    try:
        return json.load(open(keys_file()))
    except (OSError, ValueError):
        return {}


def save_keys(k):
    tmp = keys_file() + ".tmp"
    with open(tmp, "w") as f:
        json.dump(k, f)
    os.replace(tmp, keys_file())


def digest(key):
    return hashlib.sha256(key.encode()).hexdigest()


def user_for_key(key):
    if not isinstance(key, str) or not key:
        return None
    want = digest(key)
    for h, user in load_keys().items():
        if secrets.compare_digest(h, want) and user in core.load_users():
            return user
    return None


# ---------- actions ----------
class ApiError(Exception):
    pass


def _deck_id(col, name, create=True):
    did = col.decks.id_for_name(name)
    if not did and create:
        did = col.decks.id(name)
    if not did:
        raise ApiError(f"deck was not found: {name}")
    return did


def _model(col, name):
    m = col.models.by_name(name)
    if not m:
        raise ApiError(f"model was not found: {name}")
    return m


def _store_media(col, item):
    data, name = item.get("data"), item.get("filename")
    if not name or not data:
        raise ApiError("media needs 'filename' and base64 'data' (url and path are not supported here)")
    try:
        raw = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError):
        raise ApiError("media 'data' is not valid base64")
    return col.media.write_data(os.path.basename(str(name)), raw)


def _build_note(col, spec):
    model = col.models.by_name(spec.get("modelName", ""))
    if not model:
        raise ApiError(f"model was not found: {spec.get('modelName')}")
    did = _deck_id(col, spec.get("deckName", "Default"))
    note = col.new_note(model)
    for name, value in (spec.get("fields") or {}).items():
        if name not in note:
            raise ApiError(f"field was not found: {name}")
        note[name] = str(value)
    for kind in ("audio", "picture", "video"):
        items = spec.get(kind) or []
        for item in items if isinstance(items, list) else [items]:
            fname = _store_media(col, item)
            tag = f'<img src="{html.escape(fname)}">' if kind == "picture" else f"[sound:{fname}]"
            for fld in item.get("fields") or []:
                if fld in note:
                    note[fld] += tag
    note.tags = [str(t) for t in spec.get("tags") or []]
    return note, did


def _duplicate(col, note, spec):
    opts = spec.get("options") or {}
    if opts.get("allowDuplicate"):
        return False
    return bool(note.fields[0].strip()) and note.fields_check() == 2   # DUPLICATE


def act_addNote(col, p):
    note, did = _build_note(col, p["note"])
    if not note.fields[0].strip():
        raise ApiError("cannot create note because it is empty")
    if _duplicate(col, note, p["note"]):
        raise ApiError("cannot create note because it is a duplicate")
    col.add_note(note, did)
    return note.id


def act_addNotes(col, p):
    out = []
    for spec in p["notes"]:
        try:
            out.append(act_addNote(col, {"note": spec}))
        except ApiError:
            out.append(None)
    return out


def act_canAddNotes(col, p):
    out = []
    for spec in p["notes"]:
        try:
            note, _ = _build_note(col, {k: v for k, v in spec.items() if k not in ("audio", "picture", "video")})
            out.append(bool(note.fields[0].strip()) and not _duplicate(col, note, spec))
        except ApiError:
            out.append(False)
    return out


def act_updateNoteFields(col, p):
    n = p["note"]
    note = col.get_note(int(n["id"]))
    for name, value in (n.get("fields") or {}).items():
        if name in note:
            note[name] = str(value)
    for kind in ("audio", "picture", "video"):
        for item in n.get(kind) or []:
            fname = _store_media(col, item)
            tag = f'<img src="{html.escape(fname)}">' if kind == "picture" else f"[sound:{fname}]"
            for fld in item.get("fields") or []:
                if fld in note:
                    note[fld] += tag
    col.update_note(note)
    return None


def act_notesInfo(col, p):
    ids = p.get("notes")
    if ids is None:
        ids = col.find_notes(p.get("query", ""))
    out = []
    for nid in ids:
        try:
            n = col.get_note(int(nid))
        except Exception:
            out.append({})
            continue
        m = n.note_type()
        out.append({"noteId": n.id, "modelName": m["name"], "tags": n.tags, "mod": n.mod,
                    "fields": {f["name"]: {"value": n.fields[i], "order": i} for i, f in enumerate(m["flds"])},
                    "cards": [c.id for c in n.cards()]})
    return out


def act_cardsInfo(col, p):
    out = []
    for cid in p["cards"]:
        try:
            c = col.get_card(int(cid))
        except Exception:
            out.append({})
            continue
        n = c.note()
        m = n.note_type()
        out.append({"cardId": c.id, "note": c.nid, "deckName": col.decks.name(c.did), "modelName": m["name"],
                    "fields": {f["name"]: {"value": n.fields[i], "order": i} for i, f in enumerate(m["flds"])},
                    "question": c.question(), "answer": c.answer(), "ord": c.ord, "type": c.type,
                    "queue": c.queue, "due": c.due, "interval": c.ivl, "factor": c.factor, "reps": c.reps,
                    "lapses": c.lapses, "fieldOrder": c.ord, "mod": c.mod})
    return out


ACTIONS = {
    "version": lambda col, p: API_VERSION,
    "requestPermission": lambda col, p: {"permission": "granted", "requireApikey": True, "version": API_VERSION},
    "deckNames": lambda col, p: [d.name for d in col.decks.all_names_and_ids()],
    "deckNamesAndIds": lambda col, p: {d.name: d.id for d in col.decks.all_names_and_ids()},
    "createDeck": lambda col, p: _deck_id(col, p["deck"]),
    "modelNames": lambda col, p: [m.name for m in col.models.all_names_and_ids()],
    "modelNamesAndIds": lambda col, p: {m.name: m.id for m in col.models.all_names_and_ids()},
    "modelFieldNames": lambda col, p: [f["name"] for f in _model(col, p["modelName"])["flds"]],
    "findNotes": lambda col, p: list(col.find_notes(p.get("query", ""))),
    "findCards": lambda col, p: list(col.find_cards(p.get("query", ""))),
    "notesInfo": act_notesInfo,
    "cardsInfo": act_cardsInfo,
    "addNote": act_addNote,
    "addNotes": act_addNotes,
    "canAddNotes": act_canAddNotes,
    "updateNoteFields": act_updateNoteFields,
    "addTags": lambda col, p: col.tags.bulk_add([int(n) for n in p["notes"]], p["tags"]) and None,
    "removeTags": lambda col, p: col.tags.bulk_remove([int(n) for n in p["notes"]], p["tags"]) and None,
    "getTags": lambda col, p: col.tags.all(),
    "suspend": lambda col, p: bool(col.sched.suspend_cards([int(c) for c in p["cards"]])) or True,
    "unsuspend": lambda col, p: bool(col.sched.unsuspend_cards([int(c) for c in p["cards"]])) or True,
    "storeMediaFile": lambda col, p: _store_media(col, p),
    "deleteNotes": lambda col, p: col.remove_notes([int(n) for n in p["notes"]]) and None,
    "sync": lambda col, p: None,        # everything is already on this server
}


def run(user, action, params):
    if action == "multi":
        return [call(user, a) for a in params.get("actions", [])]
    fn = ACTIONS.get(action)
    if fn is None:
        raise ApiError(f"unsupported action: {action}")
    with core.collection(user) as col:
        try:
            return fn(col, params or {})
        except ApiError:
            raise
        except KeyError as e:
            raise ApiError(f"missing parameter: {e.args[0]}")
        except Exception as e:
            raise ApiError(str(e) or e.__class__.__name__)


def call(user, req):
    """One request (also used for each item of 'multi'): returns AnkiConnect's reply shape."""
    version = req.get("version", 4) if isinstance(req, dict) else 4
    try:
        if not isinstance(req, dict):
            raise ApiError("request must be a JSON object")
        result, error = run(user, req.get("action"), req.get("params") or {}), None
    except ApiError as e:
        result, error = None, str(e)
    if version <= 4:
        return result if error is None else {"result": None, "error": error}
    return {"result": result, "error": error}


router = APIRouter()


@router.options("/")
def preflight():
    return Response(status_code=204, headers=CORS)


@router.post("/")
async def api(request: Request):
    try:
        req = json.loads(await request.body() or b"{}")
    except ValueError:
        return JSONResponse({"result": None, "error": "request is not valid JSON"}, headers=CORS)
    action = req.get("action") if isinstance(req, dict) else None
    if action == "version" or action == "requestPermission":
        reply = {"result": ACTIONS[action](None, {}), "error": None}
        return JSONResponse(reply if req.get("version", 4) > 4 else reply["result"], headers=CORS)
    user = user_for_key(req.get("key") if isinstance(req, dict) else None)
    if not user:
        return JSONResponse({"result": None, "error": "valid api key must be provided"}, headers=CORS)
    return JSONResponse(call(user, req), headers=CORS)


@router.get("/")
def api_get():
    return JSONResponse({"apiVersion": f"AnkiConnect v.{API_VERSION}"}, headers=CORS)


@router.get("/settings")
def settings_page(request: Request):
    user = addons.user(request)
    has = user in load_keys().values()
    new = request.session.pop("ac_new_key", "")
    url = str(request.base_url).rstrip("/") + "/addon/ankiconnect/"
    shown = (f'<p>Your new key (copy it now, it won\'t be shown again):</p>'
             f'<input readonly value="{html.escape(new)}" onclick="this.select()" style="font-family:monospace">'
             if new else "")
    body = f"""<div class="box"><h2 style="margin-top:0">AnkiConnect</h2>
<p>Lets other apps add cards to <b>your</b> collection. In the other app, set the AnkiConnect address to:</p>
<input readonly value="{html.escape(url)}" onclick="this.select()" style="font-family:monospace">
<p>and paste your personal key into its "API key" box.</p>{shown}
<p class="muted">Status: {"you have a key" if has else "no key yet"}. Making a new key replaces the old one.</p>
<form method="post" action="/addon/ankiconnect/settings/new" class="row">
<button class="primary">{"Make a new key" if has else "Make my key"}</button>
{'<button formaction="/addon/ankiconnect/settings/revoke" class="danger">Remove my key</button>' if has else ''}
<a class="btn" href="/addons">Back to add-ons</a></form></div>"""
    return addons.page(request, "AnkiConnect", body)


@router.post("/settings/new")
def new_key(request: Request):
    user = addons.user(request)
    key = secrets.token_urlsafe(24)
    with _lock:
        k = {h: u for h, u in load_keys().items() if u != user}
        k[digest(key)] = user
        save_keys(k)
    request.session["ac_new_key"] = key
    return RedirectResponse("/addon/ankiconnect/settings", 303)


@router.post("/settings/revoke")
def revoke(request: Request):
    user = addons.user(request)
    with _lock:
        save_keys({h: u for h, u in load_keys().items() if u != user})
    return RedirectResponse("/addon/ankiconnect/settings", 303)
