"""Offline study, part 3: sending what was done offline back to the server.

`POST /offline/sync` takes the device's queue (answers given on the offline study page, and notes
added there) and replays it, in the order it happened, through the real scheduler:

- each answer is applied with `answer_card`, with the time taken that was recorded and the time it
  was given (`answered_at_millis`, which becomes the review's time in the review history), so
  statistics show the review on the day and hour it happened. The engine works out the new state
  from the card's state now, exactly as if the button had been pressed now;
- a card that was changed or answered somewhere else since the offline copy was made (its
  modification time is no longer the one the device saw) is skipped, with all its later offline
  answers, and logged; the person is told how many were skipped;
- exactly once: every queued item has a unique id; the server remembers the ids it has handled
  (and what came of them) in `data/users/<name>/offline_sync.json`, so a retry after a lost reply
  never answers a card twice;
- added notes (any non-image-occlusion note type, text only) are added with the engine's add_note.

The engine's leech handling and the add-ons' `after_answer` hook run as on the study screen.
"""
import html, json, os, re, threading, time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from anki.notes import NoteFieldsCheckResult
from anki.scheduler.v3 import CardAnswer

import app as core
from flashcards.i18n import t, tn

router = APIRouter()

STORE_NAME = "offline_sync.json"
KEEP_SECS = 120 * 86400     # remember handled ids this long (a device may stay offline for weeks)
KEEP_MAX = 50000            # and at most this many
LOG_MAX = 200               # skipped items kept in the log
MAX_ANSWERS = 5000          # per request; the device sends the rest next time
MAX_ADDS = 500
MAX_FIELD = 100_000
RATINGS = {1: CardAnswer.AGAIN, 2: CardAnswer.HARD, 3: CardAnswer.GOOD, 4: CardAnswer.EASY}
_file_lock = threading.Lock()


# ---------- the remembered ids ----------
def store_path(user):
    return os.path.join(core.DATA, "users", user, STORE_NAME)


def load_store(user):
    try:
        with open(store_path(user), encoding="utf-8") as f:
            s = json.load(f)
        if isinstance(s, dict):
            s.setdefault("done", {}); s.setdefault("cmod", {}); s.setdefault("log", [])
            return s
    except (OSError, ValueError):
        pass
    return {"done": {}, "cmod": {}, "log": []}


def save_store(user, s, now=None):
    now = now or time.time()
    done = {k: v for k, v in s["done"].items() if now - v[1] < KEEP_SECS}
    if len(done) > KEEP_MAX:
        done = dict(sorted(done.items(), key=lambda kv: kv[1][1])[-KEEP_MAX:])
    s["done"] = done
    s["cmod"] = dict(list(s["cmod"].items())[-KEEP_MAX:])
    s["log"] = s["log"][-LOG_MAX:]
    path = store_path(user)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with _file_lock:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(s, f)
        os.replace(tmp, path)


# ---------- note types offered on the offline Add screen ----------
def add_types(col):
    """Standard and cloze note types (not image occlusion), with their field names, for the offline
    Add screen. Text only: what is typed there is added as plain text."""
    from flashcards import image_occlusion
    out = []
    for nt in col.models.all():
        if nt.get("originalStockKind") == image_occlusion.IO_STOCK_KIND and image_occlusion.is_io_notetype(col, nt):
            continue
        out.append({"id": nt["id"], "name": nt["name"], "cloze": nt.get("type") == 1,
                    "fields": [f["name"] for f in nt["flds"]]})
    out.sort(key=lambda x: (x["name"] != "Basic", x["cloze"], x["name"].lower()))
    return out[:60]


def text_to_field(s):
    """Typed text as field HTML: escaped, line breaks kept."""
    s = str(s or "")[:MAX_FIELD].replace("\r\n", "\n").replace("\r", "\n")
    return html.escape(s, quote=False).replace("\n", "<br>")


# ---------- replay ----------
def _int(v, default=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _answer(col, a, expect_mod, now_ms):
    """Apply one queued answer. Returns (result, card or None)."""
    cid, rating = _int(a.get("cid")), _int(a.get("rating"))
    if rating not in RATINGS or not cid:
        return "invalid", None
    try:
        card = col.get_card(cid)
    except Exception:   # noqa: BLE001  (deleted on another device)
        return "gone", None
    # Changed elsewhere: its modification time (whole seconds) or its number of reviews differs.
    if not any(m == card.mod and (r is None or r == card.reps) for m, r in expect_mod):
        return "changed", None
    states = col._backend.get_scheduling_states(card.id)
    card.timer_started = time.time()
    ans = col.sched.build_answer(card=card, states=states, rating=RATINGS[rating])
    ans.milliseconds_taken = max(0, min(_int(a.get("ms")), 3_600_000))
    ts = _int(a.get("ts"), now_ms)
    ans.answered_at_millis = max(0, min(ts, now_ms))
    leech = col.sched.state_is_leech(ans.new_state)
    col.sched.answer_card(ans)
    from flashcards import addons
    addons.after_answer(col, card, rating)   # never raises
    card.load()
    return ("leech" if leech else "ok"), card


def _add(col, a):
    nt = col.models.get(_int(a.get("ntid")))
    if not nt:
        return "gone"
    did = _int(a.get("did"))
    if not did or not col.decks.get(did, default=False) or col.decks.is_filtered(did):
        did = 1   # the deck went away (or is filtered): the Default deck, so nothing typed is lost
    note = col.new_note(nt)
    fields = a.get("fields") if isinstance(a.get("fields"), list) else []
    for i, name in enumerate(note.keys()):
        if i < len(fields):
            note[name] = text_to_field(fields[i])
    tags = str(a.get("tags") or "")[:2000]
    note.tags = [x for x in re.split(r"\s+", tags) if x]
    # The engine's own check, as Anki's Add dialog does: an empty first field or a cloze note without a
    # cloze isn't added (a duplicate is, as in Anki).
    try:
        state = note.fields_check()
    except Exception:   # noqa: BLE001
        return "invalid"
    if state in (NoteFieldsCheckResult.EMPTY, NoteFieldsCheckResult.MISSING_CLOZE):
        return "invalid"
    try:
        col.add_note(note, did)
    except Exception:   # noqa: BLE001  (e.g. a cloze note without any cloze, or an empty first field)
        return "invalid"
    return "ok" if note.id else "invalid"


def replay(col, user, answers, adds, device="", now=None):
    """Replay queued answers and adds in the order they happened. Returns the result per id and a
    summary. Items already handled (same id) aren't applied again; their earlier result is returned.
    device: the sending device's id, so that an answer it queued while its earlier answers to the same
    card were on their way is not taken for a change made on another device."""
    now = now or time.time()
    now_ms = int(now * 1000)
    store = load_store(user)
    done, cmod = store["done"], store["cmod"]
    items = [("a", x) for x in answers if isinstance(x, dict)] + [("n", x) for x in adds if isinstance(x, dict)]
    items.sort(key=lambda it: (_int(it[1].get("ts")), str(it[1].get("uid"))))
    results, summary = {}, {"answered": 0, "skipped": 0, "added": 0, "leeches": 0, "repeated": 0, "failed": 0}
    batch_mod = {}   # card id -> its mod after this request's own answers
    for kind, x in items:
        uid = str(x.get("uid") or "")[:80]
        if not uid:
            continue
        if uid in done:
            results[uid] = done[uid][0]; summary["repeated"] += 1
            continue
        if kind == "a":
            cid = _int(x.get("cid"))
            # The mod the device saw, or the one our own replay of this device's earlier answers left
            # (an answer queued while an earlier sync of the same card was on its way). Another
            # device's replay is a change made elsewhere.
            mine = cmod.get(str(cid))
            reps = x.get("reps")
            expect = [(_int(x.get("mod"), -1), _int(reps) if reps is not None else None)]
            if cid in batch_mod:
                expect.append(batch_mod[cid])
            if isinstance(mine, list) and len(mine) == 3 and device and mine[0] == device:
                expect.append((mine[1], mine[2]))
            sibs_before = {r[0]: (r[1], r[2]) for r in col.db.all(
                "select id, mod, reps from cards where nid = (select nid from cards where id = ?) and id != ?",
                cid, cid)} if cid else {}
            res, card = _answer(col, x, expect, now_ms)
            if card is not None:
                batch_mod[cid] = (card.mod, card.reps); cmod[str(cid)] = [device, card.mod, card.reps]
                # Siblings buried by this answer were changed by us, not by another device.
                for sid, smod, sreps in col.db.all("select id, mod, reps from cards where nid = ? and id != ?",
                                                   card.nid, cid):
                    if sibs_before.get(sid) != (smod, sreps):
                        cmod[str(sid)] = [device, smod, sreps]; batch_mod[sid] = (smod, sreps)
            if res in ("ok", "leech"):
                summary["answered"] += 1
                summary["leeches"] += res == "leech"
            else:
                summary["skipped"] += 1
                store["log"].append({"t": int(now), "uid": uid, "cid": cid, "why": res, "ts": _int(x.get("ts"))})
        else:
            res = _add(col, x)
            if res == "ok":
                summary["added"] += 1
            else:
                summary["failed"] += 1
                store["log"].append({"t": int(now), "uid": uid, "add": True, "why": res, "ts": _int(x.get("ts"))})
        done[uid] = [res, int(now)]
        results[uid] = res
    save_store(user, store, now)
    return results, summary


def message(summary):
    """What to tell the person, in their language ("" when nothing new happened)."""
    parts = []
    if summary["answered"]:
        parts.append(tn("{n} offline answer was saved.", "{n} offline answers were saved.", summary["answered"]))
    if summary["added"]:
        parts.append(tn("{n} note added offline was saved.", "{n} notes added offline were saved.", summary["added"]))
    if summary["skipped"]:
        parts.append(tn("{n} answer was skipped because the card changed on another device.",
                        "{n} answers were skipped because the cards changed on another device.", summary["skipped"]))
    if summary["failed"]:
        parts.append(tn("{n} note added offline couldn't be saved (its note type was deleted, or it had no cards).",
                        "{n} notes added offline couldn't be saved (their note type was deleted, or they had no cards).",
                        summary["failed"]))
    if summary["leeches"]:
        parts.append(t("Card was a leech."))
    return " ".join(parts)


@router.post("/offline/sync")
async def offline_sync(request: Request):
    user = core.current_user(request)
    if not user: return JSONResponse({"error": "login"}, status_code=401)
    try:
        body = await request.json()
    except Exception:   # noqa: BLE001
        return JSONResponse({"error": "bad request"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "bad request"}, status_code=400)
    answers = body.get("answers") if isinstance(body.get("answers"), list) else []
    adds = body.get("adds") if isinstance(body.get("adds"), list) else []
    if len(answers) > MAX_ANSWERS or len(adds) > MAX_ADDS:
        return JSONResponse({"error": "too many"}, status_code=413)
    with core.collection(user) as col:
        device = str(body.get("device") or "")[:80]
        results, summary = replay(col, user, answers, adds, device)
        from flashcards import sync as sync_mod
        stamp = sync_mod.stamp(user)
    return JSONResponse({"results": results, **summary, "message": message(summary), "stamp": stamp},
                        headers={"Cache-Control": "no-store"})
