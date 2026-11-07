"""Offline study, part 1: keeping an offline copy of decks in the browser.

The server side of it:
- which decks a person wants offline (a switch per deck on the deck overview, and "all decks" in
  Preferences), stored in the collection config under `flashcardsOffline` so it follows the person;
- `GET /offline/state`: what the browser should hold (deck list, settings, a version that changes
  whenever the collection changes or a new day starts);
- `POST /offline/deck/{did}/snapshot`: one deck's offline copy: today's queue within the deck limits
  plus up to 3 days ahead, each card's question/answer exactly as the study screen renders it
  (add-on output included), the engine's four next states as button labels
  (describe_next_states), and the media files it needs. The browser sends what it already has
  (card id -> render key), so unchanged cards aren't rendered again;
- `/offline/client.js`: the browser side (IndexedDB copy, media in Cache Storage, status lines).

Everything here only READS the collection (no deck selection, no queue building through the
scheduler, which would change the current deck and the undo history), so a background refresh
never changes anything for the person or for sync. Studying from the copy is part 2.
"""
import hashlib, html, json, os, re, time
from urllib.parse import quote

from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse, Response, JSONResponse, HTMLResponse, FileResponse

from anki.collection import SearchNode, FormatTimeSpan
from anki.sound import SoundOrVideoTag
from anki.utils import ids2str

import app as core
from flashcards import i18n
from flashcards.i18n import t, tn

router = APIRouter()

CONFIG_KEY = "flashcardsOffline"
DAYS_CHOICES = (0, 1, 2, 3)
MEDIA_CHOICES = (50, 100, 250, 500, 1000)   # MB of pictures and sounds kept per device
DEFAULTS = {"all": False, "decks": [], "off": [], "days": 3, "media_mb": 250}
MAX_CARDS = 3000          # per deck copy (all days together)
FORMAT = 1                # bump when the snapshot's shape changes, so browsers fetch everything again


# ---------- settings ----------
def settings(col):
    raw = col.get_config(CONFIG_KEY, None)
    s = dict(DEFAULTS)
    if isinstance(raw, dict):
        s["all"] = bool(raw.get("all", False))
        for k in ("decks", "off"):
            v = raw.get(k, [])
            s[k] = sorted({int(x) for x in v if isinstance(x, int) or (isinstance(x, str) and x.isdigit())}) \
                if isinstance(v, list) else []
        if raw.get("days") in DAYS_CHOICES:
            s["days"] = raw["days"]
        if raw.get("media_mb") in MEDIA_CHOICES:
            s["media_mb"] = raw["media_mb"]
    return s


def save_settings(col, s):
    if s != settings(col):
        col.set_config(CONFIG_KEY, s)


def deck_is_offline(s, did):
    if did in s["off"]:
        return False
    return did in s["decks"] or s["all"]


def offline_decks(col, s=None):
    """[(did, name)] of the decks to keep offline. "All decks" means every top-level deck (each copy
    includes its subdecks, as studying it does); a subdeck switched on by itself gets its own copy.
    The empty Default deck is left out, as the deck list hides it."""
    s = s or settings(col)
    out = []
    for d in col.decks.all_names_and_ids():
        did, name = int(d.id), d.name
        if did in s["off"]:
            continue
        top = "::" not in name
        if did in s["decks"] or (s["all"] and top):
            if s["all"] and did == 1 and did not in s["decks"] and not col.decks.card_count(1, include_subdecks=True):
                continue
            out.append((did, name))
    out.sort(key=lambda x: x[1].lower())
    return out


def set_deck(col, did, on):
    s = settings(col)
    s["decks"] = [d for d in s["decks"] if d != did]
    s["off"] = [d for d in s["off"] if d != did]
    if on:
        if not s["all"] or "::" in col.decks.name(did):
            s["decks"].append(did)
    elif s["all"]:
        s["off"].append(did)
    s["decks"].sort(); s["off"].sort()
    save_settings(col, s)


# ---------- the snapshot ----------
def _ids(col, query, order="c.due asc, c.ord asc, c.id asc"):
    return list(col.find_cards(query, order=order))


def _rows(col, ids):
    """id -> (queue, type, due, card mod, notetype id, what the rendering depends on), one read-only query.
    Modification times are in whole seconds, so the note's fields and tags themselves are used."""
    if not ids:
        return {}
    return {r[0]: (r[1], r[2], r[3], r[4], r[5], f"{r[6]}|{r[7]}|{r[8]}|{r[9]}|{r[10]}") for r in col.db.all(
        "select c.id, c.queue, c.type, c.due, c.mod, n.mid, c.did, c.ord, c.flags, n.flds, n.tags "
        f"from cards c join notes n on n.id = c.nid where c.id in {ids2str(ids)}")}


def pick_cards(col, did, days):
    """The card ids of a deck's offline copy, by day: {0: {"lrn": [...], "rev": [...], "new": [...]}, 1: ...}.
    Today follows the deck list's counts (the engine's limits, parent decks included); later days use
    the deck's preset limits. Within a group the order is the engine's due order (new cards in their
    new-card position). Read-only: no deck is selected and no queue is built."""
    deck = col.decks.get(did)
    base = col.build_search_string(SearchNode(deck=deck["name"]))
    filtered = bool(deck.get("dyn"))
    node = col.sched.deck_due_tree(did)
    new_today = node.new_count if node else 0
    rev_today = node.review_count if node else 0
    today, cutoff = col.sched.today, col.sched.day_cutoff
    out = {0: {"lrn": [], "rev": [], "new": []}}
    # learning: everything due before the day ends (intraday steps come back during the session)
    lrn = _ids(col, f"{base} is:learn -is:suspended -is:buried")
    rows = _rows(col, lrn)
    later_lrn = {}
    for cid in lrn:
        queue, _type, due = rows[cid][:3]
        if queue in (1, 4):
            if due < cutoff:
                out[0]["lrn"].append(cid)
        elif queue == 3:
            if due <= today:
                out[0]["lrn"].append(cid)
            elif not filtered and due - today <= days:
                later_lrn.setdefault(due - today, []).append(cid)
    out[0]["rev"] = _ids(col, f"{base} is:review is:due -is:learn -is:suspended -is:buried")[:rev_today]
    new_all = _ids(col, f"{base} is:new -is:suspended -is:buried")
    out[0]["new"] = new_all[:new_today]
    if not filtered and days:
        conf = col.decks.config_dict_for_deck_id(did)
        new_per_day, rev_per_day = int(conf["new"]["perDay"]), int(conf["rev"]["perDay"])
        pos = new_today
        for k in range(1, days + 1):
            rev = _ids(col, f"{base} is:review prop:due={k} -is:learn -is:suspended")[:rev_per_day]
            out[k] = {"lrn": later_lrn.get(k, []), "rev": rev, "new": new_all[pos:pos + new_per_day]}
            pos += new_per_day
    # never more than MAX_CARDS, today first
    left, truncated = MAX_CARDS, False
    for k in sorted(out):
        for group in ("lrn", "rev", "new"):
            ids = out[k][group]
            if len(ids) > left:
                out[k][group], truncated = ids[:max(0, left)], True
            left -= len(out[k][group])
    return out, truncated


def _clean_label(s):
    return re.sub(r"[⁨⁩]", "", s)


def _span(col, secs):
    """An interval in the answer buttons' wording ("10m", "3d"), as describe_next_states writes it."""
    return _clean_label(col.format_timespan(max(0, secs), FormatTimeSpan.ANSWER_BUTTONS))


def _hard_first(secs):
    """Hard on the first learning step, as the engine does it: halfway to the second step, or 1.5 x
    the only step (at most a day longer)."""
    if not secs:
        return 0
    if len(secs) > 1:
        return (secs[0] + secs[1]) // 2
    return min(secs[0] * 3 // 2, secs[0] + 86400)


def _next(state):
    """One answer's next state from the engine, kept small for the browser:
    {"k": "l"} learning / "rl" relearning (with secs until it is shown again and the steps left),
    "p" preview (filtered deck), "r" review = done for the day."""
    if state.WhichOneof("kind") == "filtered":
        f = state.filtered
        if f.WhichOneof("kind") == "preview":
            return {"k": "r"} if f.preview.finished else {"k": "p", "secs": int(f.preview.scheduled_secs)}
        state = f.rescheduling.original_state
    else:
        state = state.normal
    kind = state.WhichOneof("kind")
    if kind == "learning":
        return {"k": "l", "secs": int(state.learning.scheduled_secs), "rem": int(state.learning.remaining_steps)}
    if kind == "relearning":
        r = state.relearning
        return {"k": "rl", "secs": int(r.learning.scheduled_secs), "rem": int(r.learning.remaining_steps),
                "days": int(r.review.scheduled_days)}
    return {"k": "r"}


def next_states(col, states):
    """For offline study: what each button does to the card (engine states), and for cards that may be
    relearned, the labels of graduating with Good / Easy (the interval the engine gives after a lapse)."""
    out = [_next(s) for s in (states.again, states.hard, states.good, states.easy)]
    days = next((x["days"] for x in out if x["k"] == "rl"), None)
    if days is None and states.current.normal.WhichOneof("kind") == "relearning":
        days = int(states.current.normal.relearning.review.scheduled_days)
    rl = [_span(col, days * 86400), _span(col, (days + 1) * 86400)] if days is not None else None
    return out, rl


def conf_info(col, conf):
    """A preset's steps (minutes) and the labels the offline study page needs for learning cards it
    shows again (the engine's wording; the page marks them "~" as they are worked out offline)."""
    learn = [round(float(x) * 60) for x in conf.get("new", {}).get("delays", [])]
    relearn = [round(float(x) * 60) for x in conf.get("lapse", {}).get("delays", [])]
    ints = list(conf.get("new", {}).get("ints", [1, 4])) + [1, 4]
    return {"learn": [float(x) for x in conf.get("new", {}).get("delays", [])],
            "relearn": [float(x) for x in conf.get("lapse", {}).get("delays", [])],
            "autoplay": bool(conf.get("autoplay", True)), "replayq": bool(conf.get("replayq", True)),
            "labels": {"learn": [_span(col, x) for x in learn], "relearn": [_span(col, x) for x in relearn],
                       "learnHard": _span(col, _hard_first(learn)) if learn else "",
                       "relearnHard": _span(col, _hard_first(relearn)) if relearn else "",
                       "grad": _span(col, int(ints[0]) * 86400), "easy": _span(col, int(ints[1]) * 86400)}}


def _media_names(col, card, mid, qtext, atext):
    names = []
    try:
        names += col.media.files_in_str(mid, qtext + atext)
    except Exception:   # noqa: BLE001  (a broken template must not stop the copy)
        pass
    for tag in list(card.question_av_tags()) + list(card.answer_av_tags()):
        if isinstance(tag, SoundOrVideoTag):
            names.append(tag.filename)
    seen, out = set(), []
    for n in names:
        if n and n not in seen and "/" not in n and "\\" not in n:
            seen.add(n); out.append(n)
    return out


def typeans_plain(col, expected, combining):
    """The expected answer as the engine compares it (HTML, sounds and spaces at the ends removed,
    entities decoded), so the offline page compares typed answers the same way."""
    out = col.compare_answer(expected, "", combining)
    m = re.fullmatch(r"<code id=typeans>(.*)</code>", out, re.S)
    return html.unescape(m.group(1)) if m else html.unescape(re.sub(r"<[^>]*>", "", out))


def render_prefix(col):
    """What every card's rendering depends on besides the card itself: theme, language, add-ons."""
    from flashcards import addons
    return f"{FORMAT}|{core.theme_of(col)}|{i18n.current()}|{','.join(sorted(addons.enabled_ids()))}"


def snapshot(col, did, known=None):
    """One deck's offline copy (see the module docstring). `known`: card id -> render key the browser
    already has; those cards are sent without their question/answer (the browser keeps its own)."""
    known = known or {}
    s = settings(col)
    deck = col.decks.get(did, default=False)
    if not deck:
        return None
    by_day, truncated = pick_cards(col, did, s["days"])
    order = [(k, g, cid) for k in sorted(by_day) for g in ("lrn", "rev", "new") for cid in by_day[k][g]]
    rows = _rows(col, [cid for _, _, cid in order])
    theme = core.theme_of(col)
    prefix = render_prefix(col)
    nt_mod = {}
    confs, cards, media, media_day = {}, [], [], {}
    mdir = col.media.dir()
    for day, group, cid in order:
        if cid not in rows:
            continue
        queue, ctype, due, cmod, mid, content = rows[cid]
        if mid not in nt_mod:   # templates and styling of the note type
            m = col.models.get(mid) or {}
            nt_mod[mid] = hashlib.sha1(json.dumps([m.get("tmpls"), m.get("css"), m.get("flds"), m.get("latexPre"),
                                                   m.get("latexPost")], sort_keys=True).encode()).hexdigest()
        rkey = hashlib.sha1(f"{prefix}|{content}|{nt_mod[mid]}".encode()).hexdigest()[:16]
        card = col.get_card(cid)
        try:
            states = col._backend.get_scheduling_states(cid)
            labels = [_clean_label(x) for x in i18n.describe_next_states(col, states)]
            nxt, rl = next_states(col, states)
        except Exception:   # noqa: BLE001
            labels, nxt, rl = ["", "", "", ""], None, None
        home = card.odid or card.did
        conf = col.decks.config_dict_for_deck_id(home)
        cid_conf = str(conf.get("id", 0))
        if cid_conf not in confs:
            confs[cid_conf] = conf_info(col, conf)
        rec = {"id": cid, "nid": card.nid, "did": card.did, "odid": card.odid, "day": day, "group": group,
               "queue": queue, "type": ctype, "due": due, "mod": cmod, "reps": card.reps, "conf": cid_conf, "labels": labels,
               "next": nxt, "rl": rl, "approx": day > 0, "flag": card.user_flag(), "rkey": rkey}
        qtext, atext = card.question(), card.answer()
        names = _media_names(col, card, mid, qtext, atext)
        rec["media"] = names
        try:   # the typed answer is compared on the device: the engine's prepared expected text
            ti = core.type_answer_info(col, card, qtext)
            rec["tplain"] = ({"text": typeans_plain(col, ti["expected"], ti["combining"]), "combining": ti["combining"]}
                             if ti and ti.get("expected") else None)
        except Exception:   # noqa: BLE001
            rec["tplain"] = None
        for n in names:
            if n not in media_day:
                media_day[n] = day
                media.append(n)
        if known.get(str(cid)) != rkey:
            try:
                question, answer, tinfo = core.study_sides(col, card)
                rec["q"], rec["a"] = core.card_doc(question, theme), core.card_doc(answer, theme)
                rec["av"] = {"q": core.av_list(card.question_av_tags()), "a": core.av_list(card.answer_av_tags())}
                rec["typeans"] = ({"expected": tinfo["expected"], "combining": tinfo["combining"]}
                                  if tinfo and tinfo.get("expected") else None)
                rec["marked"] = card.note().has_tag("marked")
            except Exception:   # noqa: BLE001  (shown as a note instead of the card; the rest still comes)
                msg = html.escape(t("This card couldn't be prepared for offline use."))
                rec["q"] = rec["a"] = core.card_doc(f"<p>{msg}</p>", theme)
                rec["av"], rec["typeans"], rec["marked"] = {"q": [], "a": []}, None, False
        cards.append(rec)
    files = []
    for n in media:
        p = os.path.join(mdir, n)
        try:
            size = os.path.getsize(p) if os.path.isfile(p) else -1
        except OSError:
            size = -1
        if size >= 0:
            files.append({"name": n, "url": "/media/" + quote(n), "size": size, "day": media_day[n]})
    node = col.sched.deck_due_tree(did)
    return {"did": did, "name": deck["name"], "filtered": bool(deck.get("dyn")), "today": col.sched.today,
            "cutoff": col.sched.day_cutoff, "days": s["days"], "generated": int(time.time()),
            "counts": {"new": node.new_count if node else 0, "lrn": node.learn_count if node else 0,
                       "rev": node.review_count if node else 0},
            "queue": {str(k): v for k, v in by_day.items()}, "cards": cards, "media": files,
            "confs": confs, "truncated": truncated, "format": FORMAT,
            "learnAhead": int(col.get_preferences().scheduling.learn_ahead_secs)}


def state(col):
    s = settings(col)
    decks = offline_decks(col, s)
    version = f"{FORMAT}|{int(col.mod)}|{col.sched.today}|{i18n.current()}"
    return {"version": version, "today": col.sched.today, "days": s["days"], "media_mb": s["media_mb"],
            "decks": [{"did": did, "name": name} for did, name in decks],
            "assets": assets() if decks else []}


# ---------- routes ----------
@router.get("/offline/state")
def offline_state(request: Request):
    user = core.current_user(request)
    if not user: return JSONResponse({"error": "login"}, status_code=401)
    with core.collection(user) as col:
        return JSONResponse(state(col), headers={"Cache-Control": "no-store"})


@router.post("/offline/deck/{did}/snapshot")
async def offline_snapshot(request: Request, did: int):
    user = core.current_user(request)
    if not user: return JSONResponse({"error": "login"}, status_code=401)
    known = {}
    try:
        body = await request.json()
        if isinstance(body, dict) and isinstance(body.get("known"), dict):
            known = {str(k): str(v) for k, v in body["known"].items()}
    except Exception:   # noqa: BLE001  (no body = send everything)
        known = {}
    with core.collection(user) as col:
        snap = snapshot(col, did, known)
    if snap is None:
        return JSONResponse({"error": "no such deck"}, status_code=404)
    return JSONResponse(snap, headers={"Cache-Control": "no-store"})


def _back(back, did):
    return back if back.startswith("/") and not back.startswith("//") else f"/deck/{did}"


@router.post("/offline/deck/{did}")
def offline_toggle(request: Request, did: int, on: str = Form(""), back: str = Form("")):
    user = core.current_user(request)
    if not user: return core.need_login(request)
    with core.collection(user) as col:
        if not col.decks.get(did, default=False):
            return RedirectResponse("/", 303)
        on = on == "1"
        set_deck(col, did, on)
    core.set_flash(request, t("This deck will be kept for offline study on your devices.") if on
                   else t("The offline copy of this deck was removed."))
    return RedirectResponse(_back(back, did), 303)


@router.post("/offline/remove_all")
def offline_remove_all(request: Request):
    user = core.current_user(request)
    if not user: return core.need_login(request)
    with core.collection(user) as col:
        s = settings(col)
        s.update(all=False, decks=[], off=[])
        save_settings(col, s)
    core.set_flash(request, t("All offline copies were removed."))
    return RedirectResponse("/preferences", 303)


def save_from_preferences(col, form):
    """Preferences > Offline: "all decks", days ahead and the media limit."""
    s = settings(col)
    all_on = form.get("offline_all") == "1"
    if all_on != s["all"]:
        s["all"], s["off"] = all_on, []
        if all_on:   # top-level decks are covered by "all"; subdecks switched on keep their own copy
            s["decks"] = [d for d in s["decks"] if "::" in (col.decks.name_if_exists(d) or "")]
    try:
        days = int(form.get("offline_days", s["days"]))
        if days in DAYS_CHOICES:
            s["days"] = days
        mb = int(form.get("offline_media", s["media_mb"]))
        if mb in MEDIA_CHOICES:
            s["media_mb"] = mb
    except (TypeError, ValueError):
        pass
    save_settings(col, s)


# ---------- page parts ----------
def plural_table(singular, plural):
    """A plural message for the browser: unique forms plus, for 0..199, which form to use (n >= 200
    uses 100 + n % 100, which picks the same form in every shipped language)."""
    code = i18n.current()
    forms = i18n.catalog(code).get(singular) if code != "en" else None
    if not (isinstance(forms, list) and forms and all(isinstance(x, str) and x for x in forms)):
        code, forms = "en", [singular, plural]
    rule = i18n.PLURAL[code]
    return {"forms": list(forms), "idx": [min(rule(n), len(forms) - 1) for n in range(200)]}


def messages():
    return {
        "unsupported": t("This browser can't keep offline copies here. It needs the app's https:// address "
                         "(or the app on the Home Screen)."),
        "waiting": t("Not downloaded yet. It downloads in the background while this page is open."),
        "working": t("Downloading for offline use…"),
        "ready": t("Offline ready · updated {ago} · {cards}, {size}"),
        "cards": plural_table("{n} card", "{n} cards"),
        "cap": t("The media limit was reached, so some pictures and sounds aren't kept offline. "
                 "You can raise the limit in Preferences."),
        "missing": t("Some files couldn't be downloaded; they'll be tried again."),
        "error": t("Couldn't update the offline copy: {error}"),
        "storage": t("This device: {used} used of about {quota} available to this app."),
        "persisted": t("The browser has agreed to keep this data."),
        "notpersisted": t("The browser may delete this data if the device runs short of space."),
        "truncated": t("Only the first {n} cards are kept offline.", n=MAX_CARDS),
        "none": t("No decks are kept offline on this device."),
        "deck": t("{name}: {cards}, {size}, updated {ago}"),
    }


def overview_box(col, did):
    """The deck overview's Offline box: the switch, and a status line the client script fills in."""
    s = settings(col)
    on = deck_is_offline(s, did)
    is_sub = "::" in (col.decks.name_if_exists(did) or "")
    covered = s["all"] and not is_sub and did not in s["off"]
    msgs = html.escape(core.js(messages()))
    hint = (t("All decks are kept offline (Preferences).") if covered
            else t("Keep today's cards, and the next days' ones, on this device to study without a connection."))
    remove = (f'<form method="post" action="/offline/deck/{did}" id="fc-off-remove" style="display:none">'
              f'<input type="hidden" name="on" value="0"><button class="danger">{core.h(t("Remove offline copy"))}'
              f'</button></form>')
    return f"""<div class="box" id="fc-off" data-did="{did}" data-on="{1 if on else 0}" data-msgs="{msgs}">
<form method="post" action="/offline/deck/{did}" class="row" style="justify-content:space-between">
<input type="hidden" name="on" value="{0 if on else 1}">
<label class="fc-switch"><input type="checkbox" role="switch"{" checked" if on else ""} onchange="this.form.requestSubmit?this.form.requestSubmit():this.form.submit()">
<span><b>{core.h(t("Make available offline"))}</b><br><span class="muted" style="font-size:14px">{core.h(hint)}</span></span></label>
<noscript><button>{core.h(t("Save"))}</button></noscript></form>
<p class="muted" id="fc-off-status" style="font-size:14px;margin:8px 0 0"></p>
<p class="muted" id="fc-off-storage" style="font-size:14px;margin:4px 0 0"></p>{remove}</div>"""


SWITCH_CSS = """<style>.fc-switch{display:flex;gap:12px;align-items:center;margin:0;color:var(--ink);font-size:17px;
cursor:pointer;min-height:44px;flex:1}.fc-switch input{appearance:none;-webkit-appearance:none;flex:none;width:52px;
height:32px;min-height:32px;padding:0;border-radius:16px;background:var(--line);position:relative;border:0;cursor:pointer;
transition:background .15s}.fc-switch input::after{content:"";position:absolute;top:3px;left:3px;width:26px;height:26px;
border-radius:50%;background:#fff;box-shadow:0 1px 3px rgba(0,0,0,.3);transition:left .15s}
.fc-switch input:checked{background:var(--good)}.fc-switch input:checked::after{left:23px}
#fc-off-remove{margin-top:10px}.fc-off-warn{color:var(--again)}</style>"""


def preferences_box(col):
    s = settings(col)
    e = lambda x: html.escape(x, False)
    msgs = html.escape(core.js(messages()))
    day_opts = "".join(f'<option value="{d}"{" selected" if d == s["days"] else ""}>'
                       f'{e(t("Today only") if d == 0 else tn("Today + {n} day", "Today + {n} days", d))}</option>'
                       for d in DAYS_CHOICES)
    mb_opts = "".join(f'<option value="{m}"{" selected" if m == s["media_mb"] else ""}>{m} MB</option>'
                      for m in MEDIA_CHOICES)
    return f"""<div class="box" id="fc-off-prefs" data-msgs="{msgs}"><h3>{e(t("Offline"))}</h3>
<label class="opt"><input type="checkbox" name="offline_all" value="1"{" checked" if s["all"] else ""}>
<span>{e(t("Make all decks available offline"))}<br><span class="muted hint">{e(t(
        "Each device keeps a copy while the app is open with a connection. Single decks can be switched "
        "on or off on their overview page."))}</span></span></label>
<label for="offline_days">{e(t("Cards to keep"))}</label><select id="offline_days" name="offline_days">{day_opts}</select>
<label for="offline_media">{e(t("Media limit"))}</label><select id="offline_media" name="offline_media">{mb_opts}</select>
<div class="muted hint">{e(t("Pictures and sounds beyond this are not kept. iPad Safari gives web apps limited space; "
                              "keep this low there."))}</div>
<div id="fc-off-list" class="muted hint" style="margin-top:12px"></div>
<p class="muted hint" id="fc-off-storage"></p>
<button type="submit" form="fc-off-removeall" class="danger">{e(t("Remove all offline copies"))}</button></div>"""


def preferences_forms():
    """Forms that can't sit inside the Preferences form (HTML forms can't nest)."""
    return ('<form method="post" action="/offline/remove_all" id="fc-off-removeall" '
            f'onsubmit="return confirm({html.escape(core.js(t("Remove every offline copy from all your devices?")))})"></form>')


# ---------- bundled files (MathJax), also kept for offline use ----------
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
STATIC_TYPES = {".js": "text/javascript", ".woff": "font/woff", ".txt": "text/plain"}


def assets():
    """Files the offline study page may need: the bundled MathJax and its fonts."""
    out = []
    root = os.path.join(STATIC_DIR, "mathjax")
    for folder, _dirs, files in os.walk(root):
        for f in sorted(files):
            if f.endswith((".js", ".woff")):
                out.append("/static/" + os.path.relpath(os.path.join(folder, f), STATIC_DIR).replace(os.sep, "/"))
    return sorted(out)


@router.get("/static/{path:path}")
def static_file(path: str):
    """Bundled libraries (no login: nothing personal). Paths outside the folder are refused."""
    root = os.path.realpath(STATIC_DIR) + os.sep
    full = os.path.realpath(os.path.join(STATIC_DIR, path))
    ext = os.path.splitext(full)[1].lower()
    if not full.startswith(root) or not os.path.isfile(full) or ext not in STATIC_TYPES:
        return Response(status_code=404)
    return FileResponse(full, media_type=STATIC_TYPES[ext], headers={"Cache-Control": "public, max-age=86400"})


# ---------- part 2: the offline study page ----------
STUDY_JS_PATH = os.path.join(os.path.dirname(__file__), "offline_study.js")


def study_messages():
    return {
        "title": t("Study offline"), "offline": t("Offline"),
        "waiting": plural_table("{n} answer waiting to sync", "{n} answers waiting to sync"),
        "none": t("No decks are kept offline on this device."),
        "unsupported": t("This browser can't keep offline copies here. It needs the app's https:// address "
                         "(or the app on the Home Screen)."),
        "nouser": t("Log in once with a connection, and switch on \"Make available offline\" for a deck."),
        "notoffline": t("This deck isn't kept offline on this device."),
        "congrats": t("Congratulations! You have finished this deck for now."),
        "readyIn": t("The next learning card will be ready in {time}."),
        "minutes": plural_table("{n} minute", "{n} minutes"),
        "hours": plural_table("{n} hour", "{n} hours"),
        "approx": t("~ = worked out on this device; the server will work out the exact interval."),
        "broken": t("This card couldn't be prepared for offline use."),
        "tapPlay": t("Tap 🔊 to play the audio"),
        "tapHint": t("Tap the card: left half Again · right half Good"),
        "undone": t("The last offline answer was undone."),
        "error": t("Couldn't open the offline copy on this device."),
        "updated": t("Offline copy updated {ago}"),
        # part 3: adding notes offline, sending everything back
        "addsWaiting": plural_table("{n} note waiting to sync", "{n} notes waiting to sync"),
        "synced": t("Back online: everything studied offline was saved."),
        "defaultDeck": t("Default deck"),
        "firstEmpty": t("The first field is empty."),
        "noCloze": t("Add a cloze like {{c1::this}} to one of the fields."),
        "added": t("Added. It will be saved on the server when the connection is back."),
    }


def study_page(request):
    """The offline study page: a deck list of the offline copies and the study screen, run entirely in
    the browser from IndexedDB (flashcards/offline_study.js). Served like any page while online, and
    kept by the offline script in Cache Storage so the service worker can show it without a connection
    (for whichever address failed: /, /study/<deck>, ...). It works without a login: the person whose
    copy it opens is the last one who used the app on this device. Their settings (theme, language,
    answer-button times, tap zones, whiteboard) are the ones of the person logged in when it was saved."""
    from flashcards import whiteboard, touch
    user = core.current_user(request)
    prefs = {"times": True, "counts": True, "tap": False}
    wb, theme = dict(whiteboard.DEFAULT), "system"
    types = []
    if user:
        with core.collection(user) as col:
            r = col.get_preferences().reviewing
            prefs = {"times": bool(r.show_intervals_on_buttons), "counts": bool(r.show_remaining_due_counts),
                     "tap": touch.tap_zones(col)}
            wb, theme = whiteboard.settings(col), core.theme_of(col)
            from flashcards import offline_sync
            types = offline_sync.add_types(col)
    e = core.h
    root = f' class="{theme}"' if theme in ("light", "dark") else ""
    lang = i18n.current()
    root += "" if lang == "en" else f' lang="{lang}"'
    names = [t("Again"), t("Hard"), t("Good"), t("Easy")]
    colors = ["again", "hard", "good", "easy"]
    btns = "".join(f'<button class="ans" style="border-color:var(--{c});color:var(--{c})" data-r="{i + 1}">'
                   f'<small></small><br>{e(n)}</button>' for i, (n, c) in enumerate(zip(names, colors)))
    cfg = {"msgs": study_messages(), "prefs": prefs, "slot": core.TYPE_SLOT, "user": user or "",
           "addTypes": types}
    with open(STUDY_JS_PATH, encoding="utf-8") as f:
        study_js = f.read()
    wb_wiring = (f"<script>{whiteboard.JS}\nwindow.WB=fcWhiteboard({{canvas:document.getElementById('wb'),"
                 f"frame:document.getElementById('frame'),bar:document.getElementById('wbbar'),"
                 f"toggle:document.getElementById('wbtoggle'),conf:{json.dumps(wb)},tap:(x,y)=>window.fcoTap(x,y),"
                 "dark:()=>{const c=document.documentElement.classList;return c.contains('dark')||"
                 "(!c.contains('light')&&matchMedia('(prefers-color-scheme:dark)').matches);},"
                 "save:()=>{}});</script>")
    body = f"""<div id="fco-top"><header><b>Flashcards</b><a href="/" id="fco-home">{e(t("Decks"))}</a><a href="/add" id="fco-add-link" data-nav="/add">{e(t("Add"))}</a></header>
<div id="fco-banner" role="status" hidden></div></div>
<main>
<div id="fco-flash" class="flash" hidden></div>
<section id="fco-list" hidden>
<h2 style="margin-top:0">{e(t("Study offline"))}</h2>
<p class="muted" style="font-size:15px">{e(t("Studying and adding notes (text only) work without a connection. Browse and the other pages need the server."))}</p>
<div class="box" style="padding:6px 18px"><table><thead><tr><th>{e(t("Deck"))}</th><th class="n">{e(t("New"))}</th>
<th class="n">{e(t("Learn"))}</th><th class="n">{e(t("Due"))}</th></tr></thead><tbody id="fco-decks"></tbody></table>
<p id="fco-empty" class="muted" hidden></p></div>
</section>
<section id="fco-add" hidden>
<h2 style="margin-top:0">{e(t("Add"))}</h2>
<p class="muted" style="font-size:15px">{e(t("Text only. Notes added here are saved on the server when the connection is back."))}</p>
<p id="fco-add-none" class="muted" hidden>{e(t("Open the app once with a connection to add notes offline."))}</p>
<div class="box" id="fco-add-form">
<div class="row" style="flex-wrap:wrap"><label>{e(t("Note type"))} <select id="fco-nt"></select></label>
<label>{e(t("Deck"))} <select id="fco-deck"></select></label></div>
<div id="fco-fields"></div>
<p id="fco-cloze-hint" class="muted" hidden>{e(t("Cloze: type {{c1::the hidden text}}; use c2, c3 … for more cards."))}</p>
<label class="fco-flabel" for="fco-tags">{e(t("Tags"))}</label><input id="fco-tags" autocomplete="off" autocapitalize="off">
<div class="row" style="margin-top:14px"><button type="button" class="primary" id="fco-add-btn">{e(t("Add"))}</button>
<a href="/offline/study" class="btn" data-nav>{e(t("Study offline"))}</a></div>
</div>
</section>
<section id="fco-wait" hidden><div class="row" style="margin-bottom:10px"><a href="/offline/study" class="btn" data-nav>{e(t("← Deck"))}</a>
<button type="button" class="fco-undo" hidden>{e(t("Undo"))}</button></div>
<div class="box" style="text-align:center"><h2 style="margin-top:0">{e(t("Nothing due right now"))}</h2>
<p id="fco-wait-text"></p><p class="muted">{e(t("Keep this screen open and it will carry on by itself."))}</p>
<button type="button" class="primary" id="fco-check">{e(t("Check again"))}</button></div></section>
<section id="fco-study" hidden>
<div class="row" style="justify-content:space-between;margin-bottom:10px">
<a href="/offline/study" class="btn" data-nav>{e(t("← Deck"))}</a>
<div class="counts" id="fco-counts"><span class="new"></span><span class="lrn"></span><span class="rev"></span></div>
<div class="row"><button type="button" class="fco-undo" hidden>{e(t("Undo"))}</button>
<button type="button" id="replay" hidden title="{html.escape(t("Replay audio (R)"))}" aria-label="{html.escape(t("Replay audio"))}">🔊</button>
{whiteboard.toggle_html(wb)}</div></div>
<div id="toast"></div>{whiteboard.bar_html(wb)}
<div id="wbwrap"><iframe id="frame" sandbox="allow-same-origin allow-scripts"></iframe><canvas id="wb" style="display:none"></canvas></div>
<div class="bar"><p id="taphint" class="muted"{"" if prefs["tap"] else " hidden"}>{e(t("Tap the card to show the answer"))}</p>
<button id="show" class="primary" style="width:100%;min-height:64px">{e(t("Show answer"))}</button>
<div id="btns" class="row" style="display:none;flex-wrap:nowrap">{btns}</div>
<p id="fco-approx" class="muted" hidden></p></div>
</section>
</main>
<script>window.FCO={core.js(cfg)};</script>
<script src="/offline/client.js" data-user="{html.escape(user or "")}" data-page="study"></script>
<script>{study_js}</script>{wb_wiring}"""
    return f"""<!doctype html><html{root}><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes"><meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="Flashcards"><link rel="manifest" href="/manifest.json">
{touch.head_html(theme)}<title>{e(t("Study offline"))}</title><style>{core.CSS}{touch.CSS}{core.STUDY_CSS}{whiteboard.CSS}
{STUDY_PAGE_CSS}</style></head><body>{body}</body></html>"""


STUDY_PAGE_CSS = """#fco-top{position:sticky;top:0;z-index:6}#fco-top header{position:static}
#fco-banner{background:#8a5a00;color:#fff;text-align:center;padding:8px 16px;font-size:15px}#fco-decks a{min-height:44px;display:flex;align-items:center}
#fco-decks td{padding:4px 8px}#fco-approx{font-size:13px;text-align:center;margin:6px 0 0}
.counts span:empty{display:none}
.fco-flabel{display:block;font-weight:600;margin:12px 0 4px}#fco-fields textarea,#fco-tags{width:100%;font:inherit;
padding:10px;border:1px solid var(--line);border-radius:10px;background:var(--card);color:var(--ink);min-height:44px}
#fco-add select{min-height:44px;font:inherit}"""


@router.get("/offline/study")
def offline_study(request: Request):
    return HTMLResponse(study_page(request), headers={"Cache-Control": "no-cache"})


# ---------- the browser side ----------
CLIENT_JS_PATH = os.path.join(os.path.dirname(__file__), "offline_client.js")


@router.get("/offline/client.js")
def client_js():
    with open(CLIENT_JS_PATH, encoding="utf-8") as f:
        return Response(f.read(), media_type="text/javascript", headers={"Cache-Control": "no-cache"})


def script_tag(user):
    return f'<script src="/offline/client.js" defer data-user="{html.escape(user)}"></script>'
