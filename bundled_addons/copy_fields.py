"""Copy, append or move one field into another for the notes selected in Browse.

Like the desktop add-on "Advanced Copy Fields". Adds a "Copy fields" panel to Browse's action bar;
it works on the ticked notes (or the whole search when "All" is ticked). Notes whose note type
lacks either field are left alone. The change is saved through the engine in one step, so the
header's Undo takes it back.
"""
import html

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

import app as core
from flashcards import addons
from flashcards.i18n import t, N_

NAME = "Advanced Copy Fields"   # plain literal: flashcards/addons.py reads it without importing
N_("Advanced Copy Fields")      # ...so mark it for translation here
N_('Copy, append or move one field into another for the notes selected in Browse.')
MODES = {"copy": N_("Replace (copy)"), "append": N_("Add to the end"), "prepend": N_("Add to the start"),
         "move": N_("Move (empty the source)")}
SEPARATORS = {"space": (N_("space"), " "), "br": (N_("new line"), "<br>"), "none": (N_("nothing"), ""),
              "comma": (N_("comma"), ", ")}


def browse_tools(col, mode):
    fields = sorted({f["name"] for m in col.models.all() for f in m["flds"]}, key=str.lower)
    opts = "".join(f'<option value="{html.escape(f)}">{html.escape(f)}</option>' for f in fields)
    e = lambda x: html.escape(x, quote=False)
    a = lambda x: html.escape(x)
    modes = "".join(f'<option value="{k}">{e(t(v))}</option>' for k, v in MODES.items())
    seps = "".join(f'<option value="{k}">{e(t(v))}</option>' for k, (v, _) in SEPARATORS.items())
    return f"""<details class="panel acf" style="margin-top:10px"><summary class="btn">{e(t("Copy fields ▾"))}</summary>
<div class="row" style="margin-top:10px"><select name="acf_from" aria-label="{a(t("From field"))}">{opts}</select>
<span aria-hidden="true">→</span><select name="acf_to" aria-label="{a(t("To field"))}">{opts}</select>
<select name="acf_mode" aria-label="{a(t("How"))}">{modes}</select>
<select name="acf_sep" aria-label="{a(t("Separator"))}">{seps}</select>
<button data-addon formaction="/addon/copy_fields/run">{e(t("Copy"))}</button></div>
<p class="muted" style="font-size:14px;margin-bottom:0">{e(t("Works on the selected notes (or every note in the search when “All” is ticked)."))}
{e(t("The separator is used when adding to the end or start of a field that isn't empty."))}</p></details>"""


def copy(col, nids, src, dst, how, sep):
    """Change the notes; returns (changed, skipped)."""
    changed, skipped = [], 0
    for nid in nids:
        note = col.get_note(nid)
        if src not in note or dst not in note:
            skipped += 1
            continue
        value, old = note[src], note[dst]
        if how == "append":
            new = old + sep + value if old and value else old + value
        elif how == "prepend":
            new = value + sep + old if old and value else value + old
        else:
            new = value
        if how == "move":
            note[src] = ""
        if new == old and how != "move":
            continue
        note[dst] = new
        changed.append(note)
    if changed:
        col.update_notes(changed)
    return len(changed), skipped


router = APIRouter()


@router.post("/run")
async def run(request: Request):
    form = await request.form()
    back = core.safe_back(str(form.get("back", "")))
    src, dst = str(form.get("acf_from", "")), str(form.get("acf_to", ""))
    how = str(form.get("acf_mode", "copy"))
    sep = SEPARATORS.get(str(form.get("acf_sep", "space")), SEPARATORS["space"])[1]
    if how not in MODES:
        how = "copy"
    with addons.collection(request) as col:
        try:
            _, nids = core.bulk_targets(col, form)
            if not nids:
                raise ValueError(t("Nothing selected."))
            if not src or not dst or src == dst:
                raise ValueError(t("Choose two different fields."))
            changed, skipped = copy(col, nids, src, dst, how, sep)
            msg = t("Notes changed: {n}.", n=changed)
            if skipped:
                msg += " " + t("Skipped, the field isn't in their note type: {n}.", n=skipped)
        except Exception as e:
            msg = "!" + (str(e) or t("That did not work."))
    core.set_flash(request, msg)
    return RedirectResponse(back, 303)
