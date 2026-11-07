"""Deck options page: every main field of Anki's deck options screen, presets, FSRS optimizer.

Reads and writes go through the engine's own deck options calls
(`get_deck_configs_for_update` / `update_deck_configs`), the same ones Anki's
options screen uses, so validation, "apply to subdecks", preset removal and
FSRS memory-state updates behave as in Anki.
"""
import datetime, html, json, re

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from anki.decks import UpdateDeckConfigs
from anki import deck_config_pb2

import app as core
from flashcards.i18n import t, tn, N_

router = APIRouter()
DC = deck_config_pb2.DeckConfig
Cfg = DC.Config
Mode = deck_config_pb2.UpdateDeckConfigsMode

# ---------- choice lists (labels as in Anki's English UI; translated where shown) ----------
INSERT_ORDER = [(Cfg.NEW_CARD_INSERT_ORDER_DUE, N_("Sequential (oldest cards first)")),
                (Cfg.NEW_CARD_INSERT_ORDER_RANDOM, N_("Random"))]
GATHER = [(Cfg.NEW_CARD_GATHER_PRIORITY_DECK, N_("Deck")),
          (Cfg.NEW_CARD_GATHER_PRIORITY_DECK_THEN_RANDOM_NOTES, N_("Deck, then random notes")),
          (Cfg.NEW_CARD_GATHER_PRIORITY_LOWEST_POSITION, N_("Ascending position")),
          (Cfg.NEW_CARD_GATHER_PRIORITY_HIGHEST_POSITION, N_("Descending position")),
          (Cfg.NEW_CARD_GATHER_PRIORITY_RANDOM_NOTES, N_("Random notes")),
          (Cfg.NEW_CARD_GATHER_PRIORITY_RANDOM_CARDS, N_("Random cards"))]
SORT = [(Cfg.NEW_CARD_SORT_ORDER_TEMPLATE, N_("Card type, then order gathered")),
        (Cfg.NEW_CARD_SORT_ORDER_NO_SORT, N_("Order gathered")),
        (Cfg.NEW_CARD_SORT_ORDER_TEMPLATE_THEN_RANDOM, N_("Card type, then random")),
        (Cfg.NEW_CARD_SORT_ORDER_RANDOM_NOTE_THEN_TEMPLATE, N_("Random note, then card type")),
        (Cfg.NEW_CARD_SORT_ORDER_RANDOM_CARD, N_("Random"))]
MIX = [(Cfg.REVIEW_MIX_MIX_WITH_REVIEWS, N_("Mix with reviews")),
       (Cfg.REVIEW_MIX_AFTER_REVIEWS, N_("Show after reviews")),
       (Cfg.REVIEW_MIX_BEFORE_REVIEWS, N_("Show before reviews"))]
REVIEW_ORDER = [(Cfg.REVIEW_CARD_ORDER_DAY, N_("Due date, then random")),
                (Cfg.REVIEW_CARD_ORDER_DAY_THEN_DECK, N_("Due date, then deck")),
                (Cfg.REVIEW_CARD_ORDER_DECK_THEN_DAY, N_("Deck, then due date")),
                (Cfg.REVIEW_CARD_ORDER_INTERVALS_ASCENDING, N_("Ascending intervals")),
                (Cfg.REVIEW_CARD_ORDER_INTERVALS_DESCENDING, N_("Descending intervals")),
                (Cfg.REVIEW_CARD_ORDER_EASE_ASCENDING, N_("Ascending ease / difficulty")),
                (Cfg.REVIEW_CARD_ORDER_EASE_DESCENDING, N_("Descending ease / difficulty")),
                (Cfg.REVIEW_CARD_ORDER_RETRIEVABILITY_ASCENDING, N_("Ascending retrievability")),
                (Cfg.REVIEW_CARD_ORDER_RETRIEVABILITY_DESCENDING, N_("Descending retrievability")),
                (Cfg.REVIEW_CARD_ORDER_RELATIVE_OVERDUENESS, N_("Relative overdueness")),
                (Cfg.REVIEW_CARD_ORDER_RANDOM, N_("Random")),
                (Cfg.REVIEW_CARD_ORDER_ADDED, N_("Order added")),
                (Cfg.REVIEW_CARD_ORDER_REVERSE_ADDED, N_("Reverse order added"))]
LEECH = [(Cfg.LEECH_ACTION_SUSPEND, N_("Suspend card")), (Cfg.LEECH_ACTION_TAG_ONLY, N_("Tag only"))]

# Plain number fields: form name -> (label, kind, min, max, help)
INT, FLOAT = int, float
NUMBERS = {
    "new_per_day": (N_("New cards/day"), INT, 0, 9999, ""),
    "reviews_per_day": (N_("Maximum reviews/day"), INT, 0, 9999, ""),
    "graduating_interval_good": (N_("Graduating interval (days)"), INT, 1, 36500, ""),
    "graduating_interval_easy": (N_("Easy interval (days)"), INT, 1, 36500, ""),
    "minimum_lapse_interval": (N_("Minimum interval (days)"), INT, 1, 36500, ""),
    "leech_threshold": (N_("Leech threshold (lapses)"), INT, 1, 9999, ""),
    "maximum_review_interval": (N_("Maximum interval (days)"), INT, 1, 36500, ""),
    "initial_ease": (N_("Starting ease"), FLOAT, 1.31, 5.0, N_("SM-2 only. 2.50 = 250%.")),
    "easy_multiplier": (N_("Easy bonus"), FLOAT, 1.0, 5.0, N_("SM-2 only.")),
    "hard_multiplier": (N_("Hard interval"), FLOAT, 0.5, 1.3, N_("SM-2 only.")),
    "lapse_multiplier": (N_("New interval (after a lapse)"), FLOAT, 0.0, 1.0, N_("SM-2 only.")),
    "interval_multiplier": (N_("Interval modifier"), FLOAT, 0.5, 2.0, N_("SM-2 only.")),
    "desired_retention": (N_("Desired retention"), FLOAT, 0.7, 0.99, N_("FSRS only.")),
    "cap_answer_time_to_secs": (N_("Maximum answer seconds"), INT, 30, 600, ""),
}
CHOICES = {"new_card_insert_order": INSERT_ORDER, "new_card_gather_priority": GATHER,
           "new_card_sort_order": SORT, "new_mix": MIX, "interday_learning_mix": MIX,
           "review_order": REVIEW_ORDER, "leech_action": LEECH}
CHECKS = ["bury_new", "bury_reviews", "bury_interday_learning", "disable_autoplay",
          "skip_question_when_replaying_answer"]
# Old quick form on the overview page used these names.
ALIASES = {"rev_per_day": "reviews_per_day", "retention": "desired_retention"}


# ---------- learning steps ("1m 10m 1h 1d", stored in minutes) ----------
UNITS = {"s": 1 / 60, "m": 1, "h": 60, "d": 1440}

def parse_steps(text):
    steps = []
    for part in text.replace(",", " ").split():
        m = re.fullmatch(r"(\d+(?:\.\d+)?)([smhd]?)", part.strip().lower())
        if not m or float(m.group(1)) <= 0:
            raise ValueError(t("“{part}” is not a valid step. Use values like 1m 10m 1h 1d.", part=part))
        steps.append(float(m.group(1)) * UNITS[m.group(2) or "m"])
    return steps

def fmt_steps(steps):
    out = []
    for mins in steps:
        for unit, size in (("d", 1440), ("h", 60), ("m", 1)):
            if mins >= size and abs(mins / size - round(mins / size)) < 1e-6:
                out.append(f"{round(mins / size)}{unit}"); break
        else:
            out.append(f"{round(mins * 60)}s")
    return " ".join(out)

def current_params(c):
    return list(c.fsrs_params_6 or c.fsrs_params_5 or c.fsrs_params_4)

def fmt_params(params):
    return ", ".join(f"{p:.4f}".rstrip("0").rstrip(".") for p in params)

def parse_params(text):
    try:
        return [float(x) for x in re.split(r"[,\s]+", text.strip()) if x]
    except ValueError:
        raise ValueError(t("FSRS parameters must be numbers separated by commas."))


# ---------- engine helpers ----------
def load(col, did):
    return col.decks.get_deck_configs_for_update(did)

def find_conf(data, cid):
    for c in data.all_config:
        if c.config.id == cid:
            return c.config
    return None

def save(col, did, data, configs, removed=(), mode=Mode.UPDATE_DECK_CONFIGS_MODE_NORMAL,
         limits=None, fsrs=None, reschedule=False, ignore_limit=None, parent_limits=None):
    """Save `configs` (the last one is given to the deck) the way Anki's options screen does."""
    req = UpdateDeckConfigs(
        target_deck_id=did, configs=configs, removed_config_ids=list(removed), mode=mode,
        card_state_customizer=data.card_state_customizer,
        limits=limits if limits is not None else data.current_deck.limits,
        new_cards_ignore_review_limit=data.new_cards_ignore_review_limit if ignore_limit is None else ignore_limit,
        apply_all_parent_limits=data.apply_all_parent_limits if parent_limits is None else parent_limits,
        fsrs=data.fsrs if fsrs is None else fsrs, fsrs_reschedule=reschedule,
        fsrs_health_check=data.fsrs_health_check)
    col.decks.update_deck_configs(req)

def unique_name(data, name):
    names = {c.config.name for c in data.all_config}
    base, n = name, 2
    while name in names:
        name = f"{base} {n}"; n += 1
    return name

def options_url(did, preset=0):
    return f"/deck/{did}/options" + (f"?preset={preset}" if preset else "")


# ---------- page ----------
def sel(name, choices, value):
    opts = "".join(f'<option value="{v}"{" selected" if v == value else ""}>{html.escape(t(label))}</option>'
                   for v, label in choices)
    return f'<select name="{name}" id="{name}">{opts}</select>'

def num(name, value, c):
    label, kind, lo, hi, helptext = NUMBERS[name]
    step = "1" if kind is INT else "0.01"
    shown = int(value) if kind is INT else round(value, 4)
    h = f'<div class="muted" style="font-size:13px">{t(helptext)}</div>' if helptext else ""
    return (f'<div class="opt"><label for="{name}">{t(label)}</label><input id="{name}" name="{name}" '
            f'type="number" inputmode="decimal" step="{step}" min="{lo}" max="{hi}" value="{shown}">{h}</div>')

def check(name, label, on):
    return (f'<label class="chk"><input type="checkbox" name="{name}" value="1"{" checked" if on else ""}>'
            f' {label}</label><input type="hidden" name="has_{name}" value="1">')

def text(name, label, value, helptext="", placeholder=""):
    h = f'<div class="muted" style="font-size:13px">{helptext}</div>' if helptext else ""
    return (f'<div class="opt"><label for="{name}">{label}</label><input id="{name}" name="{name}" '
            f'value="{html.escape(value)}" placeholder="{html.escape(placeholder)}">{h}</div>')

OPT_CSS = """<style>.opt{flex:1;min-width:220px}.grid{display:flex;gap:4px 14px;flex-wrap:wrap}
.chk{display:flex;gap:10px;align-items:center;color:var(--ink);font-size:16px;min-height:44px;margin:4px 0}
.chk input{width:22px;height:22px;flex:none}.box h3{margin-top:0}
details summary{cursor:pointer;min-height:44px;display:flex;align-items:center;font-weight:600}
.savebar{position:sticky;bottom:0;background:var(--bg);padding:12px 0;border-top:1px solid var(--line)}
</style>"""

def options_html(col, did, preset):
    deck = col.decks.get(did)
    data = load(col, did)
    deck_conf_id = data.current_deck.config_id
    conf = find_conf(data, preset) or find_conf(data, deck_conf_id)
    c = conf.config
    lim = data.current_deck.limits
    name = html.escape(deck["name"])
    uses = {x.config.id: x.use_count for x in data.all_config}
    presets = "".join(
        f'<option value="{x.config.id}"{" selected" if x.config.id == conf.id else ""}>'
        + tn("{name} (used by {n} deck)", "{name} (used by {n} decks)", x.use_count,
             name=html.escape(x.config.name)) + '</option>'
        for x in sorted(data.all_config, key=lambda x: x.config.name.lower()))
    not_used_here = conf.id != deck_conf_id
    switch_note = ('<p class="flash" style="margin:12px 0 0">'
                   + t("Saving will switch this deck to the preset “{name}”.", name=html.escape(conf.name))
                   + '</p>' if not_used_here else "")
    shared = uses.get(conf.id, 0) - (0 if not_used_here else 1)
    shared_note = ('<p class="muted" style="font-size:14px;margin:8px 0 0">'
                   + tn("Changes also affect {n} other deck using this preset.",
                        "Changes also affect {n} other decks using this preset.", shared)
                   + '</p>' if shared > 0 else "")
    lim_new = str(lim.new) if lim.HasField("new") else ""
    lim_rev = str(lim.review) if lim.HasField("review") else ""
    fsrs = data.fsrs
    sm2_note = ('<p class="muted" style="font-size:14px">' + t("FSRS is on, so these are not used.") + '</p>'
                if fsrs else "")
    del_confirm = html.escape(json.dumps(t("Delete the preset “{name}”? Decks using it will go back to Default.",
                                           name=conf.name)), quote=True)
    use_preset = html.escape(t("Use preset"))
    ignore_date = c.ignore_revlogs_before_date
    default_search = f'preset:"{conf.name}" -is:suspended'
    has_children = bool(col.decks.children(did))
    pid = conf.id

    body = f"""{OPT_CSS}
<p><a href="/deck/{did}">← {name}</a></p>
<h2 style="margin:4px 0 14px">{t("Options for {name}", name=name)}</h2>
<div class="box"><h3>{t("Preset")}</h3>
<div class="row"><select id="presetpick" style="flex:1;min-width:220px" aria-label="{html.escape(t("Preset"))}"
onchange="location='/deck/{did}/options?preset='+this.value">{presets}</select></div>
{switch_note}{shared_note}
<div class="row" style="margin-top:12px">
<form method="post" action="/deck/{did}/options/preset" class="row" style="flex:1;min-width:260px">
<input type="hidden" name="preset" value="{pid}"><input name="name" placeholder="{html.escape(t("Name for new / renamed preset"))}" style="flex:1">
<button name="op" value="add">{t("Add")}</button><button name="op" value="clone">{t("Clone")}</button>
<button name="op" value="rename">{t("Rename")}</button></form>
{'' if pid == 1 else f'''<form method="post" action="/deck/{did}/options/preset" onsubmit="return confirm({del_confirm})">
<input type="hidden" name="preset" value="{pid}"><button name="op" value="delete" class="danger">{t("Delete")}</button></form>'''}
</div></div>

<form method="post" action="/deck/{did}/options" id="optform">
<input type="hidden" name="preset_id" value="{pid}"><input type="hidden" name="full" value="1">
<div class="box"><h3>{t("Daily limits")}</h3><div class="grid">
{num("new_per_day", c.new_per_day, c)}{num("reviews_per_day", c.reviews_per_day, c)}</div>
<div class="grid">
<div class="opt"><label for="limit_new">{t("This deck only: new cards/day")}</label>
<input id="limit_new" name="limit_new" type="number" min="0" value="{lim_new}" placeholder="{use_preset}"></div>
<div class="opt"><label for="limit_review">{t("This deck only: maximum reviews/day")}</label>
<input id="limit_review" name="limit_review" type="number" min="0" value="{lim_rev}" placeholder="{use_preset}"></div></div>
{check("new_cards_ignore_review_limit", t("New cards ignore review limit"), data.new_cards_ignore_review_limit)}
{check("apply_all_parent_limits", t("Limits start from top"), data.apply_all_parent_limits)}
</div>

<div class="box"><h3>{t("New cards")}</h3><div class="grid">
{text("learn_steps", t("Learning steps"), fmt_steps(c.learn_steps), t("Separated by spaces, e.g. 1m 10m 1h 1d."))}
{num("graduating_interval_good", c.graduating_interval_good, c)}
{num("graduating_interval_easy", c.graduating_interval_easy, c)}
<div class="opt"><label for="new_card_insert_order">{t("Insertion order")}</label>{sel("new_card_insert_order", INSERT_ORDER, c.new_card_insert_order)}
<div class="muted" style="font-size:13px">{t("Changing this repositions existing new cards.")}</div></div>
</div></div>

<div class="box"><h3>{t("Lapses")}</h3><div class="grid">
{text("relearn_steps", t("Relearning steps"), fmt_steps(c.relearn_steps), t("Blank for none."))}
{num("minimum_lapse_interval", c.minimum_lapse_interval, c)}
{num("leech_threshold", c.leech_threshold, c)}
<div class="opt"><label for="leech_action">{t("Leech action")}</label>{sel("leech_action", LEECH, c.leech_action)}</div>
</div></div>

<div class="box"><h3>{t("Display order")}</h3><div class="grid">
<div class="opt"><label for="new_card_gather_priority">{t("New card gather order")}</label>{sel("new_card_gather_priority", GATHER, c.new_card_gather_priority)}</div>
<div class="opt"><label for="new_card_sort_order">{t("New card sort order")}</label>{sel("new_card_sort_order", SORT, c.new_card_sort_order)}</div>
<div class="opt"><label for="new_mix">{t("New/review order")}</label>{sel("new_mix", MIX, c.new_mix)}</div>
<div class="opt"><label for="interday_learning_mix">{t("Interday learning/review order")}</label>{sel("interday_learning_mix", MIX, c.interday_learning_mix)}</div>
<div class="opt"><label for="review_order">{t("Review sort order")}</label>{sel("review_order", REVIEW_ORDER, c.review_order)}</div>
</div></div>

<div class="box"><h3>{t("Burying")}</h3>
{check("bury_new", t("Bury new siblings"), c.bury_new)}
{check("bury_reviews", t("Bury review siblings"), c.bury_reviews)}
{check("bury_interday_learning", t("Bury interday learning siblings"), c.bury_interday_learning)}
</div>

<div class="box"><h3>{t("Audio and timer")}</h3>
{check("disable_autoplay", t("Don’t play audio automatically"), c.disable_autoplay)}
{check("skip_question_when_replaying_answer", t("Skip question when replaying answer"), c.skip_question_when_replaying_answer)}
<div class="grid">{num("cap_answer_time_to_secs", c.cap_answer_time_to_secs, c)}</div>
</div>

<div class="box" id="fsrs"><h3>FSRS</h3>
{check("fsrs", t("Use FSRS (applies to the whole collection)"), fsrs)}
<div class="grid">{num("desired_retention", c.desired_retention, c)}
{text("ignore_revlogs_before_date", t("Ignore reviews before"), ignore_date, t("YYYY-MM-DD. Used by Optimize."), t("e.g. 2024-01-01"))}</div>
{text("param_search", t("Optimize search"), c.param_search, t("Which reviews Optimize learns from."), default_search)}
<div class="opt"><label for="fsrs_params">{t("FSRS parameters")}</label>
<textarea id="fsrs_params" name="fsrs_params" style="min-height:70px" placeholder="{html.escape(t("Blank = defaults"))}">{fmt_params(current_params(c))}</textarea></div>
{check("fsrs_reschedule", t("Reschedule cards on change"), False)}
<div class="row" style="margin-top:10px"><button formaction="/deck/{did}/options/optimize" formnovalidate>{t("Optimize")}</button>
<span class="muted" style="font-size:14px">{t("Saves the form, then fits parameters to your review history with Anki's optimizer.")}</span></div>
</div>

<div class="box"><details{' open' if not fsrs else ''}><summary>{t("Advanced (SM-2)")}</summary>{sm2_note}<div class="grid">
{num("maximum_review_interval", c.maximum_review_interval, c)}
{num("initial_ease", c.initial_ease, c)}{num("easy_multiplier", c.easy_multiplier, c)}
{num("hard_multiplier", c.hard_multiplier, c)}{num("lapse_multiplier", c.lapse_multiplier, c)}
{num("interval_multiplier", c.interval_multiplier, c)}</div></details></div>

<div class="savebar row"><button class="primary">{t("Save")}</button>
{check("apply_children", t("Save to all subdecks"), False) if has_children else ""}
<a class="btn" href="/deck/{did}">{t("Cancel")}</a></div>
</form>"""
    return body


@router.get("/deck/{did}/options", response_class=HTMLResponse)
def options_page(request: Request, did: int, preset: int = 0):
    user = core.current_user(request)
    if not user: return core.need_login(request)
    with core.collection(user) as col:
        deck = col.decks.get(did, default=False)
        if not deck: return RedirectResponse("/", 303)
        if deck.get("dyn"):
            core.set_flash(request, "!" + t("Filtered decks have no options preset."))
            return RedirectResponse(f"/deck/{did}", 303)
        body = options_html(col, did, preset)
        title = t("Options: {name}", name=deck["name"])
    return core.page(title, body, user, "decks", core.take_flash(request))


def apply_form(c, form):
    """Copy submitted fields onto the protobuf config. Missing fields are left as they are."""
    f = {ALIASES.get(k, k): v for k, v in form.items()}
    for name, (label, kind, lo, hi, _) in NUMBERS.items():
        if name not in f or str(f[name]).strip() == "":
            continue
        try:
            v = kind(float(f[name])) if kind is INT else float(f[name])
        except ValueError:
            raise ValueError(t("{label}: “{value}” is not a number.", label=t(label), value=f[name]))
        setattr(c, name, min(hi, max(lo, v)))
    for name, choices in CHOICES.items():
        if name in f:
            v = int(f[name])
            if v not in [x for x, _ in choices]:
                raise ValueError(t("Unknown choice for {name}.", name=name))
            setattr(c, name, v)
    for name in CHECKS:
        if f"has_{name}" in f:
            setattr(c, name, name in f)
    for name in ("learn_steps", "relearn_steps"):
        if name in f:
            steps = parse_steps(f[name])
            if name == "learn_steps" and not steps:
                raise ValueError(t("Learning steps can't be empty. Use at least one step, e.g. 1m."))
            getattr(c, name)[:] = steps
    if "ignore_revlogs_before_date" in f:
        d = f["ignore_revlogs_before_date"].strip()
        if d:
            try: datetime.date.fromisoformat(d)
            except ValueError: raise ValueError(t("“Ignore reviews before” must be a date like 2024-01-01."))
        c.ignore_revlogs_before_date = d
    if "param_search" in f:
        c.param_search = f["param_search"].strip()
    if "fsrs_params" in f:
        params = parse_params(f["fsrs_params"])
        if params != [float(f"{p:.4f}") for p in current_params(c)]:
            if params and len(params) not in (17, 19, 21):
                raise ValueError(t("FSRS needs 17, 19 or 21 parameters (got {n}). Leave blank for defaults.",
                                   n=len(params)))
            for field in ("fsrs_params_4", "fsrs_params_5", "fsrs_params_6"):
                getattr(c, field)[:] = []
            target = {17: "fsrs_params_4", 19: "fsrs_params_5", 21: "fsrs_params_6"}.get(len(params))
            if target:
                getattr(c, target)[:] = params


def save_form(col, did, form):
    """Save the options form. Returns the preset now used by the deck."""
    data = load(col, did)
    pid = int(form.get("preset_id") or data.current_deck.config_id)
    conf = find_conf(data, pid) or find_conf(data, data.current_deck.config_id)
    apply_form(conf.config, form)
    full = "full" in form
    limits = deck_config_pb2.DeckConfigsForUpdate.CurrentDeck.Limits()
    limits.CopyFrom(data.current_deck.limits)
    if full:
        for key in ("new", "review"):
            v = str(form.get(f"limit_{key}", "")).strip()
            if v:
                setattr(limits, key, max(0, int(float(v))))
            else:
                limits.ClearField(key)
    mode = (Mode.UPDATE_DECK_CONFIGS_MODE_APPLY_TO_CHILDREN if "apply_children" in form
            else Mode.UPDATE_DECK_CONFIGS_MODE_NORMAL)
    save(col, did, data, [conf], mode=mode, limits=limits,
         fsrs=("fsrs" in form) if "has_fsrs" in form else None,
         reschedule="fsrs_reschedule" in form,
         ignore_limit=("new_cards_ignore_review_limit" in form) if "has_new_cards_ignore_review_limit" in form else None,
         parent_limits=("apply_all_parent_limits" in form) if "has_apply_all_parent_limits" in form else None)
    return conf


async def form_dict(request):
    return {k: v for k, v in (await request.form()).items() if isinstance(v, str)}


@router.post("/deck/{did}/options")
async def options_save(request: Request, did: int):
    user = core.current_user(request)
    if not user: return core.need_login(request)
    form = await form_dict(request)
    with core.collection(user) as col:
        if not col.decks.get(did, default=False): return RedirectResponse("/", 303)
        try:
            save_form(col, did, form)
        except Exception as e:
            core.set_flash(request, "!" + t("Not saved: {error}", error=e))
            return RedirectResponse(options_url(did, int(form.get("preset_id") or 0)), 303)
    core.set_flash(request, t("Options saved. Subdecks now use this preset too.") if "apply_children" in form
                   else t("Options saved."))
    return RedirectResponse(f"/deck/{did}/options" if "full" in form else f"/deck/{did}", 303)


@router.post("/deck/{did}/options/preset")
async def options_preset(request: Request, did: int):
    """Add, clone, rename or delete a preset. Add/clone also switch this deck to the new preset."""
    user = core.current_user(request)
    if not user: return core.need_login(request)
    form = await form_dict(request)
    op, name = form.get("op", ""), form.get("name", "").strip()
    with core.collection(user) as col:
        if not col.decks.get(did, default=False): return RedirectResponse("/", 303)
        data = load(col, did)
        conf = find_conf(data, int(form.get("preset") or 0)) or find_conf(data, data.current_deck.config_id)
        deck_conf = find_conf(data, data.current_deck.config_id)
        if op in ("add", "clone"):
            new = DC()
            if op == "add":
                new.config.CopyFrom(data.defaults.config)
                new.name = unique_name(data, name or t("New preset"))
            else:
                new.config.CopyFrom(conf.config)
                new.name = unique_name(data, name or t("{name} copy", name=conf.name))
            save(col, did, data, [new])
            msg = t("Preset “{name}” created and chosen for this deck. Press Save after changing it.", name=new.name)
        elif op == "rename":
            if not name:
                core.set_flash(request, "!" + t("Type the new name first."))
                return RedirectResponse(options_url(did, conf.id), 303)
            old = conf.name
            conf.name = unique_name(data, name) if name != old else name
            # Keep the deck on its own preset: renamed one first, the deck's preset last.
            save(col, did, data, [conf] if conf.id == deck_conf.id else [conf, deck_conf])
            msg = t("Renamed “{old}” to “{new}”.", old=old, new=conf.name)
        elif op == "delete":
            if conf.id == 1:
                core.set_flash(request, "!" + t("The Default preset can't be deleted."))
                return RedirectResponse(options_url(did), 303)
            keep = deck_conf if deck_conf.id != conf.id else find_conf(data, 1)
            save(col, did, data, [keep], removed=[conf.id])
            msg = t("Preset “{name}” deleted. Decks that used it now use Default.", name=conf.name)
        else:
            return RedirectResponse(options_url(did), 303)
    core.set_flash(request, msg)
    return RedirectResponse(options_url(did), 303)


@router.post("/deck/{did}/options/optimize")
async def options_optimize(request: Request, did: int):
    """Save the form, then run the engine's FSRS optimizer on the preset's reviews (Anki's Optimize button)."""
    user = core.current_user(request)
    if not user: return core.need_login(request)
    form = await form_dict(request)
    with core.collection(user) as col:
        if not col.decks.get(did, default=False): return RedirectResponse("/", 303)
        try:
            conf = save_form(col, did, form)
        except Exception as e:
            core.set_flash(request, "!" + t("Not saved: {error}", error=e))
            return RedirectResponse(options_url(did, int(form.get("preset_id") or 0)), 303)
        data = load(col, did)
        conf = find_conf(data, conf.id)
        c = conf.config
        search = c.param_search or f'preset:"{conf.name}" -is:suspended'
        ignore_ms = 0
        if c.ignore_revlogs_before_date:
            d = datetime.datetime.fromisoformat(c.ignore_revlogs_before_date)
            ignore_ms = int(d.timestamp() * 1000)
        old = current_params(c)
        try:
            resp = col._backend.compute_fsrs_params(
                search=search, current_params=old, ignore_revlogs_before_ms=ignore_ms,
                num_of_relearning_steps=len(c.relearn_steps), health_check=False)
        except Exception as e:
            core.set_flash(request, "!" + t("Optimize failed: {error}", error=e))
            return RedirectResponse(options_url(did, conf.id) + "#fsrs", 303)
        new = list(resp.params)
        if not resp.fsrs_items:
            msg = "!" + t("No reviews to learn from yet, so the parameters were not changed.")
        elif not new or [round(p, 4) for p in new] == [round(p, 4) for p in old]:
            # English has always said "reviews" here, so both forms are the same.
            msg = tn("Optimized using {n} reviews: the current parameters are already the best fit.",
                     "Optimized using {n} reviews: the current parameters are already the best fit.",
                     resp.fsrs_items)
        else:
            for field in ("fsrs_params_4", "fsrs_params_5", "fsrs_params_6"):
                getattr(c, field)[:] = []
            c.fsrs_params_6[:] = new
            deck_conf = find_conf(data, data.current_deck.config_id)
            save(col, did, data, [conf] if conf.id == deck_conf.id else [conf, deck_conf])
            msg = tn("Optimized using {n} reviews. New parameters saved.",
                     "Optimized using {n} reviews. New parameters saved.", resp.fsrs_items)
    core.set_flash(request, msg)
    return RedirectResponse(options_url(did, conf.id) + "#fsrs", 303)
