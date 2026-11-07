"""Maintenance: Anki's Tools > Check Database, Tools > Empty Cards, and backups.

Engine calls only:
  - Check Database: `col.fix_integrity()` (the engine's `check_database`, as Anki's menu item).
  - Empty Cards: `col.get_empty_cards()` for the report and `col.remove_cards_and_orphaned_notes()`
    to delete, with Anki's "Keep notes with no valid cards" choice.
  - Backups: `col.create_backup()` writes the engine's no-media .colpkg backup, and restoring
    uses the engine's `import_collection_package` (what Anki's "Open Backup..." does).

Backups live in `data/users/<name>/backups/`. One is made automatically the first time a
user's collection is used each day, and the 30 newest are kept. The engine's own rotation
(12 daily / 10 weekly / 9 monthly) would thin that folder differently, so each backup is
written to an empty staging folder first and then moved in; this module does the pruning.
"""
import datetime, html, os, re, shutil, tempfile, threading
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, Response

from anki.collection import Collection
from anki.media import media_paths_from_col_path

import app as core
from flashcards.i18n import t, tn, N_

router = APIRouter()
KEEP = 30
NAME_RE = re.compile(r"backup-(\d{4})-(\d\d)-(\d\d)-(\d\d)\.(\d\d)\.(\d\d)\.colpkg")
_checked = {}            # user -> date string of the last daily-backup check (in this process)
_checked_lock = threading.Lock()


def clean(s):
    return re.sub(r"[⁨⁩]", "", s or "")


def esc(s):
    """Escape translated text for an HTML text node (quotes left alone, so English output is unchanged)."""
    return html.escape(s, quote=False)


# ---------- backups ----------
def backup_folder(user):
    return os.path.join(core.DATA, "users", user, "backups")


def list_backups(user):
    """Backup file names, newest first. Only names in the engine's format count."""
    folder = backup_folder(user)
    if not os.path.isdir(folder):
        return []
    return sorted((n for n in os.listdir(folder) if NAME_RE.fullmatch(n)), reverse=True)


def backup_time(name):
    m = NAME_RE.fullmatch(name)
    return datetime.datetime(*map(int, m.groups())) if m else None


def prune(user, keep=KEEP):
    """Delete all but the `keep` newest backups. Returns the names removed."""
    folder = backup_folder(user)
    old = list_backups(user)[keep:]
    for n in old:
        try:
            os.remove(os.path.join(folder, n))
        except OSError:
            pass
    return old


def make_backup(user, col):
    """Make a backup now through the engine. Returns the new file name, or None when the
    engine says nothing has changed since its last backup. Raises on failure."""
    folder = backup_folder(user)
    os.makedirs(folder, exist_ok=True)
    stage = tempfile.mkdtemp(prefix=".stage-", dir=folder)
    try:
        name = None
        if col.create_backup(backup_folder=stage, force=True, wait_for_completion=True):
            made = [n for n in os.listdir(stage) if NAME_RE.fullmatch(n)]
            if made:
                name = made[0]
                # Names have one-second precision: never overwrite an earlier backup.
                when = backup_time(name)
                while os.path.exists(os.path.join(folder, name)):
                    when += datetime.timedelta(seconds=1)
                    name = when.strftime("backup-%Y-%m-%d-%H.%M.%S.colpkg")
                os.replace(os.path.join(stage, made[0]), os.path.join(folder, name))
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    prune(user)
    return name


def maybe_daily_backup(user, col):
    """Called whenever a collection is used: make the day's automatic backup if there is none
    for today yet. Never raises (a failed backup must not stop studying)."""
    today = datetime.date.today().isoformat()
    with _checked_lock:
        if _checked.get(user) == today:
            return
        _checked[user] = today
    try:
        names = list_backups(user)
        if names and backup_time(names[0]).date().isoformat() == today:
            return
        if col.is_empty():
            return  # nothing worth keeping yet (a brand-new account)
        make_backup(user, col)
    except Exception as e:  # noqa: BLE001
        print(f"daily backup for {user} failed: {e}")


def replace_collection(user, col, package_path, missing_fsrs=False):
    """Replace the user's collection with a .colpkg through the engine, then open it again.
    Call while holding the user's collection lock. Returns (ok, error message). If the
    package is bad the old collection is opened unchanged."""
    path = col.path
    media_folder, media_db = media_paths_from_col_path(path)
    backend = col._backend
    col.close()
    try:
        backend.import_collection_package(col_path=path, backup_path=package_path,
                                          media_folder=media_folder, media_db=media_db)
        ok, err = True, ""
    except Exception as e:  # noqa: BLE001
        ok, err = False, str(e) or t("the file could not be read")
    new = core._cols[user] = core.i18n.open_collection(path)
    if new.get_config("fsrs", None) is None:
        new.set_config("fsrs", missing_fsrs)  # desktop Anki's meaning of a missing setting: FSRS off
    return ok, err


def fmt_size(n):
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return tn("{n} bytes", "{n} bytes", int(n)) if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1024


def fmt_time(name):
    when = backup_time(name)
    return when.strftime("%a %d %b %Y, %H:%M") if when else name


def latest_backup_line(user):
    names = list_backups(user)
    if not names:
        return t("No backups yet.")
    return tn("Latest: {time}. {n} kept (up to {keep}).", "Latest: {time}. {n} kept (up to {keep}).",
              len(names), time=fmt_time(names[0]), keep=KEEP)


@router.get("/backups", response_class=HTMLResponse)
def backups_page(request: Request):
    user = core.current_user(request)
    if not user: return core.need_login(request)
    with core.collection(user) as col:
        tr = col.tr
    names = list_backups(user)
    folder = backup_folder(user)
    rows = []
    for i, n in enumerate(names):
        size = fmt_size(os.path.getsize(os.path.join(folder, n)))
        tag = f' <span class="muted">{esc(t("(latest)"))}</span>' if i == 0 else ""
        rows.append(f"""<tr><td>{html.escape(fmt_time(n))}{tag}<br><span class="muted" style="font-size:14px">{esc(size)}</span></td>
<td style="text-align:right;white-space:nowrap"><a class="btn" href="/backups/file/{quote(n)}" download>{esc(t("Download"))}</a>
<a class="btn" href="/backups/restore/{quote(n)}">{esc(t("Restore…"))}</a></td></tr>""")
    table = (f'<table>{"".join(rows)}</table>' if rows else f'<p class="muted">{esc(t("No backups yet."))}</p>')
    body = f"""<p><a href="/settings" class="btn">{esc(t("← Settings"))}</a></p>
<div class="box"><h2 style="margin-top:0">{html.escape(tr.preferences_backups())}</h2>
<p class="muted">{esc(t("A backup of your cards is made automatically once a day, the first time you use the app that day."))}
{esc(tn("The {n} newest backup is kept.", "The {n} newest backups are kept.", KEEP))}
{esc(t("Backups hold your cards, notes, decks and review history, but not pictures or sounds (the same as Anki's backups)."))}
{esc(t("A backup is also made before a backup is restored and before a .colpkg import."))}</p>
<form method="post" action="/backups/create"><button class="primary">{esc(t("Back up now"))}</button></form></div>
<div class="box">{table}</div>"""
    return core.page(t("Backups"), body, user, "settings", core.take_flash(request))


@router.post("/backups/create")
def backups_create(request: Request):
    user = core.current_user(request)
    if not user: return core.need_login(request)
    with core.collection(user) as col:
        try:
            made = make_backup(user, col)
            msg = clean(col.tr.profiles_backup_created() if made else col.tr.profiles_backup_unchanged())
        except Exception as e:  # noqa: BLE001
            msg = "!" + clean(col.tr.profiles_backup_creation_failed(reason=str(e)))
    core.set_flash(request, msg)
    return RedirectResponse("/backups", 303)


def valid_backup(user, name):
    return NAME_RE.fullmatch(name) and os.path.isfile(os.path.join(backup_folder(user), name))


@router.get("/backups/file/{name}")
def backups_download(request: Request, name: str):
    user = core.current_user(request)
    if not user: return core.need_login(request)
    if not valid_backup(user, name):
        return Response(t("Not found"), status_code=404)
    return FileResponse(os.path.join(backup_folder(user), name), media_type="application/octet-stream",
                        filename=name)


@router.get("/backups/restore/{name}", response_class=HTMLResponse)
def restore_confirm(request: Request, name: str):
    user = core.current_user(request)
    if not user: return core.need_login(request)
    if not valid_backup(user, name):
        core.set_flash(request, "!" + t("That backup was not found."))
        return RedirectResponse("/backups", 303)
    with core.collection(user) as col:
        notes, cards = col.note_count(), col.card_count()
    # markup kept in the key: splitting "replaces everything" out would break the sentence
    replaces = t("This <b>replaces everything</b> in your collection (now {notes} notes and {cards} cards) with the backup.",
                 notes=notes, cards=cards)
    body = f"""<div class="box"><h2 style="margin-top:0">{esc(t("Restore backup"))}</h2>
<p>{core.ht(t("Restore the backup from {time}?"), time=f"<b>{html.escape(fmt_time(name))}</b>")}</p>
<p>{replaces}
{esc(t("Changes made since then will be lost from the collection, but a backup of how it is now is made first, so you can go back."))}
{esc(t("Pictures and sounds are not changed."))}</p>
<form method="post" action="/backups/restore/{quote(name)}">
<label class="row" style="color:var(--ink);font-size:17px;min-height:44px;cursor:pointer">
<input type="checkbox" name="confirm" value="1" style="width:22px;height:22px;flex:none;margin:0">
<span>{esc(t("I understand that my current collection will be replaced."))}</span></label>
<div class="row"><button class="primary danger" style="color:#fff;background:var(--again);border-color:var(--again)">{esc(t("Restore"))}</button>
<a class="btn" href="/backups">{esc(t("Cancel"))}</a></div></form></div>"""
    return core.page(t("Restore backup"), body, user, "settings", core.take_flash(request))


@router.post("/backups/restore/{name}")
async def restore(request: Request, name: str):
    user = core.current_user(request)
    if not user: return core.need_login(request)
    form = await request.form()
    if not valid_backup(user, name):
        core.set_flash(request, "!" + t("That backup was not found."))
        return RedirectResponse("/backups", 303)
    if not form.get("confirm"):
        core.set_flash(request, "!" + t("Tick the box to confirm that your collection will be replaced."))
        return RedirectResponse(f"/backups/restore/{quote(name)}", 303)
    # Copy the chosen file first: the safety backup below may prune it if it is the oldest.
    fd, copy = tempfile.mkstemp(suffix=".colpkg", dir=backup_folder(user))
    os.close(fd)
    try:
        shutil.copyfile(os.path.join(backup_folder(user), name), copy)
        with core.collection(user) as col:
            try:
                make_backup(user, col)
            except Exception as e:  # noqa: BLE001
                core.set_flash(request, "!" + t("Could not back up the current collection, so nothing was restored: {error}",
                                                error=e))
                return RedirectResponse("/backups", 303)
            ok, err = replace_collection(user, col, copy)
            new = core._cols[user]
            if ok:
                core.set_flash(request, tn("Backup from {time} restored: {n} notes.", "Backup from {time} restored: {n} notes.",
                                           new.note_count(), time=fmt_time(name)))
            else:
                core.set_flash(request, "!" + t("Restore failed, your collection was not changed: {error}", error=err))
    finally:
        try:
            os.remove(copy)
        except OSError:
            pass
    return RedirectResponse("/" if ok else "/backups", 303)


# ---------- Check Database ----------
CHECK_INTRO = N_("Looks for and fixes problems in your collection (cards without notes, wrong field counts, "
                 "broken properties, missing decks or templates) and rebuilds and optimises the database. "
                 "This is Anki's Tools > Check Database.")   # shown with esc(t(CHECK_INTRO))


@router.get("/check-database", response_class=HTMLResponse)
def check_database_page(request: Request):
    user = core.current_user(request)
    if not user: return core.need_login(request)
    with core.collection(user) as col:
        title = col.tr.database_check_title()
    body = f"""<p><a href="/settings" class="btn">{esc(t("← Settings"))}</a></p>
<div class="box"><h2 style="margin-top:0">{html.escape(title)}</h2><p class="muted">{esc(t(CHECK_INTRO))}</p>
<form method="post" action="/check-database"><button class="primary">{html.escape(title)}</button></form></div>"""
    return core.page(title, body, user, "settings", core.take_flash(request))


@router.post("/check-database", response_class=HTMLResponse)
def check_database(request: Request):
    user = core.current_user(request)
    if not user: return core.need_login(request)
    with core.collection(user) as col:
        title = col.tr.database_check_title()
        text, ok = col.fix_integrity()
    lines = [clean(l) for l in text.split("\n") if l.strip()]
    head = t("No problems found.") if ok else t("Problems were found and fixed:")
    items = "".join(f"<li>{html.escape(l)}</li>" for l in lines)
    body = f"""<p><a href="/settings" class="btn">{esc(t("← Settings"))}</a></p>
<div class="box" id="result"><h2 style="margin-top:0">{html.escape(title)}</h2>
<p><b>{esc(head)}</b></p><ul>{items}</ul>
<div class="row"><form method="post" action="/check-database"><button>{esc(t("Check again"))}</button></form>
<a class="btn" href="/settings">{esc(t("Done"))}</a></div></div>"""
    return core.page(title, body, user, "settings")


# ---------- Empty Cards ----------
def empty_report(col):
    """(html, number of cards that would go) built from the engine's report. The engine's own
    HTML is not used as-is because it puts note type and template names in unescaped."""
    tr = col.tr
    report = col.get_empty_cards()
    groups = {}
    for n in report.notes:
        note = col.get_note(n.note_id)
        groups.setdefault(note.mid, []).append((n, note))
    parts = []
    for mid, notes in groups.items():
        nt = col.models.get(mid)
        items = []
        for n, note in notes:
            names = ", ".join(col.get_card(cid).template()["name"] for cid in n.card_ids)
            line = clean(tr.empty_cards_count_line(empty_count=len(n.card_ids),
                                                   existing_count=len(note.card_ids()), template_names=names))
            first = core.strip_html(note.fields[0])[:80] if note.fields else ""
            warn = (f' <span class="muted">{esc(t("(no valid cards: the whole note would go)"))}</span>'
                    if n.will_delete_note else "")
            items.append(f'<li><a href="/browse?q={quote(f"nid:{note.id}")}">{html.escape(first) or esc(t("(empty)"))}</a>'
                         f' — {html.escape(line)}{warn}</li>')
        parts.append(f'<h3>{html.escape(clean(tr.empty_cards_for_note_type(notetype=nt["name"] if nt else "?")))}</h3>'
                     f'<ol>{"".join(items)}</ol>')
    return "".join(parts), report


def cards_to_delete(report, keep_notes):
    """Anki's rule (aqt/emptycards.py): with "keep notes" on, a note whose cards are all empty
    keeps its first card."""
    out = []
    for n in report.notes:
        if keep_notes and n.will_delete_note:
            out.extend(n.card_ids[1:])
        else:
            out.extend(n.card_ids)
    return out


@router.get("/empty-cards", response_class=HTMLResponse)
def empty_cards_page(request: Request):
    user = core.current_user(request)
    if not user: return core.need_login(request)
    with core.collection(user) as col:
        tr = col.tr
        title = tr.empty_cards_window_title()
        report_html, report = empty_report(col)
        if report.notes:
            n = sum(len(x.card_ids) for x in report.notes)
            inner = f"""<p>{esc(t("{cards} empty card(s) in {notes} note(s).", cards=n, notes=len(report.notes)))}
{esc(t('Empty cards are cards whose front is blank because the fields they use are empty (for example a reverse '
       'card after "Add Reverse" was cleared, or a cloze number that was removed).'))}</p>
<form method="post" action="/empty-cards" onsubmit="return confirm({html.escape(core.js(t("Delete the empty cards?")))})">
<label class="row" style="color:var(--ink);font-size:17px;min-height:44px;cursor:pointer">
<input type="checkbox" name="keep_notes" value="1" checked style="width:22px;height:22px;flex:none;margin:0">
<span>{html.escape(tr.empty_cards_preserve_notes_checkbox())}</span></label>
<button class="danger">{html.escape(tr.empty_cards_delete_button())}</button></form>
<div id="report">{report_html}</div>"""
        else:
            inner = f'<p>{html.escape(tr.empty_cards_not_found())}</p>'
    body = f"""<p><a href="/settings" class="btn">{esc(t("← Settings"))}</a></p>
<div class="box"><h2 style="margin-top:0">{html.escape(title)}</h2>{inner}</div>"""
    return core.page(title, body, user, "settings", core.take_flash(request))


@router.post("/empty-cards")
async def empty_cards_delete(request: Request):
    user = core.current_user(request)
    if not user: return core.need_login(request)
    form = await request.form()
    keep = form.get("keep_notes") == "1"
    with core.collection(user) as col:
        cids = cards_to_delete(col.get_empty_cards(), keep)   # always the engine's current report
        if cids:
            col.remove_cards_and_orphaned_notes(cids)
        core.set_flash(request, clean(col.tr.empty_cards_deleted_count(cards=len(cids))))
    return RedirectResponse("/empty-cards", 303)
