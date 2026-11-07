"""Check Media: Anki's Tools > Check Media window as a page.

Everything goes through the engine's own calls, the ones Anki's media check uses:
`col.media.check()` (finds missing and unused files, and renames/normalises badly named
files as Anki does), `trash_files` (Delete Unused moves files to the media trash),
`empty_trash`, `restore_trash`, and `col.tags.bulk_add` with Anki's `missing-media`
tag for "Tag Missing", `render_all_latex` for "Render LaTeX". Labels come from the engine's translations.

The page lives at /check-media because /media/<name> serves the media files themselves.
"""
import html, re
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

import app as core
from flashcards import latex

router = APIRouter()
SHOW_MAX = 500   # file names listed per section; the actions still cover every file
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".avif", ".bmp", ".ico", ".tif", ".tiff")


def clean(s):
    """Drop the bidi isolation marks the engine's translations put around numbers."""
    return re.sub(r"[⁨⁩]", "", s)


def media_url(name):
    return "/media/" + quote(name)


def file_list(names, ticks=False):
    rows = []
    for name in list(names)[:SHOW_MAX]:
        thumb = ""
        if name.lower().endswith(IMAGE_EXTS):
            thumb = (f'<img src="{html.escape(media_url(name))}" alt="" loading="lazy" '
                     f'style="width:44px;height:44px;object-fit:cover;border-radius:6px;flex:none">')
        box = (f'<input type="checkbox" name="f" value="{html.escape(name)}" checked '
               f'style="width:22px;height:22px;flex:none" aria-label="{html.escape(name)}">') if ticks else ""
        link = (f'<a href="{html.escape(media_url(name))}" target="_blank" rel="noopener" '
                f'style="color:var(--ink);word-break:break-all">{html.escape(name)}</a>') if ticks else \
            f'<span style="word-break:break-all">{html.escape(name)}</span>'
        rows.append(f'<li class="row" style="min-height:48px;padding:4px 0;border-bottom:1px solid var(--line);'
                    f'flex-wrap:nowrap">{box}{thumb}{link}</li>')
    more = ""
    if len(names) > SHOW_MAX:
        more = f'<p class="muted">… and {len(names) - SHOW_MAX} more.</p>'
    return f'<ul style="list-style:none;padding:0;margin:8px 0">{"".join(rows)}</ul>{more}'


def report_html(col, out, latex_error=None):
    tr = col.tr
    missing, unused, notes = list(out.missing), list(out.unused), list(out.missing_media_notes)
    parts = []
    summary = (f'<div class="row" style="gap:24px"><div><div style="font-size:28px;font-weight:600">{len(missing)}</div>'
               f'<div class="muted">Missing files</div></div>'
               f'<div><div style="font-size:28px;font-weight:600">{len(unused)}</div>'
               f'<div class="muted">Unused files</div></div></div>')
    if not missing and not unused:
        summary += '<p>All media files are in use and none are missing.</p>'
    parts.append(f'<div class="box"><h2 style="margin-top:0">{html.escape(tr.media_check_window_title())}</h2>'
                 f'{summary}<p class="row" style="margin-bottom:0"><a class="btn" href="/check-media">Check again</a>'
                 f'<a class="btn" href="/settings">Back to settings</a></p></div>')
    if missing:
        nids = ",".join(str(n) for n in notes)
        browse = (f'<a class="btn" href="/browse?q={quote("nid:" + nids)}">Show {len(notes)} note(s) in Browse</a>'
                  if notes else "")
        parts.append(f"""<div class="box" id="missing"><h3 style="margin-top:0">Missing files</h3>
<p class="muted">{html.escape(clean(tr.media_check_missing_header()))}</p>{file_list(missing)}
<form method="post" action="/check-media/tag" class="row">
<button>{html.escape(tr.media_check_add_tag())}</button>{browse}</form>
<p class="muted" style="margin-bottom:0">Tag Missing adds the tag <code>{html.escape(tr.media_check_missing_media_tag())}</code>
to the notes, so you can find them later with <code>tag:{html.escape(tr.media_check_missing_media_tag())}</code>.</p></div>""")
    if unused:
        confirm = clean(tr.media_check_delete_unused_confirm())
        parts.append(f"""<div class="box" id="unused"><h3 style="margin-top:0">Unused files</h3>
<p class="muted">{html.escape(clean(tr.media_check_unused_header()))}</p>
<form method="post" action="/check-media/delete" onsubmit="return confirm({html.escape(core.js(confirm))})">
{file_list(unused, ticks=True)}
<div class="row"><button class="danger" name="all" value="1">{html.escape(tr.media_check_delete_unused())} (all {len(unused)})</button>
<button class="danger" name="all" value="0">Delete ticked</button></div></form>
<p class="muted" style="margin-bottom:0">Deleted files go to the media trash. "Restore Deleted" brings them back
until the trash is emptied.</p></div>""")
    if out.have_trash:
        parts.append(f"""<div class="box" id="trash"><h3 style="margin-top:0">Trash</h3>
<p class="muted">Files deleted from the media folder are kept in the trash until it is emptied.</p>
<div class="row"><form method="post" action="/check-media/restore-trash"><button>{html.escape(tr.media_check_restore_trash())}</button></form>
<form method="post" action="/check-media/empty-trash" onsubmit="return confirm('Permanently delete the files in the media trash?')">
<button class="danger">{html.escape(tr.media_check_empty_trash())}</button></form></div></div>""")
    parts.append(latex_box(col, latex_error))
    parts.append(f'<details class="box"><summary style="cursor:pointer;min-height:28px">Full report</summary>'
                 f'<pre style="white-space:pre-wrap;word-break:break-all;font-size:14px">'
                 f'{html.escape(clean(out.report))}</pre></details>')
    return "".join(parts)


def latex_box(col, error=None):
    """Anki's "Render LaTeX" button: make every LaTeX image the notes need that isn't in the
    media folder yet (engine's `col.media.render_all_latex`, using flashcards/latex.py)."""
    tr = col.tr
    status = ""
    if not latex.installed():
        status = (f'<p class="flash err">{html.escape(latex.NOT_INSTALLED)} (latex and dvipng), so cards show '
                  f'LaTeX as source. The Docker image includes it.</p>')
    elif not latex.enabled(col):
        status = (f'<p class="flash err">{html.escape(clean(tr.preferences_latex_generation_disabled()))} '
                  f'<a href="/preferences">Preferences</a></p>')
    err = ""
    if error:
        nid, msg = error
        err = (f'<div class="flash err" id="latex-error"><p style="margin-top:0">This note’s LaTeX could not be '
               f'rendered. <a href="/edit/{nid}?back=/check-media">Edit the note</a> · '
               f'<a href="/browse?q={quote(f"nid:{nid}")}">Show in Browse</a></p>{msg}</div>')
    return f"""<div class="box" id="latex"><h3 style="margin-top:0">LaTeX</h3>
<p class="muted">Cards can use <code>[latex]…[/latex]</code>, <code>[$]…[/$]</code> and <code>[$$]…[/$$]</code>.
Pictures for them are made when a card is shown; this makes any that are missing for every note.</p>
{status}{err}<form method="post" action="/check-media/latex"><button>{html.escape(tr.media_check_render_latex())}</button></form></div>"""


@router.post("/check-media/latex")
def render_latex(request: Request):
    user = core.current_user(request)
    if not user: return core.need_login(request)
    with core.collection(user) as col:
        if not latex.enabled(col):
            core.set_flash(request, "!" + clean(col.tr.preferences_latex_generation_disabled()))
            return RedirectResponse("/check-media#latex", 303)
        if not latex.installed():
            core.set_flash(request, "!" + latex.NOT_INSTALLED + ".")
            return RedirectResponse("/check-media#latex", 303)
        res = col.media.render_all_latex()
        if res is None:
            core.set_flash(request, clean(col.tr.media_check_all_latex_rendered()))
            return RedirectResponse("/check-media", 303)
    return RedirectResponse(f"/check-media?latex_error={res[0]}#latex", 303)


@router.get("/check-media", response_class=HTMLResponse)
def check_media(request: Request, latex_error: int = 0):
    user = core.current_user(request)
    if not user: return core.need_login(request)
    with core.collection(user) as col:
        out = col.media.check()
        err = None
        if latex_error:   # that note's LaTeX error, worked out again (too long to keep in the session)
            msg = note_latex_errors(col, latex_error)
            err = (latex_error, msg) if msg else None
        body = report_html(col, out, err)
    return core.page("Check Media", body, user, "settings", core.take_flash(request))


def note_latex_errors(col, nid):
    try:
        note = col.get_note(nid)
    except Exception:
        return ""
    _html, errors = latex.engine_latex.render_latex_returning_errors(
        "\x1f".join(note.fields), note.note_type(), col, expand_clozes=True)
    return "<br>".join(dict.fromkeys(errors))


@router.post("/check-media/delete")
async def delete_unused(request: Request):
    user = core.current_user(request)
    if not user: return core.need_login(request)
    form = await request.form()
    with core.collection(user) as col:
        unused = set(col.media.check().unused)   # only ever trash files the engine says are unused
        if form.get("all") == "1":
            names = sorted(unused)
        else:
            names = [n for n in form.getlist("f") if n in unused]
        if names:
            col.media.trash_files(names)
            core.set_flash(request, clean(col.tr.media_check_delete_unused_complete(count=len(names))))
        else:
            core.set_flash(request, "!No unused files were selected.")
    return RedirectResponse("/check-media", 303)


@router.post("/check-media/tag")
def tag_missing(request: Request):
    user = core.current_user(request)
    if not user: return core.need_login(request)
    with core.collection(user) as col:
        nids = list(col.media.check().missing_media_notes)
        if nids:
            changed = col.tags.bulk_add(nids, col.tr.media_check_missing_media_tag()).count
            core.set_flash(request, clean(col.tr.browsing_notes_updated(count=changed)))
        else:
            core.set_flash(request, "No notes refer to missing files.")
    return RedirectResponse("/check-media", 303)


@router.post("/check-media/empty-trash")
def empty_trash(request: Request):
    user = core.current_user(request)
    if not user: return core.need_login(request)
    with core.collection(user) as col:
        col.media.empty_trash()
        core.set_flash(request, clean(col.tr.media_check_trash_emptied()))
    return RedirectResponse("/check-media", 303)


@router.post("/check-media/restore-trash")
def restore_trash(request: Request):
    user = core.current_user(request)
    if not user: return core.need_login(request)
    with core.collection(user) as col:
        col.media.restore_trash()
        core.set_flash(request, clean(col.tr.media_check_trash_restored()))
    return RedirectResponse("/check-media", 303)
