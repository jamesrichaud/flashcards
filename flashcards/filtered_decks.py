"""Filtered decks: Anki's "Create Filtered Deck" dialog as a page, plus Rebuild and Empty.

Everything goes through the engine's own calls, the ones Anki's dialog uses:
`get_or_create_filtered_deck` (defaults for a new deck, or the saved settings of an
existing one), `add_or_update_filtered_deck` (saves and rebuilds), `rebuild_filtered_deck`
and `empty_filtered_deck`. Labels come from the engine's translations.
"""
import html
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from anki import decks_pb2
from anki.consts import DYN_MAX_SIZE
from anki.errors import FilteredDeckError, SearchError, DeckRenameError

import app as core

router = APIRouter()
Term = decks_pb2.Deck.Filtered.SearchTerm
# preview delays, in seconds, as stored by the engine
DELAYS = [("preview_again_secs", "Again"), ("preview_hard_secs", "Hard"), ("preview_good_secs", "Good")]
MAX_DELAY = 99999

JS = """<script>
function fdShow(){var two=document.getElementById('second').checked,res=document.getElementById('resched').checked;
document.getElementById('f2').style.display=two?'':'none';
document.getElementById('delays').style.display=res?'none':'';}
['second','resched'].forEach(function(i){document.getElementById(i).addEventListener('change',fdShow)});fdShow();
</script>"""


def defaults(col):
    """A new filtered deck as the engine would make it (based on the current deck)."""
    return col.sched.get_or_create_filtered_deck(0)


def term_html(col, n, term, label):
    labels = list(col.sched.filtered_deck_order_labels())
    opts = "".join(f'<option value="{i}"{" selected" if i == term.order else ""}>{html.escape(t)}</option>'
                   for i, t in enumerate(labels))
    tr = col.tr
    return f"""<h3 style="margin:14px 0 0">{html.escape(label)}</h3>
<label for="search{n}">{html.escape(tr.actions_search())}</label>
<input id="search{n}" name="search{n}" value="{html.escape(term.search)}" autocapitalize="none" autocorrect="off" spellcheck="false">
<div class="row" style="align-items:flex-end">
<div><label for="limit{n}">{html.escape(tr.decks_limit_to())}</label>
<input type="number" id="limit{n}" name="limit{n}" value="{term.limit}" min="1" max="{DYN_MAX_SIZE}" style="width:140px"></div>
<div style="flex:1;min-width:220px"><label for="order{n}">{html.escape(tr.decks_cards_selected_by())}</label>
<select id="order{n}" name="order{n}">{opts}</select></div></div>"""


def form_html(col, fd, excluded=""):
    """fd: a FilteredDeckForUpdate. `excluded` is a search to offer under "Show any excluded cards"."""
    tr = col.tr
    cfg = fd.config
    first = cfg.search_terms[0] if cfg.search_terms else defaults(col).config.search_terms[0]
    second_on = len(cfg.search_terms) > 1
    second = cfg.search_terms[1] if second_on else defaults(col).config.search_terms[1]
    delays = "".join(
        f'<div><label for="{k}">{html.escape(tr.decks_delay_for_button(button=b))} (seconds)</label>'
        f'<input type="number" id="{k}" name="{k}" value="{getattr(cfg, k)}" min="0" max="{MAX_DELAY}" style="width:140px"></div>'
        for k, b in DELAYS)
    show = ""
    if excluded:
        show = (f'<p><a class="btn" href="/browse?q={quote(excluded)}&amp;mode=cards">'
                f'{html.escape(tr.decks_unmovable_cards())}</a></p>')
    title = tr.actions_build_filtered_deck() if not fd.id else tr.actions_options_for(val=fd.name)
    button = tr.decks_build() if not fd.id else tr.actions_rebuild()
    cancel = f"/deck/{fd.id}" if fd.id else "/"
    chk = 'style="width:22px;height:22px"'
    return f"""<div class="box"><h2 style="margin-top:0">{html.escape(title)}</h2>{show}
<form method="post" action="/filtered/save">
<input type="hidden" name="did" value="{fd.id}">
<label for="name">{html.escape(tr.actions_name().rstrip(":"))}</label>
<input id="name" name="name" value="{html.escape(fd.name)}" required>
{term_html(col, 1, first, tr.decks_filter().rstrip(":"))}
<label class="row" style="margin:16px 0 0;color:var(--ink);font-size:17px;min-height:44px">
<input type="checkbox" id="second" name="second" value="1" {chk}{" checked" if second_on else ""}>
{html.escape(tr.decks_enable_second_filter())}</label>
<div id="f2">{term_html(col, 2, second, tr.decks_filter_2())}</div>
<h3 style="margin:18px 0 0">{html.escape(tr.actions_options())}</h3>
<label class="row" style="margin:8px 0 0;color:var(--ink);font-size:17px;min-height:44px">
<input type="checkbox" id="resched" name="resched" value="1" {chk}{" checked" if cfg.reschedule else ""}>
{html.escape(tr.decks_reschedule_cards_based_on_my_answers())}</label>
<div id="delays"><div class="row" style="align-items:flex-end">{delays}</div>
<p class="muted" style="font-size:14px;margin:6px 0 0">{html.escape(tr.decks_zero_minutes_hint().replace("minutes", "seconds"))}</p></div>
<label class="row" style="margin:8px 0 0;color:var(--ink);font-size:17px;min-height:44px">
<input type="checkbox" name="allow_empty" value="1" {chk}{" checked" if fd.allow_empty else ""}>
{html.escape(tr.decks_create_even_if_empty())}</label>
<div class="row" style="margin-top:16px"><button class="primary">{html.escape(button)}</button>
<a class="btn" href="{cancel}">Cancel</a></div></form></div>{JS}"""


def num(form, name, lo, hi, default):
    raw = str(form.get(name, default)).strip()
    try:
        v = int(raw)
    except ValueError:
        raise ValueError("Please enter a whole number.")
    return max(lo, min(hi, v))


def order(form, name):
    try:
        v = int(form.get(name, Term.Order.RANDOM))
    except ValueError:
        raise ValueError("Unknown order.")
    if v not in Term.Order.values():
        raise ValueError("Unknown order.")
    return v


def form_did(form):
    try:
        return int(form.get("did", 0) or 0)
    except ValueError:
        return 0


def from_form(col, form):
    """Build a FilteredDeckForUpdate from the submitted form, starting from the saved deck."""
    did = form_did(form)
    if did:
        deck = col.decks.get(did, default=False)
        if not deck or not deck.get("dyn"):
            raise LookupError
    fd = col.sched.get_or_create_filtered_deck(did)
    fd.name = str(form.get("name", "")).strip() or fd.name
    cfg = fd.config
    terms = [Term(search=str(form.get("search1", "")).strip(), limit=num(form, "limit1", 1, DYN_MAX_SIZE, 100),
                  order=order(form, "order1"))]
    if form.get("second"):
        terms.append(Term(search=str(form.get("search2", "")).strip(),
                          limit=num(form, "limit2", 1, DYN_MAX_SIZE, 20), order=order(form, "order2")))
    del cfg.search_terms[:]
    cfg.search_terms.extend(terms)
    cfg.reschedule = bool(form.get("resched"))
    for k, _ in DELAYS:
        setattr(cfg, k, num(form, k, 0, MAX_DELAY, getattr(cfg, k)))
    fd.allow_empty = bool(form.get("allow_empty"))
    return fd


@router.get("/filtered/new", response_class=HTMLResponse)
def filtered_new(request: Request, search: str = "", did: int = 0):
    user = core.current_user(request)
    if not user: return core.need_login(request)
    with core.collection(user) as col:
        # Like Anki, the defaults are based on the current deck; a deck id or a Browse search
        # can be passed in.
        if did and col.decks.get(did, default=False) and not col.decks.is_filtered(did):
            core.select_deck(col, did)
        fd = defaults(col)
        if search.strip():
            fd.config.search_terms[0].search = search.strip()
        body = form_html(col, fd)
    return core.page("Create filtered deck", body, user, "decks", core.take_flash(request))


@router.get("/deck/{did}/filtered", response_class=HTMLResponse)
def filtered_edit(request: Request, did: int):
    user = core.current_user(request)
    if not user: return core.need_login(request)
    with core.collection(user) as col:
        deck = col.decks.get(did, default=False)
        if not deck: return RedirectResponse("/", 303)
        if not deck.get("dyn"):
            return RedirectResponse(f"/deck/{did}/options", 303)
        body = form_html(col, col.sched.get_or_create_filtered_deck(did))
    return core.page("Filtered deck options", body, user, "decks", core.take_flash(request))


@router.post("/filtered/save")
async def filtered_save(request: Request):
    user = core.current_user(request)
    if not user: return core.need_login(request)
    form = await request.form()
    with core.collection(user) as col:
        try:
            fd = from_form(col, form)
        except LookupError:
            return RedirectResponse("/", 303)
        except ValueError as e:
            fd = col.sched.get_or_create_filtered_deck(form_did(form))
            return core.page("Filtered deck", form_html(col, fd), user, "decks", "!" + str(e))
        if not fd.config.search_terms[0].search:
            return core.page("Filtered deck", form_html(col, fd), user, "decks",
                             "!" + col.tr.decks_please_select_something())
        try:
            new_id = col.sched.add_or_update_filtered_deck(fd).id
        except FilteredDeckError as e:
            # Anki keeps its dialog open and offers to show the cards that were left out.
            return core.page("Filtered deck", form_html(col, fd, excluded=fd.config.search_terms[0].search),
                             user, "decks", "!" + str(e))
        except (SearchError, DeckRenameError) as e:
            return core.page("Filtered deck", form_html(col, fd), user, "decks", "!" + str(e))
        core.select_deck(col, new_id)
        n = len(col.find_cards(f"did:{new_id}"))
    core.set_flash(request, f"Deck built: {n} card{'s' if n != 1 else ''}.")
    return RedirectResponse(f"/deck/{new_id}", 303)


@router.post("/deck/{did}/rebuild")
def deck_rebuild(request: Request, did: int):
    user = core.current_user(request)
    if not user: return core.need_login(request)
    with core.collection(user) as col:
        deck = col.decks.get(did, default=False)
        if deck and deck.get("dyn"):
            try:
                n = col.sched.rebuild_filtered_deck(did).count
                core.set_flash(request, f"Deck rebuilt: {n} cards.")
            except Exception as e:
                core.set_flash(request, "!" + str(e))
    return RedirectResponse(f"/deck/{did}", 303)


@router.post("/deck/{did}/empty")
def deck_empty(request: Request, did: int):
    user = core.current_user(request)
    if not user: return core.need_login(request)
    with core.collection(user) as col:
        deck = col.decks.get(did, default=False)
        if deck and deck.get("dyn"):
            col.sched.empty_filtered_deck(did)
            core.set_flash(request, "Deck emptied. Its cards are back in their home decks.")
    return RedirectResponse(f"/deck/{did}", 303)
