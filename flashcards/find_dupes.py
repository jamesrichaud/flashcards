"""Find Duplicates, like Anki's Browse > Notes > Find Duplicates.

Pick a field (every field name of every note type, as in desktop Anki) and an optional search;
the engine's `col.find_dupes` groups notes whose field is the same once HTML and media are
stripped (blank fields never count). Each group links to Browse (an engine-built `nid:` search)
and to each note's editor. "Tag Duplicates" adds Anki's own `duplicate` tag (from the engine's
translations) to every note in every group, as one undoable step.
"""
import html, re
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

import anki.lang
from anki.collection import SearchNode

import app as core

router = APIRouter()
LAST_KEY = "flashcardsFindDupes"   # our own key: {"field": .., "q": ..} last used, per collection
MAX_GROUPS = 500                   # groups listed on the page (Tag Duplicates still tags them all)


def clean(s):
    return re.sub(r"[⁨⁩]", "", str(s))


def field_names(col):
    """Every field name across note types, case-insensitively unique, sorted (desktop Anki's list)."""
    seen = {}
    for m in col.models.all():
        for f in m["flds"]:
            seen.setdefault(f["name"].lower(), f["name"])
    return sorted(seen.values(), key=str.lower)


def nid_search(col, nids):
    return col.build_search_string(SearchNode(nids=SearchNode.IdList(ids=list(nids))))


def dupes_url(field="", q=""):
    return f"/browse/dupes?field={quote(field, safe='')}&q={quote(q, safe='')}"


def find(col, field, q):
    """[(value, [nid, ...]), ...] from the engine; raises on a search the engine can't read."""
    if anki.lang.current_i18n is None:
        # find_dupes strips HTML through anki.lang's global backend, which desktop Anki sets up at start.
        anki.lang.set_lang("en")
    return col.find_dupes(field, q)


def results_html(col, field, q, groups):
    esc = html.escape
    tr = col.tr
    if not groups:
        return f'<div class="box"><p style="margin:0">No duplicates found in <b>{esc(field)}</b>.</p></div>'
    total = sum(len(n) for _, n in groups)
    back = quote(dupes_url(field, q), safe="/")
    all_url = core.browse_url(nid_search(col, [n for _, ns in groups for n in ns]), mode="notes")
    head = (f'<div class="box row" style="justify-content:space-between"><span><b>{len(groups)}</b> '
            f'group{"" if len(groups) == 1 else "s"} · {total} notes</span><div class="row">'
            f'<a class="btn" href="{esc(all_url)}">Browse all</a>'
            f'<form method="post" action="/browse/dupes/tag" class="row" style="margin:0">'
            f'<input type="hidden" name="field" value="{esc(field)}"><input type="hidden" name="q" value="{esc(q)}">'
            f'<button class="primary">{esc(tr.browsing_tag_duplicates())}</button></form></div>'
            f'<p class="muted" style="flex-basis:100%;margin:0;font-size:14px">{esc(tr.browsing_tag_duplicates())} adds the tag '
            f'<code>{esc(tr.browsing_duplicate())}</code> to all {total} notes, so you can find them later with '
            f'<code>tag:{esc(tr.browsing_duplicate())}</code>.</p></div>')
    deck_name = {d.id: d.name for d in col.decks.all_names_and_ids()}
    rows = []
    for val, nids in groups[:MAX_GROUPS]:
        notes = []
        for nid in nids:
            try:
                n = col.get_note(nid)
                cids = n.card_ids()
                did = col.get_card(cids[0]).did if cids else 0
                label = f"{deck_name.get(did, '?')} · {n.note_type()['name']}"
            except Exception:  # noqa: BLE001  (removed meanwhile)
                continue
            notes.append(f'<a class="dchip" href="/edit/{nid}?back={back}">{esc(label)}</a>')
        url = core.browse_url(nid_search(col, nids), mode="notes")
        shown = val if len(val) <= 200 else val[:200] + "…"
        rows.append(f'<div class="box dgroup"><div class="row" style="justify-content:space-between">'
                    f'<b class="dval">{esc(shown)}</b><a class="btn" href="{esc(url)}">{len(nids)} notes in Browse</a></div>'
                    f'<div class="dnotes">{"".join(notes)}</div></div>')
    more = (f'<p class="muted">Showing the first {MAX_GROUPS} groups of {len(groups)}. '
            f'Use “Browse all” or narrow the search to see the rest.</p>') if len(groups) > MAX_GROUPS else ""
    return head + "".join(rows) + more


CSS = """.dval{overflow-wrap:anywhere;flex:1;min-width:0}.dnotes{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px}
.dchip{display:inline-flex;align-items:center;min-height:44px;padding:0 14px;border:1px solid var(--line);border-radius:22px;
color:var(--ink);text-decoration:none;font-size:15px;background:var(--bg);max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.dchip:hover{border-color:var(--accent)}.dgroup{padding-top:12px;padding-bottom:12px}
.dform select,.dform input{min-height:44px}"""


@router.get("/browse/dupes", response_class=HTMLResponse)
def dupes_page(request: Request, field: str = "", q: str = "", search: str = ""):
    """field/q run the search; `search` only pre-fills the box (from Browse's current search)."""
    user = core.current_user(request)
    if not user: return core.need_login(request)
    esc = html.escape
    with core.collection(user) as col:
        names = field_names(col)
        last = col.get_config(LAST_KEY, {}) or {}
        out = ""
        if field:
            if field.lower() not in {n.lower() for n in names}:
                out = f'<div class="flash err">No note type has a field called “{esc(field)}”.</div>'
            else:
                try:
                    groups = find(col, field, q)
                    out = results_html(col, field, q, groups)
                    if last != {"field": field, "q": q}:
                        col.set_config(LAST_KEY, {"field": field, "q": q})
                except Exception as e:  # noqa: BLE001
                    out = f'<div class="flash err">{esc(clean(e) or "That search did not work.")}</div>'
            box_q = q
        else:
            box_q = search if search else str(last.get("q", ""))
        chosen = field or str(last.get("field", "")) or (names[0] if names else "")
        tr = col.tr
        title, search_in, optional = tr.browsing_find_duplicates(), tr.browsing_search_in(), tr.browsing_optional_filter()
    opts = "".join(f'<option value="{esc(n)}"{" selected" if n.lower() == chosen.lower() else ""}>{esc(n)}</option>'
                   for n in names)
    body = f"""<style>{CSS}</style><div class="box"><h2 style="margin-top:0">{esc(title)}</h2>
<form class="dform" method="get" action="/browse/dupes"><div class="row">
<label class="row" style="margin:0;gap:8px">{esc(search_in.rstrip(':'))}: <select name="field" aria-label="Field">{opts}</select></label>
<input name="q" value="{esc(box_q)}" style="flex:1;min-width:200px" placeholder="{esc(optional.rstrip(':'))} (e.g. deck:French)"
aria-label="Optional filter"><button class="primary">Search</button></div></form>
<p class="muted" style="margin-bottom:0;font-size:14px">Notes count as duplicates when the field is the same once
formatting and pictures are ignored. Blank fields don't count.</p>
<p style="margin-bottom:0"><a href="{esc(core.browse_url(box_q))}">← Back to Browse</a></p></div>{out}"""
    return core.page(title, body, user, "browse", core.take_flash(request))


@router.post("/browse/dupes/tag")
async def tag_dupes(request: Request):
    user = core.current_user(request)
    if not user: return core.need_login(request)
    form = await request.form()
    field, q = str(form.get("field", "")), str(form.get("q", ""))
    with core.collection(user) as col:
        try:
            if not field: raise ValueError("Choose a field first.")
            nids = [n for _, ns in find(col, field, q) for n in ns]
            if nids:
                changed = col.tags.bulk_add(nids, col.tr.browsing_duplicate()).count
                msg = clean(col.tr.browsing_notes_updated(count=changed))
            else:
                msg = "No duplicates to tag."
        except Exception as e:  # noqa: BLE001
            msg = "!" + (clean(e) or "That did not work.")
    core.set_flash(request, msg)
    return RedirectResponse(dupes_url(field, q), 303)
