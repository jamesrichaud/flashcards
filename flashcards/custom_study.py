"""Custom study: Anki's "Custom Study" dialog as a page.

Everything goes through the engine's own calls (`custom_study_defaults` and
`custom_study`), the same ones Anki's dialog uses. Raising today's limits stays
on the deck; the other choices build (or rebuild) the "Custom Study Session"
filtered deck, and the engine selects it, so we show its overview next.
"""
import html

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from anki import scheduler_pb2
from anki.consts import DYN_MAX_SIZE
from anki.errors import CustomStudyError, NotFoundError

import app as core
from flashcards.i18n import t, N_

router = APIRouter()
# The engine leaves an empty "Default" deck out of its deck tree (desktop Anki hides it), and its
# custom study calls then fail with NotFoundError; Anki gives no way to get there, so say why.
# Shown as an error flash: "!" + t(NOT_IN_TREE).
NOT_IN_TREE = N_("Custom study isn't available here: this deck is empty and hidden from the deck list.")
Req = scheduler_pb2.CustomStudyRequest
Cram = Req.Cram

# The radio choices, in the order of Anki's dialog.
MODES = ["new", "rev", "forgot", "ahead", "preview", "cram"]
# Cram card kinds, in the order of Anki's list.
KINDS = [(Cram.CRAM_KIND_NEW, "custom_study_new_cards_only"),
         (Cram.CRAM_KIND_DUE, "custom_study_due_cards_only"),
         (Cram.CRAM_KIND_REVIEW, "custom_study_all_review_cards_in_random_order"),
         (Cram.CRAM_KIND_ALL, "custom_study_all_cards_in_random_order_dont")]
# form field -> (min, max) as in Anki's dialog
LIMITS = {"new_delta": (-DYN_MAX_SIZE, DYN_MAX_SIZE), "rev_delta": (-DYN_MAX_SIZE, DYN_MAX_SIZE),
          "forgot_days": (1, 30), "ahead_days": (1, DYN_MAX_SIZE), "preview_days": (1, DYN_MAX_SIZE),
          "cram_limit": (1, DYN_MAX_SIZE)}

JS = """<script>
function csShow(){var m=(document.querySelector('input[name=mode]:checked')||{}).value;
document.querySelectorAll('.cs-part').forEach(function(e){e.style.display=e.dataset.mode===m?'':'none'});}
document.querySelectorAll('input[name=mode]').forEach(function(r){r.addEventListener('change',csShow)});csShow();
</script>"""


def number(name, value, before, after):
    lo, hi = LIMITS[name]
    return (f'<label for="{name}">{html.escape(before)}</label><div class="row">'
            f'<input type="number" id="{name}" name="{name}" value="{value}" min="{lo}" max="{hi}" '
            f'style="width:140px">'
            f'<span>{html.escape(after)}</span></div>')


def custom_html(col, did, mode="new"):
    tr = col.tr
    d = col.sched.custom_study_defaults(did)
    count = col.decks.card_count(did, True)

    def avail(n, children, fn):
        s = getattr(tr, fn)(count_string=str(n))
        if children:
            s += " " + tr.custom_study_available_child_count(count=children)
        return s

    titles = {"new": tr.custom_study_increase_todays_new_card_limit(),
              "rev": tr.custom_study_increase_todays_review_card_limit(),
              "forgot": tr.custom_study_review_forgotten_cards(),
              "ahead": tr.custom_study_review_ahead(),
              "preview": tr.custom_study_preview_new_cards(),
              "cram": tr.custom_study_study_by_card_state_or_tag()}
    radios = ""
    for m in MODES:
        off = m not in ("new", "rev") and not count
        radios += (f'<label class="row" style="margin:6px 0;color:var(--ink);font-size:17px;min-height:44px">'
                   f'<input type="radio" name="mode" value="{m}"'
                   f'{" checked" if m == mode and not off else ""}{" disabled" if off else ""}'
                   f' style="width:22px;height:22px"> '
                   f'{html.escape(titles[m])}</label>')

    parts = {
        "new": f'<p class="muted">{html.escape(avail(d.available_new, d.available_new_in_children, "custom_study_available_new_cards_2"))}</p>'
               + number("new_delta", d.extend_new, tr.custom_study_increase_todays_new_card_limit_by(),
                        tr.custom_study_cards(count=d.extend_new)),
        "rev": f'<p class="muted">{html.escape(avail(d.available_review, d.available_review_in_children, "custom_study_available_review_cards_2"))}</p>'
               + number("rev_delta", d.extend_review, tr.custom_study_increase_todays_review_limit_by(),
                        tr.custom_study_cards(count=d.extend_review)),
        "forgot": number("forgot_days", 1, tr.custom_study_review_cards_forgotten_in_last(), tr.custom_study_days(count=1)),
        "ahead": number("ahead_days", 1, tr.custom_study_review_ahead_by(), tr.custom_study_days(count=1)),
        "preview": number("preview_days", 1, tr.custom_study_preview_new_cards_added_in_the(), tr.custom_study_days(count=1)),
    }
    kinds = "".join(f'<option value="{k}"{" selected" if k == Cram.CRAM_KIND_NEW else ""}>'
                    f'{html.escape(getattr(tr, fn)())}</option>' for k, fn in KINDS)
    tags_in = tags_out = ""
    for tag in d.tags:
        n = html.escape(tag.name)
        tags_in += (f'<label class="row" style="margin:4px 0;color:var(--ink)"><input type="checkbox" name="include" '
                    f'value="{n}" style="width:22px;height:22px"{" checked" if tag.include else ""}> {n}</label>')
        tags_out += (f'<label class="row" style="margin:4px 0;color:var(--ink)"><input type="checkbox" name="exclude" '
                     f'value="{n}" style="width:22px;height:22px"{" checked" if tag.exclude else ""}> {n}</label>')
    tag_box = ""
    if d.tags:
        tag_box = f"""<div class="row" style="align-items:flex-start;gap:24px;margin-top:8px">
<div style="flex:1;min-width:220px"><label>{html.escape(tr.custom_study_require_one_or_more_of_these())}</label>
<p class="muted" style="font-size:14px;margin:0 0 6px">{t("Tick none to allow any tag.")}</p>{tags_in}</div>
<div style="flex:1;min-width:220px"><label>{html.escape(tr.custom_study_select_tags_to_exclude())}</label>{tags_out}</div></div>"""
    parts["cram"] = (number("cram_limit", 100, tr.custom_study_select(), tr.custom_study_cards_from_the_deck(count=100))
                     + f'<label for="cram_kind">{t("Cards")}</label><select id="cram_kind" name="cram_kind">{kinds}</select>'
                     + tag_box)
    sections = "".join(f'<div class="cs-part" data-mode="{m}">{parts[m]}</div>' for m in MODES)
    deck = col.decks.name(did)
    empty = ("" if count else
             '<p class="muted">' + t("This deck has no cards, so only the limit options are available.") + "</p>")
    return f"""<div class="box"><h2 style="margin-top:0">{html.escape(tr.actions_custom_study())}</h2>
<p class="muted" style="margin-top:-6px">{html.escape(deck)}</p>{empty}
<form method="post" action="/deck/{did}/custom" id="csform">{radios}
<div class="box" style="margin:14px 0 0;background:var(--bg)">{sections}</div>
<div class="row" style="margin-top:16px"><button class="primary">{html.escape(tr.custom_study_ok())}</button>
<a class="btn" href="/deck/{did}">{t("Cancel")}</a></div></form></div>{JS}"""


def form_int(form, name, default):
    lo, hi = LIMITS[name]
    try:
        v = int(str(form.get(name, default)).strip())
    except ValueError:
        raise ValueError(t("Please enter a whole number."))
    return max(lo, min(hi, v))


def build_request(did, form):
    mode = form.get("mode", "new")
    req = Req(deck_id=did)
    if mode == "new":
        req.new_limit_delta = form_int(form, "new_delta", 0)
    elif mode == "rev":
        req.review_limit_delta = form_int(form, "rev_delta", 0)
    elif mode == "forgot":
        req.forgot_days = form_int(form, "forgot_days", 1)
    elif mode == "ahead":
        req.review_ahead_days = form_int(form, "ahead_days", 1)
    elif mode == "preview":
        req.preview_days = form_int(form, "preview_days", 1)
    elif mode == "cram":
        kind = int(form.get("cram_kind", Cram.CRAM_KIND_NEW))
        if kind not in dict(KINDS):
            raise ValueError(t("Unknown card choice."))
        req.cram.kind = kind
        req.cram.card_limit = form_int(form, "cram_limit", 100)
        req.cram.tags_to_include.extend(form.getlist("include"))
        req.cram.tags_to_exclude.extend(form.getlist("exclude"))
    else:
        raise ValueError(t("Choose an option."))
    return mode, req


@router.get("/deck/{did}/custom", response_class=HTMLResponse)
def custom_page(request: Request, did: int, mode: str = "new"):
    user = core.current_user(request)
    if not user: return core.need_login(request)
    with core.collection(user) as col:
        deck = col.decks.get(did, default=False)
        if not deck: return RedirectResponse("/", 303)
        if deck.get("dyn"):
            core.set_flash(request, "!" + t("Custom study is only available for normal decks."))
            return RedirectResponse(f"/deck/{did}", 303)
        try:
            body = custom_html(col, did, mode if mode in MODES else "new")
        except NotFoundError:
            core.set_flash(request, "!" + t(NOT_IN_TREE))
            return RedirectResponse(f"/deck/{did}", 303)
    return core.page(t("Custom study"), body, user, "decks", core.take_flash(request))


@router.post("/deck/{did}/custom")
async def custom_save(request: Request, did: int):
    user = core.current_user(request)
    if not user: return core.need_login(request)
    form = await request.form()
    with core.collection(user) as col:
        deck = col.decks.get(did, default=False)
        if not deck: return RedirectResponse("/", 303)
        if deck.get("dyn"):
            core.set_flash(request, "!" + t("Custom study is only available for normal decks."))
            return RedirectResponse(f"/deck/{did}", 303)
        try:
            mode, req = build_request(did, form)
            col.sched.custom_study(req)
        except (ValueError, CustomStudyError) as e:
            # Anki keeps the dialog open on failure so the user can adjust it.
            core.set_flash(request, "!" + str(e))
            back = form.get("mode") if form.get("mode") in MODES else "new"
            return RedirectResponse(f"/deck/{did}/custom?mode={back}", 303)
        except NotFoundError:
            core.set_flash(request, "!" + t(NOT_IN_TREE))
            return RedirectResponse(f"/deck/{did}", 303)
        if mode in ("new", "rev"):
            core.select_deck(col, did)
            core.set_flash(request, t("Today's limit was changed."))
            return RedirectResponse(f"/deck/{did}", 303)
        # The engine made "Custom Study Session" the current deck.
        new_did = col.decks.get_current_id()
    core.set_flash(request, t("Custom study session created."))
    return RedirectResponse(f"/deck/{new_did}", 303)

