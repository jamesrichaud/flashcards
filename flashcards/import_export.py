"""Import and export: Anki's File > Import and File > Export as pages.

Everything goes through the engine's own calls, the ones Anki's import/export screens use:
`import_anki_package` (with ImportAnkiPackageOptions and the engine's saved presets),
`get_csv_metadata` + `import_csv` for text files, the backend's
`import_collection_package` for a .colpkg (replaces the whole collection), and
`export_collection_package`, `export_anki_package`, `export_note_csv` and
`export_card_csv` for exports.
"""
import html, os, re, secrets, shutil, tempfile, time
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Request, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse

from anki import import_export_pb2 as pb
from anki.collection import (Collection, ImportAnkiPackageRequest, ImportAnkiPackageOptions, ImportCsvRequest,
                             ExportAnkiPackageOptions, CsvMetadata, DeckIdLimit, NoteIdsLimit, CardIdsLimit)
import app as core
from flashcards import maintenance

router = APIRouter()
Update = pb.ImportAnkiPackageUpdateCondition
UPDATE_CHOICES = [(Update.IMPORT_ANKI_PACKAGE_UPDATE_CONDITION_IF_NEWER, "importing_update_if_newer"),
                  (Update.IMPORT_ANKI_PACKAGE_UPDATE_CONDITION_ALWAYS, "importing_update_always"),
                  (Update.IMPORT_ANKI_PACKAGE_UPDATE_CONDITION_NEVER, "importing_update_never")]
DELIMS = [(CsvMetadata.Delimiter.TAB, "importing_tab"), (CsvMetadata.Delimiter.PIPE, "importing_pipe"),
          (CsvMetadata.Delimiter.SEMICOLON, "importing_semicolon"), (CsvMetadata.Delimiter.COLON, "importing_colon"),
          (CsvMetadata.Delimiter.COMMA, "importing_comma"), (CsvMetadata.Delimiter.SPACE, None)]
DUPES = [(CsvMetadata.DupeResolution.UPDATE, "importing_update"),
         (CsvMetadata.DupeResolution.PRESERVE, "importing_preserve"),
         (CsvMetadata.DupeResolution.DUPLICATE, "importing_duplicate")]
SCOPES = [(CsvMetadata.MatchScope.NOTETYPE, "Note type"),
          (CsvMetadata.MatchScope.NOTETYPE_AND_DECK, "Note type and deck")]
PACKAGE_EXTS = (".apkg", ".colpkg", ".zip")
TEXT_EXTS = (".csv", ".tsv", ".txt")
UPLOAD_DIR = os.path.join(tempfile.gettempdir(), "flashcards-uploads")
UPLOAD_MAX_AGE = 24 * 3600
PREVIEW_ROWS = 10
LOG_ROWS = 500

# token -> {"user", "path", "name"}; uploads waiting for the options/confirm step
_uploads = {}
# user -> last import log (shown on the Import page)
_last_log = {}

CHK = 'style="width:22px;height:22px;flex:none"'


def check(name, label, on, help_text=""):
    tip = f' title="{html.escape(help_text)}"' if help_text else ""
    return (f'<label class="row" style="margin:6px 0;color:var(--ink);font-size:17px;min-height:44px;flex-wrap:nowrap"{tip}>'
            f'<input type="checkbox" name="{name}" value="1" {CHK}{" checked" if on else ""}> '
            f'{html.escape(label)}</label>')


def options(choices, current):
    return "".join(f'<option value="{v}"{" selected" if v == current else ""}>{html.escape(t)}</option>'
                   for v, t in choices)


def plural(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"


# ---------- uploads ----------
def _clean_uploads():
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    now = time.time()
    for f in os.listdir(UPLOAD_DIR):
        p = os.path.join(UPLOAD_DIR, f)
        try:
            if now - os.path.getmtime(p) > UPLOAD_MAX_AGE:
                os.remove(p)
        except OSError:
            pass
    for t in [t for t, u in _uploads.items() if not os.path.exists(u["path"])]:
        _uploads.pop(t, None)


async def save_upload(user, file, ext):
    _clean_uploads()
    token = secrets.token_hex(12)
    path = os.path.join(UPLOAD_DIR, token + ext)
    with open(path, "wb") as fh:
        while chunk := await file.read(1 << 20):
            fh.write(chunk)
    _uploads[token] = {"user": user, "path": path, "name": os.path.basename(file.filename or "file" + ext)}
    return token


def get_upload(user, token):
    u = _uploads.get(token)
    if not u or u["user"] != user or not os.path.exists(u["path"]):
        return None
    return u


def drop_upload(token):
    u = _uploads.pop(token, None)
    if u and os.path.exists(u["path"]):
        os.remove(u["path"])


def ext_of(name):
    name = (name or "").lower()
    return next((e for e in PACKAGE_EXTS + TEXT_EXTS if name.endswith(e)), "")


# ---------- import log ----------
# (log field, status shown). `first_field_match` depends on the chosen "Existing notes" setting.
LOG_KINDS = [("new", "Added"), ("updated", "Updated"), ("duplicate", "Skipped (already in your collection)"),
             ("conflicting", "Skipped (note type changed)"), ("first_field_match", None),
             ("missing_notetype", "Skipped (note type missing)"), ("missing_deck", "Skipped (deck missing)"),
             ("empty_first_field", "Skipped (empty first field)")]


FFM_STATUS = {CsvMetadata.DupeResolution.UPDATE: ("updated", "Updated (first field matched)"),
              CsvMetadata.DupeResolution.PRESERVE: ("skipped", "Skipped (first field matches a note)"),
              CsvMetadata.DupeResolution.DUPLICATE: ("new", "Added (first field matches a note)")}


def ffm(log):
    """What happened to notes whose first field matched an existing one: it depends on the
    "Existing notes" choice, as in Anki's import log. Returns (kind, label)."""
    return FFM_STATUS.get(log.dupe_resolution, FFM_STATUS[CsvMetadata.DupeResolution.UPDATE])


def count_new(log):
    return len(log.new) + (len(log.first_field_match) if ffm(log)[0] == "new" else 0)


def summarise(log):
    """One line like Anki's import summary."""
    kind, n_ffm = ffm(log)[0], len(log.first_field_match)
    parts = [plural(count_new(log), "new note")]
    updated = len(log.updated) + (n_ffm if kind == "updated" else 0)
    if updated:
        parts.append(f"{updated} updated")
    skipped = len(log.duplicate) + len(log.conflicting) + len(log.missing_notetype) + len(log.missing_deck) \
        + len(log.empty_first_field) + (n_ffm if kind == "skipped" else 0)
    if skipped:
        parts.append(f"{skipped} skipped")
    return ", ".join(parts)


def remember_log(user, name, log):
    rows = []
    for kind, label in LOG_KINDS:
        if label is None:
            label = ffm(log)[1]
        for n in getattr(log, kind):
            rows.append((label, n.id.nid, [core.strip_html(f)[:80] for f in n.fields[:2]]))
    _last_log[user] = {"name": name, "when": time.strftime("%Y-%m-%d %H:%M"), "summary": summarise(log),
                       "found": log.found_notes, "rows": rows}


def log_html(user):
    lg = _last_log.get(user)
    if not lg:
        return ""
    rows = "".join(
        f'<tr><td>{html.escape(label)}</td><td>{html.escape(f[0] if f else "")}</td>'
        f'<td>{html.escape(f[1] if len(f) > 1 else "")}</td>'
        f'<td>{f"<a href=/browse?q=nid:{nid}>Browse</a>" if nid else ""}</td></tr>'
        for label, nid, f in lg["rows"][:LOG_ROWS])
    more = (f'<p class="muted">Showing the first {LOG_ROWS} of {len(lg["rows"])} notes.</p>'
            if len(lg["rows"]) > LOG_ROWS else "")
    return f"""<div class="box" id="log"><h3 style="margin-top:0">Last import: {html.escape(lg['name'])}</h3>
<p>{html.escape(lg['summary'])} <span class="muted">· {lg['found']} notes found in file · {lg['when']}</span></p>
<details><summary class="btn">Details</summary><div style="overflow-x:auto;margin-top:10px"><table>
<tr><th>Status</th><th>First field</th><th>Second field</th><th></th></tr>{rows}</table></div>{more}</details></div>"""


# ---------- import page ----------
@router.get("/import", response_class=HTMLResponse)
def import_page(request: Request):
    user = core.current_user(request)
    if not user: return core.need_login(request)
    with core.collection(user) as col:
        tr = col.tr
        p = col._backend.get_import_anki_package_presets()
    upd = lambda name, cur: (f'<select name="{name}" style="width:auto">'
                             + options([(v, getattr(tr, t)()) for v, t in UPDATE_CHOICES], cur) + "</select>")
    body = f"""<div class="box"><h2 style="margin-top:0">Import a deck</h2>
<p>Choose an Anki deck file (<b>.apkg</b>), for example a shared deck from ankiweb.net/shared or one exported from Anki.
A whole-collection backup (<b>.colpkg</b>) can be imported here too; you will be asked to confirm, because it replaces everything.</p>
<form method="post" action="/import" enctype="multipart/form-data"><input type="hidden" name="opts" value="1">
<input type="file" name="file" accept=".apkg,.colpkg,.zip" required>
<details style="margin-top:10px"><summary class="btn">Options</summary><div style="margin-top:8px">
{check("with_scheduling", tr.importing_also_import_progress(), p.with_scheduling, tr.importing_include_reviews_help())}
{check("with_deck_configs", tr.importing_with_deck_configs(), p.with_deck_configs, tr.importing_with_deck_configs_help())}
<h4 style="margin:12px 0 0">{html.escape(tr.importing_updates())}</h4>
{check("merge_notetypes", tr.importing_merge_notetypes(), p.merge_notetypes, tr.importing_merge_notetypes_help())}
<div class="row"><label style="margin:0;min-width:160px;color:var(--ink);font-size:17px" title="{html.escape(tr.importing_update_notes_help())}">{html.escape(tr.importing_update_notes())}</label>{upd("update_notes", p.update_notes)}</div>
<div class="row" style="margin-top:8px"><label style="margin:0;min-width:160px;color:var(--ink);font-size:17px" title="{html.escape(tr.importing_update_notetypes_help())}">{html.escape(tr.importing_update_notetypes())}</label>{upd("update_notetypes", p.update_notetypes)}</div>
</div></details>
<p><button class="primary">Import</button></p></form>
<p class="muted" style="font-size:14px">Large decks with lots of images can take a minute.</p></div>
<div class="box"><h2 style="margin-top:0">Import a text file</h2>
<p>Choose a <b>.csv</b>, <b>.tsv</b> or <b>.txt</b> file with one note per line. Next you can pick the separator,
note type, deck and which column goes into which field.</p>
<form method="post" action="/import/csv" enctype="multipart/form-data">
<input type="file" name="file" accept=".csv,.tsv,.txt,text/plain,text/csv" required>
<p><button class="primary">Next</button></p></form></div>
{log_html(user)}
<div class="box row" style="justify-content:space-between"><span>Make a backup or share a deck.</span>
<a class="btn" href="/export">Export…</a></div>"""
    return core.page("Import", body, user, "import", core.take_flash(request))


def package_options(form, presets):
    if not form.get("opts"):
        return presets  # no options sent: use the engine's saved defaults

    def cond(name, default):
        try:
            v = int(form.get(name, default))
        except ValueError:
            return default
        return v if v in Update.values() else default
    return ImportAnkiPackageOptions(
        with_scheduling=bool(form.get("with_scheduling")), with_deck_configs=bool(form.get("with_deck_configs")),
        merge_notetypes=bool(form.get("merge_notetypes")),
        update_notes=cond("update_notes", presets.update_notes),
        update_notetypes=cond("update_notetypes", presets.update_notetypes))


@router.post("/import")
async def do_import(request: Request, file: UploadFile = File(...)):
    user = core.current_user(request)
    if not user: return core.need_login(request)
    form = await request.form()
    ext = ext_of(file.filename)
    if ext not in PACKAGE_EXTS:
        core.set_flash(request, "!Please choose an .apkg or .colpkg file. Text files go under “Import a text file”.")
        return RedirectResponse("/import", 303)
    token = await save_upload(user, file, ext)
    if ext == ".colpkg":
        return RedirectResponse(f"/import/colpkg/{token}", 303)
    up = _uploads[token]
    with core.collection(user) as col:
        try:
            opts = package_options(form, col._backend.get_import_anki_package_presets())
            log = col.import_anki_package(ImportAnkiPackageRequest(package_path=up["path"], options=opts)).log
            remember_log(user, up["name"], log)
            n_new = count_new(log)
            extra = summarise(log).split(", ", 1)[1:]  # updated / skipped, if any
            core.set_flash(request, f"Imported {n_new} new notes from {up['name']}"
                           + (f" ({extra[0]})" if extra else "") + ". Details are on the Import page.")
        except Exception as e:
            core.set_flash(request, f"!Import failed: {e}")
    drop_upload(token)
    return RedirectResponse("/", 303)


# ---------- .colpkg: replace the whole collection ----------
@router.get("/import/colpkg/{token}", response_class=HTMLResponse)
def colpkg_confirm(request: Request, token: str):
    user = core.current_user(request)
    if not user: return core.need_login(request)
    up = get_upload(user, token)
    if not up:
        core.set_flash(request, "!That upload has expired. Please choose the file again.")
        return RedirectResponse("/import", 303)
    with core.collection(user) as col:
        tr = col.tr
        notes, cards = col.note_count(), col.card_count()
    body = f"""<div class="box"><h2 style="margin-top:0">Replace your whole collection?</h2>
<p class="flash err">{html.escape(tr.importing_this_will_delete_your_existing_collection())}</p>
<p>File: <b>{html.escape(up['name'])}</b> ({os.path.getsize(up['path']) // 1024} KB)</p>
<p>Your account has <b>{plural(notes, 'note')}</b> and <b>{plural(cards, 'card')}</b> now. All of them, with their
review history, decks, note types and options, will be replaced by what is in the file.
A backup of the current collection is saved first (in your account's <code>backups</code> folder).</p>
<p class="muted">To add a shared deck to what you already have, use an <b>.apkg</b> file instead.</p>
<form method="post" action="/import/colpkg/{token}">
{check("confirm", "I understand that everything in this account will be replaced", False)}
<div class="row" style="margin-top:12px"><button class="primary danger" style="background:var(--again);border-color:var(--again);color:#fff">Replace collection</button>
<a class="btn" href="/import/colpkg/{token}/cancel">Cancel</a></div></form></div>"""
    return core.page("Replace collection", body, user, "import", core.take_flash(request))


@router.get("/import/colpkg/{token}/cancel")
def colpkg_cancel(request: Request, token: str):
    user = core.current_user(request)
    if not user: return core.need_login(request)
    if get_upload(user, token):
        drop_upload(token)
    return RedirectResponse("/import", 303)


def backup_folder(user):
    return maintenance.backup_folder(user)


@router.post("/import/colpkg/{token}")
async def colpkg_import(request: Request, token: str):
    user = core.current_user(request)
    if not user: return core.need_login(request)
    form = await request.form()
    up = get_upload(user, token)
    if not up:
        core.set_flash(request, "!That upload has expired. Please choose the file again.")
        return RedirectResponse("/import", 303)
    if not form.get("confirm"):
        core.set_flash(request, "!Tick the box to confirm that everything will be replaced.")
        return RedirectResponse(f"/import/colpkg/{token}", 303)
    with core.collection(user) as col:
        try:
            # Through the backups module, so its own rotation (keep 30) applies.
            maintenance.make_backup(user, col)
        except Exception as e:
            core.set_flash(request, f"!Could not back up the current collection, so nothing was replaced: {e}")
            return RedirectResponse(f"/import/colpkg/{token}", 303)
        # The old Collection object is closed and a fresh one opened (the old file, if the import failed).
        ok, err = maintenance.replace_collection(user, col, up["path"])
        if ok:
            core.set_flash(request, f"Collection replaced with {up['name']}: "
                           f"{plural(core._cols[user].note_count(), 'note')}.")
        else:
            core.set_flash(request, f"!Import failed, your collection was not changed: {err}")
    drop_upload(token)
    return RedirectResponse("/" if ok else "/import", 303)


# ---------- text (CSV) import ----------
@router.post("/import/csv")
async def csv_upload(request: Request, file: UploadFile = File(...)):
    user = core.current_user(request)
    if not user: return core.need_login(request)
    ext = ext_of(file.filename)
    if ext not in TEXT_EXTS:
        core.set_flash(request, "!Please choose a .csv, .tsv or .txt file. Anki decks (.apkg) go under “Import a deck”.")
        return RedirectResponse("/import", 303)
    token = await save_upload(user, file, ext)
    return RedirectResponse(f"/import/csv/{token}", 303)


def int_or(v, default=None):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def csv_metadata(col, path, q):
    """Metadata from the engine, with the choices from the form/query applied on top.

    Changing the separator, note type or deck asks the engine again, so its column
    guesses and default field mapping match Anki's."""
    delim = int_or(q.get("delimiter"))
    if delim not in CsvMetadata.Delimiter.values():
        delim = None
    m = col.get_csv_metadata(path, delim)
    nt = q.get("notetype", "")
    if int_or(nt) and col.models.get(int(nt)):
        if not (m.HasField("global_notetype") and m.global_notetype.id == int(nt)):
            req = pb.CsvMetadataRequest(path=path, notetype_id=int(nt))
            if delim is not None:
                req.delimiter = delim
            m2 = col._backend.get_csv_metadata(req)
            m.global_notetype.CopyFrom(m2.global_notetype)
    dk = q.get("deck", "")
    if int_or(dk) and col.decks.get(int(dk), default=False):
        m.deck_id = int(dk)
    if "html" in q or q.get("sent"):
        if not m.force_is_html:
            m.is_html = bool(q.get("html"))
    return m


def column_choices(m):
    n = max([len(r.vals) for r in m.preview] + [len(m.column_labels), 0])
    first = m.preview[0].vals if m.preview else []
    out = []
    for i in range(n):
        label = m.column_labels[i] if i < len(m.column_labels) and m.column_labels[i] else ""
        sample = label or (first[i] if i < len(first) else "")
        sample = core.strip_html(sample)[:30]
        out.append((i + 1, f"Column {i + 1}" + (f": {sample}" if sample else "")))
    return out


def csv_form(col, user, token, up, m, error=""):
    tr = col.tr
    cols = column_choices(m)
    nothing = [(0, "(Nothing)")]
    # separator
    if m.force_delimiter:
        delim_html = (f'<input type="hidden" name="delimiter" value="{m.delimiter}">'
                      f'<p style="margin:0">{html.escape(dict((v, t) for v, t in delim_choices(tr))[m.delimiter])} '
                      f'<span class="muted">(set by the file)</span></p>')
    else:
        delim_html = (f'<select name="delimiter" class="refresh" style="width:auto">'
                      f'{options(delim_choices(tr), m.delimiter)}</select>')
    # note type
    nt_choices = [(str(n.id), n.name) for n in col.models.all_names_and_ids()]
    nt_cur = str(m.global_notetype.id) if m.HasField("global_notetype") else "column"
    if m.notetype_column:
        nt_choices.append(("column", f"From column {m.notetype_column}"))
    # deck
    deck_choices = [(str(d.id), d.name) for d in col.decks.all_names_and_ids(include_filtered=False)]
    which = m.WhichOneof("deck")
    if which == "deck_column":
        deck_choices.append(("column", f"From column {m.deck_column}"))
        deck_cur = "column"
    elif which == "deck_name":
        deck_choices.append(("name", f"{m.deck_name} (new deck)"))
        deck_cur = "name"
    else:
        deck_cur = str(m.deck_id)
    # field mapping
    if m.HasField("global_notetype"):
        names = col.models.field_names(col.models.get(m.global_notetype.id))
        fc = list(m.global_notetype.field_columns)
        rows = "".join(
            f'<div class="row" style="flex-wrap:nowrap"><label for="map{i}" style="margin:0;min-width:120px;color:var(--ink);font-size:17px">'
            f'{html.escape(name)}</label><select id="map{i}" name="map{i}" style="flex:1">'
            f'{options(nothing + cols, fc[i] if i < len(fc) else 0)}</select></div>'
            for i, name in enumerate(names))
        mapping = f'<div style="display:grid;gap:8px">{rows}</div>'
    else:
        mapping = '<p class="muted">Fields are filled in order, using the note type named in each line.</p>'
    tags_sel = (f'<select name="tags_column" style="flex:1">{options(nothing + cols, m.tags_column)}</select>')
    # preview
    width = len(cols)
    head = "".join(f"<th>{html.escape(t)}</th>" for _, t in cols)
    prev = "".join("<tr>" + "".join(f"<td>{html.escape(core.strip_html(r.vals[i]) if i < len(r.vals) else '')[:60]}</td>"
                                    for i in range(width)) + "</tr>" for r in m.preview[:PREVIEW_ROWS])
    more = (f'<p class="muted" style="font-size:14px">Showing the first {PREVIEW_ROWS} lines.</p>'
            if len(m.preview) > PREVIEW_ROWS else "")
    err = f'<div class="flash err">{html.escape(error)}</div>' if error else ""
    html_on = m.is_html
    html_box = (f'<label class="row" style="margin:6px 0;color:var(--ink);font-size:17px;min-height:44px" '
                f'title="{html.escape(core.strip_html(tr.importing_allow_html_in_fields_help()))}">'
                f'<input type="checkbox" name="html" value="1" class="refresh" {CHK}{" checked" if html_on else ""}'
                f'{" disabled" if m.force_is_html else ""}> {html.escape(tr.importing_allow_html_in_fields())}'
                f'{" <span class=muted>(set by the file)</span>" if m.force_is_html else ""}</label>')
    if m.force_is_html and html_on:
        html_box += '<input type="hidden" name="html" value="1">'
    return f"""{err}<div class="box"><h2 style="margin-top:0">Import {html.escape(up['name'])}</h2>
<form id="csvform" method="post" action="/import/csv/{token}"><input type="hidden" name="sent" value="1">
<h3 style="margin:0 0 6px">{html.escape(tr.importing_file())}</h3>
<div class="row"><label style="margin:0;min-width:160px;color:var(--ink);font-size:17px">{html.escape(tr.importing_field_separator())}</label>{delim_html}</div>
{html_box}
<h3 style="margin:16px 0 6px">{html.escape(tr.importing_import_options())}</h3>
<label for="notetype">Note type</label><select id="notetype" name="notetype" class="refresh">{options(nt_choices, nt_cur)}</select>
<label for="deck">Deck</label><select id="deck" name="deck">{options(deck_choices, deck_cur)}</select>
<label for="dupe">{html.escape(tr.importing_existing_notes())}</label>
<select id="dupe" name="dupe" title="{html.escape(tr.importing_existing_notes_help())}">{options([(v, getattr(tr, t)()) for v, t in DUPES], m.dupe_resolution)}</select>
<label for="scope">{html.escape(tr.importing_match_scope())}</label>
<select id="scope" name="scope" title="{html.escape(tr.importing_match_scope_help())}">{options(SCOPES, m.match_scope)}</select>
<h3 style="margin:16px 0 6px">{html.escape(tr.importing_field_mapping())}</h3>
{mapping}
<div class="row" style="flex-wrap:nowrap;margin-top:8px"><label style="margin:0;min-width:120px;color:var(--ink);font-size:17px">Tags</label>{tags_sel}</div>
<label for="gtags">{html.escape(tr.importing_tag_all_notes())}</label>
<input id="gtags" name="global_tags" value="{html.escape(' '.join(m.global_tags))}" autocapitalize="none" placeholder="space-separated tags">
<label for="utags">{html.escape(tr.importing_tag_updated_notes())}</label>
<input id="utags" name="updated_tags" value="{html.escape(' '.join(m.updated_tags))}" autocapitalize="none" placeholder="space-separated tags">
<div class="row" style="margin-top:16px"><button class="primary" name="go" value="1">Import</button>
<button formmethod="get" formaction="/import/csv/{token}" id="refresh">Refresh preview</button>
<a class="btn" href="/import/csv/{token}/cancel">Cancel</a></div></form></div>
<div class="box"><h3 style="margin-top:0">Preview</h3><div style="overflow-x:auto"><table><tr>{head}</tr>{prev}</table></div>{more}</div>
<script>document.querySelectorAll('#csvform .refresh').forEach(function(e){{e.addEventListener('change',function(){{
document.getElementById('refresh').click();}})}});</script>"""


def delim_choices(tr):
    return [(v, getattr(tr, t)() if t else "Space") for v, t in DELIMS]


def show_csv(request, user, token, q, error=""):
    up = get_upload(user, token)
    if not up:
        core.set_flash(request, "!That upload has expired. Please choose the file again.")
        return RedirectResponse("/import", 303)
    with core.collection(user) as col:
        try:
            m = csv_metadata(col, up["path"], q)
        except Exception as e:
            drop_upload(token)
            core.set_flash(request, f"!Could not read {up['name']}: {e}")
            return RedirectResponse("/import", 303)
        try:  # keep what was chosen (after a refused import); a refresh shows the engine's mapping
            apply_choices(col, m, q, mapping=bool(q.get("go")))
        except ValueError:
            pass
        body = csv_form(col, user, token, up, m, error)
    return core.page("Import text", body, user, "import")


@router.get("/import/csv/{token}", response_class=HTMLResponse)
def csv_options(request: Request, token: str):
    user = core.current_user(request)
    if not user: return core.need_login(request)
    return show_csv(request, user, token, dict(request.query_params))


@router.get("/import/csv/{token}/cancel")
def csv_cancel(request: Request, token: str):
    user = core.current_user(request)
    if not user: return core.need_login(request)
    if get_upload(user, token):
        drop_upload(token)
    return RedirectResponse("/import", 303)


def apply_choices(col, m, q, mapping=True):
    """Put the form's choices into the engine's metadata. Raises ValueError with a message for the user."""
    tr = col.tr
    tags = lambda s: [t for t in re.split(r"\s+", s or "") if t]
    if "global_tags" in q:
        del m.global_tags[:]; m.global_tags.extend(tags(q.get("global_tags")))
    if "updated_tags" in q:
        del m.updated_tags[:]; m.updated_tags.extend(tags(q.get("updated_tags")))
    if int_or(q.get("dupe")) in CsvMetadata.DupeResolution.values():
        m.dupe_resolution = int(q["dupe"])
    if int_or(q.get("scope")) in CsvMetadata.MatchScope.values():
        m.match_scope = int(q["scope"])
    ncols = len(column_choices(m))
    if "tags_column" in q:
        t = int_or(q.get("tags_column"), 0)
        m.tags_column = t if 0 <= t <= ncols else 0
    if not mapping or not m.HasField("global_notetype"):
        return
    nfields = len(col.models.get(m.global_notetype.id)["flds"])
    fc = []
    for i in range(nfields):
        v = int_or(q.get(f"map{i}"), 0)
        fc.append(v if 0 <= v <= ncols else 0)
    del m.global_notetype.field_columns[:]
    m.global_notetype.field_columns.extend(fc)
    if not fc or not fc[0]:
        raise ValueError(tr.importing_the_first_field_of_the_note())


@router.post("/import/csv/{token}")
async def csv_import(request: Request, token: str):
    user = core.current_user(request)
    if not user: return core.need_login(request)
    q = dict(await request.form())
    up = get_upload(user, token)
    if not up:
        core.set_flash(request, "!That upload has expired. Please choose the file again.")
        return RedirectResponse("/import", 303)
    with core.collection(user) as col:
        try:
            m = csv_metadata(col, up["path"], q)
            apply_choices(col, m, q)
            if not m.HasField("global_notetype") and not m.notetype_column:
                raise ValueError("Please choose a note type.")
            log = col.import_csv(ImportCsvRequest(path=up["path"], metadata=m)).log
        except Exception as e:
            err = str(e) or "The import did not work."
        else:
            err = ""
    if err:
        return show_csv(request, user, token, q, err)
    remember_log(user, up["name"], log)
    drop_upload(token)
    core.set_flash(request, f"Imported {up['name']}: {summarise(log)}.")
    return RedirectResponse("/import#log", 303)


# ---------- export ----------
FORMATS = [("colpkg", "exporting_anki_collection_package", ".colpkg"),
           ("apkg", "exporting_anki_deck_package", ".apkg"),
           ("notes", "exporting_notes_in_plain_text", ".txt"),
           ("cards", "exporting_cards_in_plain_text", ".txt")]
# which options each format has (the others are hidden)
FORMAT_OPTS = {"colpkg": ["media", "legacy"], "apkg": ["scheduling", "deck_configs", "media", "legacy"],
               "notes": ["html", "tags", "deck", "notetype", "guid"], "cards": ["html"]}
OPT_DEFAULTS = {"scheduling": True, "deck_configs": False, "media": True, "legacy": False, "html": True,
                "tags": True, "deck": False, "notetype": False, "guid": False}

EXPORT_JS = """<script>
(function(){var f=document.getElementById('expform');function upd(){
var fmt=(f.querySelector('input[name=fmt]:checked')||{}).value;
f.querySelectorAll('[data-fmts]').forEach(function(e){e.style.display=e.dataset.fmts.split(' ').indexOf(fmt)>=0?'':'none'});}
f.querySelectorAll('input[name=fmt]').forEach(function(r){r.addEventListener('change',upd)});upd();})();
</script>"""


@router.get("/export", response_class=HTMLResponse)
def export_page(request: Request, did: int = 0, q: str = "", fmt: str = ""):
    user = core.current_user(request)
    if not user: return core.need_login(request)
    with core.collection(user) as col:
        tr = col.tr
        decks = [(str(d.id), d.name) for d in col.decks.all_names_and_ids()]
        n_sel = len(col.find_notes(q)) if q else 0
        err = ""
    if not fmt:
        fmt = "apkg" if (did or q) else "colpkg"
    scope = [("0", tr.exporting_all_decks())]
    if q:
        scope.append(("search", f"{tr.exporting_selected_notes()} ({plural(n_sel, 'note')} from Browse)"))
    scope += decks
    cur_scope = "search" if q else str(did)
    radios = "".join(
        f'<label class="row" style="margin:6px 0;color:var(--ink);font-size:17px;min-height:44px">'
        f'<input type="radio" name="fmt" value="{k}" {CHK}{" checked" if k == fmt else ""}> '
        f'{html.escape(getattr(tr, t)())} <span class="muted">({ext})</span></label>' for k, t, ext in FORMATS)
    labels = {"scheduling": tr.exporting_include_scheduling_information(), "deck_configs": tr.exporting_include_deck_configs(),
              "media": tr.exporting_include_media(), "legacy": tr.exporting_support_older_anki_versions(),
              "html": tr.exporting_include_html_and_media_references(), "tags": tr.exporting_include_tags(),
              "deck": tr.exporting_include_deck(), "notetype": tr.exporting_include_notetype(),
              "guid": tr.exporting_include_guid()}
    opts = "".join(
        f'<div data-fmts="{" ".join(f for f, o in FORMAT_OPTS.items() if k in o)}">{check(k, labels[k], OPT_DEFAULTS[k])}</div>'
        for k in OPT_DEFAULTS)
    body = f"""<div class="box"><h2 style="margin-top:0">Export</h2>
<form id="expform" method="post" action="/export"><input type="hidden" name="q" value="{html.escape(q)}">
<h3 style="margin:0 0 4px">Format</h3>{radios}
<div data-fmts="apkg notes cards"><label for="scope">Include</label>
<select id="scope" name="scope">{options(scope, cur_scope)}</select></div>
<div data-fmts="colpkg"><p class="muted" style="font-size:15px">A collection package holds everything in this
account: all decks, note types, options, review history and (if ticked) media. Importing it replaces a whole
collection, so it is the format for backups and for moving to desktop Anki.</p></div>
<h3 style="margin:14px 0 4px">Options</h3>{opts}
<div class="row" style="margin-top:14px"><button class="primary">Export</button>
<a class="btn" href="/import">Back</a></div></form></div>{EXPORT_JS}"""
    return core.page("Export", body, user, "import", core.take_flash(request) or err)


def safe_name(name, default):
    return re.sub(r"[^\w.@ -]+", "_", name).strip() or default


@router.post("/export")
async def do_export(request: Request):
    user = core.current_user(request)
    if not user: return core.need_login(request)
    form = await request.form()
    fmt = form.get("fmt", "")
    if fmt not in FORMAT_OPTS:
        core.set_flash(request, "!Please choose an export format.")
        return RedirectResponse("/export", 303)
    on = lambda k: bool(form.get(k)) if k in FORMAT_OPTS[fmt] else False
    q = form.get("q", "")
    scope = form.get("scope", "0")
    out_dir = tempfile.mkdtemp(prefix="flashcards-export-")
    stamp = time.strftime("%Y-%m-%d@%H-%M-%S")
    with core.collection(user) as col:
        try:
            limit, label = None, "All Decks"
            if fmt != "colpkg":
                if scope == "search" and q:
                    label = "Selected Notes"
                    limit = (CardIdsLimit(col.find_cards(q)) if fmt == "cards" else NoteIdsLimit(col.find_notes(q)))
                elif int_or(scope, 0):
                    did = int(scope)
                    if not col.decks.get(did, default=False):
                        raise ValueError("That deck no longer exists.")
                    limit, label = DeckIdLimit(did), col.decks.name(did)
            if fmt == "colpkg":
                name = f"collection-{stamp}.colpkg"
                out = os.path.join(out_dir, name)
                try:
                    col.export_collection_package(out, include_media=on("media"), legacy=on("legacy"))
                finally:
                    try:
                        col.reopen()  # the engine closes the collection to write the package
                    except Exception:
                        core._cols.pop(user, None)  # opened afresh on the next request
            elif fmt == "apkg":
                name = safe_name(label, "deck") + ".apkg"
                out = os.path.join(out_dir, name)
                col.export_anki_package(out_path=out, limit=limit, options=ExportAnkiPackageOptions(
                    with_scheduling=on("scheduling"), with_deck_configs=on("deck_configs"),
                    with_media=on("media"), legacy=on("legacy")))
            elif fmt == "notes":
                name = safe_name(label, "notes") + ".txt"
                out = os.path.join(out_dir, name)
                col.export_note_csv(out_path=out, limit=limit, with_html=on("html"), with_tags=on("tags"),
                                    with_deck=on("deck"), with_notetype=on("notetype"), with_guid=on("guid"))
            else:
                name = safe_name(label, "cards") + ".txt"
                out = os.path.join(out_dir, name)
                col.export_card_csv(out_path=out, limit=limit, with_html=on("html"))
        except Exception as e:
            shutil.rmtree(out_dir, ignore_errors=True)
            core.set_flash(request, f"!Export failed: {e}")
            back = "/export" + ("?" + urlencode({"q": q}) if q else "")
            return RedirectResponse(back, 303)
    media = "text/plain; charset=utf-8" if fmt in ("notes", "cards") else "application/octet-stream"
    return FileResponse(out, filename=name, media_type=media)
