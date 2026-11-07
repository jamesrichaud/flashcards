"""Sync: every device sees the same collection, and real Anki apps can sync too.

1. Between browsers. Every device uses the same server-side collection (one open Collection
   per user in `core.collection`), so two browsers logged in as the same person always read
   and write the same data. A small script on every page asks `/sync/stamp` for the
   collection's modification time when the page comes back into view (switching apps on an
   iPad, switching tabs on a Chromebook, Safari's back/forward cache) and reloads the page if
   another device changed something, unless the person has typed into a form on it.

2. With Anki apps (AnkiDroid, AnkiMobile, desktop Anki). Anki's own sync server
   (`python -m anki.syncserver`, see README) holds a copy of the collection, exactly like
   AnkiWeb. This page makes the web app a sync client of that server (or of AnkiWeb itself)
   through the engine's sync client: `sync_login`, `sync_collection`, `full_upload_or_download`,
   `sync_media`, `media_sync_status`, `sync_status`. The other apps sync to the same server.

The sync login (server address, user name and the engine's hkey; never the password) is
kept in `data/users/<name>/sync.json`, not in the collection, because the collection's config
is itself synced.
"""
import html, json, os, re, threading, time
from urllib.parse import urlsplit

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from anki.errors import SyncError, SyncErrorKind
from anki.sync import SyncAuth
from anki.sync_pb2 import SyncCollectionResponse, SyncStatusResponse

import app as core
from flashcards.i18n import t, tn, N_

router = APIRouter()

AUTO_EVERY = 10 * 60   # automatic sync from the deck list: at most every 10 minutes
FULL = (SyncCollectionResponse.FULL_SYNC, SyncCollectionResponse.FULL_DOWNLOAD, SyncCollectionResponse.FULL_UPLOAD)
_busy = {}   # user -> True while a sync of that user runs (a second tap gets a message)
_busy_lock = threading.Lock()


def clean(s):
    return re.sub(r"[⁨⁩]", "", str(s or "")).strip()


def default_server():
    """Server preset for the login form: the SYNC_SERVER_URL environment setting (the
    docker-compose sync service), else blank, which means AnkiWeb."""
    return os.environ.get("SYNC_SERVER_URL", "").strip()


def normalise_server(url):
    """'' for AnkiWeb, or an http(s) URL ending in '/'. Raises ValueError for anything else."""
    url = (url or "").strip()
    if not url:
        return ""
    p = urlsplit(url)
    if p.scheme not in ("http", "https") or not p.netloc or p.query or p.fragment:
        raise ValueError(t("The server address must look like {example}", example="http://computer:8080/"))
    return url if url.endswith("/") else url + "/"


# ---------- stored login ----------
def conf_path(user):
    return os.path.join(core.DATA, "users", user, "sync.json")


def load_conf(user):
    try:
        with open(conf_path(user)) as f:
            c = json.load(f)
        return c if isinstance(c, dict) else {}
    except (OSError, ValueError):
        return {}


def save_conf(user, conf):
    path = conf_path(user)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(conf, f)
    os.replace(tmp, path)


def update_conf(user, **changes):
    conf = load_conf(user)
    conf.update(changes)
    save_conf(user, conf)
    return conf


def auth_of(conf):
    if not conf.get("hkey"):
        return None
    return SyncAuth(hkey=conf["hkey"], endpoint=conf.get("endpoint") or None)


def server_label(conf_or_url):
    url = conf_or_url.get("endpoint", "") if isinstance(conf_or_url, dict) else conf_or_url
    return url or "AnkiWeb"


# ---------- engine calls (call while holding the user's collection lock) ----------
def error_text(e):
    msg = clean(str(e)) or t("The sync server could not be reached.")
    return msg


def wait_media_idle(col, seconds=10):
    """Stop a running media sync before a full sync closes the collection."""
    try:
        if not col.media_sync_status().active:
            return
        col.abort_media_sync()
    except Exception:  # noqa: BLE001  (a failed earlier media sync throws here; nothing is running)
        return
    end = time.time() + seconds
    while time.time() < end:
        try:
            if not col.media_sync_status().active:
                return
        except Exception:  # noqa: BLE001
            return
        time.sleep(0.1)


def start_media(col, auth, conf):
    if conf.get("media", True):
        try:
            col.sync_media(auth)
        except Exception as e:  # noqa: BLE001
            print(f"media sync could not start: {e}")


def sync_now(user, col, conf):
    """One normal sync. Returns (message, needs_full). needs_full is the engine's required
    full-sync kind (FULL_SYNC / FULL_DOWNLOAD / FULL_UPLOAD) and the server's media usn,
    which the person must choose to do, or None."""
    auth = auth_of(conf)
    try:
        out = col.sync_collection(auth, conf.get("media", True))
    except SyncError as e:
        if e.kind == SyncErrorKind.AUTH:
            update_conf(user, hkey="")
            return "!" + t("{error} Please log in to the sync server again.", error=error_text(e)), None
        update_conf(user, last_error=error_text(e))
        return "!" + error_text(e), None
    except Exception as e:  # noqa: BLE001  (network errors and the like)
        update_conf(user, last_error=error_text(e))
        return "!" + error_text(e), None
    changes = {"last_error": ""}
    if out.new_endpoint:
        changes["endpoint"] = out.new_endpoint   # AnkiWeb moves an account to another host
    note = (" " + clean(out.server_message)) if out.server_message else ""
    if out.required in FULL:
        update_conf(user, **changes)
        return ("!" + note.strip()) if note else "", (int(out.required), int(out.server_media_usn))
    changes["last_sync"] = int(time.time())
    update_conf(user, **changes)
    return clean(col.tr.sync_collection_complete()) + note, None


def full_sync(user, col, conf, upload, server_usn):
    """Replace one side with the other through the engine. A download is preceded by a
    backup of this collection (it is about to be replaced). Returns a flash message."""
    from flashcards import maintenance
    auth = auth_of(conf)
    if not upload and not col.is_empty():
        try:
            maintenance.make_backup(user, col)
        except Exception as e:  # noqa: BLE001
            return "!" + t("Could not back up this collection first, so nothing was downloaded: {error}", error=e)
    wait_media_idle(col)
    col.close_for_full_sync()
    err = ""
    try:
        col.full_upload_or_download(auth=auth, server_usn=server_usn, upload=upload)
    except Exception as e:  # noqa: BLE001
        err = error_text(e)
    finally:
        col.reopen(after_full_sync=True)   # the engine reopens the collection whatever happened
    if err:
        update_conf(user, last_error=err)
        return "!" + err
    if not upload and col.get_config("fsrs", None) is None:
        col.set_config("fsrs", False)   # desktop Anki's meaning of a missing setting: FSRS off
    update_conf(user, last_sync=int(time.time()), last_error="")
    start_media(col, auth, conf)
    if upload:
        return t("Uploaded this collection to the sync server.")
    return tn("Downloaded the collection from the sync server: {n} note.",
              "Downloaded the collection from the sync server: {n} notes.", col.note_count())


def auto_sync(user, col):
    """Called by the deck list (Anki syncs when a profile opens). Normal syncs only, at most
    every AUTO_EVERY seconds, and only if the person turned it on. Returns a flash message
    or ''."""
    conf = load_conf(user)
    if not (conf.get("auto") and conf.get("hkey")):
        return ""
    if time.time() - conf.get("last_try", 0) < AUTO_EVERY:
        return ""
    if not claim(user):
        return ""
    try:
        conf = update_conf(user, last_try=int(time.time()))
        msg, full = sync_now(user, col, conf)
    finally:
        release(user)
    if full:
        return "!" + t("The sync server needs you to choose between uploading and downloading. Open Sync.")
    return msg if msg.startswith("!") else ""


def claim(user):
    with _busy_lock:
        if _busy.get(user):
            return False
        _busy[user] = True
        return True


def release(user):
    with _busy_lock:
        _busy.pop(user, None)


def media_line(col, conf):
    """(text, active) for the media part of the page."""
    if not conf.get("media", True):
        return t("Pictures and sounds are not synced."), False
    try:
        st = col.media_sync_status()
    except Exception as e:  # noqa: BLE001  (the engine throws the last media sync's error here)
        return t("Media sync failed: {error}", error=error_text(e)), False
    p = st.progress
    parts = [clean(x) for x in (p.checked, p.added, p.removed) if x]
    if st.active:
        return t("Syncing pictures and sounds… {progress}", progress=" · ".join(parts)), True
    return (t("Last media sync: {progress}", progress=" · ".join(parts)) if parts else ""), False


# ---------- live refresh between devices ----------
def stamp(user):
    """The collection's modification time, read without the lock (like the theme and undo
    status in page()). Changes on every write, sync and undo."""
    col = core._cols.get(user) if user else None
    if col is None:
        return 0
    try:
        return int(col.mod)
    except Exception:  # noqa: BLE001
        return 0


def live_script(user):
    """Added to every page by core.page. When the page comes back into view and another
    device changed the collection, go to the page again (a GET, so a page that was the
    answer to a form never re-posts). Skipped once something was typed into the page and on
    the editor pages (marked data-noundokey), so nothing typed is lost."""
    return f"""<script>(function(){{let S={stamp(user)},dirty=false,busy=false;
document.addEventListener('input',()=>{{dirty=true;}},true);document.addEventListener('change',()=>{{dirty=true;}},true);
window.fcStampSeen=s=>{{S=s;}};  // a page's own fetch() change (e.g. flagging on the study screen) is not "another device"
async function check(){{if(busy||dirty||document.hidden||document.querySelector('[data-noundokey]'))return;busy=true;
 try{{const r=await fetch('/sync/stamp',{{cache:'no-store'}});if(r.ok){{const j=await r.json();
  if(j.stamp&&j.stamp!==S){{location.replace(window.FC_AFTER_UNDO||(location.pathname+location.search));return;}}}}}}catch(e){{}}
 busy=false;}}
document.addEventListener('visibilitychange',check);window.addEventListener('pageshow',e=>{{if(e.persisted)check();}});
}})();</script>"""


@router.get("/sync/stamp")
def sync_stamp(request: Request):
    user = core.current_user(request)
    if not user: return {"stamp": 0}
    with core.collection(user):
        pass   # opens the collection if this process hasn't yet
    return {"stamp": stamp(user)}


# ---------- pages ----------
SYNC_CSS = """<style>.sync dt{color:var(--muted);font-size:14px;margin-top:10px}.sync dd{margin:2px 0 0}
.chk{display:flex;gap:10px;align-items:center;margin:12px 0;color:var(--ink);font-size:17px}
.chk input{width:24px;height:24px;flex:none}</style>"""


def status_text(col, auth):
    try:
        st = col.sync_status(auth)
    except Exception as e:  # noqa: BLE001
        return t("Could not check: {error}", error=error_text(e))
    # The engine's status is about this side (the server is asked only now and then), so
    # "no changes" does not mean another device has nothing new.
    return {SyncStatusResponse.NO_CHANGES: t("No changes here since the last sync."),
            SyncStatusResponse.NORMAL_SYNC: t("Changes made here are waiting to be synced."),
            SyncStatusResponse.FULL_SYNC: t("A full sync is needed (upload or download).")}.get(st.required, "")


def _e(s):
    """Escape translated text for an HTML text node."""
    return html.escape(s, False)


def fmt_when(ts):
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else t("never")


@router.get("/sync", response_class=HTMLResponse)
def sync_page(request: Request):
    user = core.current_user(request)
    if not user: return core.need_login(request)
    conf = load_conf(user)
    auth = auth_of(conf)
    flash = core.take_flash(request)
    refresh = ""
    devices = f"""<div class="box"><h2 style="margin-top:0">{_e(t("Your devices"))}</h2>
<p>{_e(t("Every iPad, Chromebook or computer that opens this app and logs in as you uses the same cards on this\n"
         "server, so they are always in step: there is nothing to sync between them. A page that was left open\n"
         "updates itself when you come back to it, if another device changed something."))}</p></div>"""
    back = f'<p><a href="/settings" class="btn">{_e(t("← Settings"))}</a></p>'
    heading = f'<h2 style="margin-top:0">{_e(t("Sync with Anki apps"))}</h2>'
    if not auth:
        server = conf.get("endpoint", default_server())
        body = f"""{SYNC_CSS}{back}{devices}
<div class="box sync">{heading}
<p class="muted">{_e(t("To use AnkiDroid, AnkiMobile or desktop Anki as well, log in here and in those apps to the same\n"
                       "sync server: the one that comes with this app (see the README), another self-hosted Anki sync "
                       "server, or AnkiWeb.\nChanges are then merged both ways when you sync, as between Anki apps."))}</p>
<form method="post" action="/sync/login">
<label for="server">{_e(t("Sync server address (leave blank for AnkiWeb)"))}</label>
<input id="server" name="server" value="{html.escape(server)}" placeholder="http://computer:8080/" autocapitalize="none" autocorrect="off" inputmode="url">
<label for="username">{_e(t("User name or AnkiWeb email"))}</label>
<input id="username" name="username" value="{html.escape(conf.get('username', ''))}" autocapitalize="none" autocorrect="off" required>
<label for="password">{_e(t("Password"))}</label><input id="password" name="password" type="password" required>
<p class="muted" style="font-size:14px">{_e(t("Only a sync key from the server is kept, not your password."))}</p>
<p><button class="primary">{_e(t("Log in"))}</button></p></form></div>"""
        return core.page(t("Sync"), body, user, "settings", flash)
    with core.collection(user) as col:
        state = status_text(col, auth)
        media, active = media_line(col, conf)
    if active:
        refresh = '<meta http-equiv="refresh" content="2">'
    err = conf.get("last_error", "")
    last_error = f'<dt>{_e(t("Last error"))}</dt><dd class="danger">{html.escape(err)}</dd>' if err else ''
    media_row = f'<dt>{_e(t("Media"))}</dt><dd id="media-state">{html.escape(media)}</dd>' if media else ''
    body = f"""{SYNC_CSS}{refresh}{back}{devices}
<div class="box sync">{heading}
<dl><dt>{_e(t("Server"))}</dt><dd>{html.escape(server_label(conf))}</dd>
<dt>{_e(t("Logged in as"))}</dt><dd>{html.escape(conf.get('username', ''))}</dd>
<dt>{_e(t("Last sync"))}</dt><dd>{_e(fmt_when(conf.get('last_sync')))}</dd>
<dt>{_e(t("Now"))}</dt><dd id="sync-state">{html.escape(state)}</dd>
{last_error}
{media_row}</dl>
<form method="post" action="/sync/now"><p><button class="primary" style="min-width:160px">{_e(t("Sync now"))}</button></p></form>
<form method="post" action="/sync/options">
<label class="chk"><input type="checkbox" name="auto" value="1"{' checked' if conf.get('auto') else ''}>
{_e(tn("Sync automatically when the deck list opens (at most every {n} minute)",
       "Sync automatically when the deck list opens (at most every {n} minutes)", AUTO_EVERY // 60))}</label>
<label class="chk"><input type="checkbox" name="media" value="1"{' checked' if conf.get('media', True) else ''}>
{_e(t("Sync pictures and sounds too"))}</label>
<button>{_e(t("Save"))}</button></form>
<form method="post" action="/sync/logout" style="margin-top:16px"><button class="danger">{_e(t("Log out of the sync server"))}</button></form></div>"""
    return core.page(t("Sync"), body, user, "settings", flash)


@router.post("/sync/login")
async def sync_login(request: Request):
    user = core.current_user(request)
    if not user: return core.need_login(request)
    form = await request.form()
    username = str(form.get("username", "")).strip()
    password = str(form.get("password", ""))
    try:
        server = normalise_server(str(form.get("server", "")))
    except ValueError as e:
        core.set_flash(request, "!" + str(e))
        return RedirectResponse("/sync", 303)
    if not username or not password:
        core.set_flash(request, "!" + t("Type your sync user name and password."))
        return RedirectResponse("/sync", 303)
    with core.collection(user) as col:
        try:
            auth = col.sync_login(username, password, server or None)
        except Exception as e:  # noqa: BLE001
            update_conf(user, endpoint=server, username=username)
            core.set_flash(request, "!" + error_text(e))
            return RedirectResponse("/sync", 303)
    conf = load_conf(user)
    conf.update(endpoint=auth.endpoint or server, username=username, hkey=auth.hkey, last_error="")
    conf.setdefault("media", True)
    save_conf(user, conf)
    core.set_flash(request, t("Logged in to {server}. Tap Sync now.", server=server_label(conf)))
    return RedirectResponse("/sync", 303)


@router.post("/sync/logout")
def sync_logout(request: Request):
    user = core.current_user(request)
    if not user: return core.need_login(request)
    update_conf(user, hkey="", last_error="")
    request.session.pop("sync_full", None)
    core.set_flash(request, t("Logged out of the sync server. Your cards here are unchanged."))
    return RedirectResponse("/sync", 303)


@router.post("/sync/options")
async def sync_options(request: Request):
    user = core.current_user(request)
    if not user: return core.need_login(request)
    form = await request.form()
    update_conf(user, auto=bool(form.get("auto")), media=bool(form.get("media")))
    core.set_flash(request, t("Saved."))
    return RedirectResponse("/sync", 303)


@router.post("/sync/now")
def sync_now_route(request: Request):
    user = core.current_user(request)
    if not user: return core.need_login(request)
    conf = load_conf(user)
    if not auth_of(conf):
        core.set_flash(request, "!" + t("Log in to a sync server first."))
        return RedirectResponse("/sync", 303)
    if not claim(user):
        core.set_flash(request, "!" + t("A sync is already running. Please wait a moment."))
        return RedirectResponse("/sync", 303)
    try:
        with core.collection(user) as col:
            msg, full = sync_now(user, col, conf)
    finally:
        release(user)
    if full:
        request.session["sync_full"] = list(full)
        if msg:
            core.set_flash(request, msg)
        return RedirectResponse("/sync/full", 303)
    core.set_flash(request, msg)
    return RedirectResponse("/sync", 303)


FULL_TEXT = {
    SyncCollectionResponse.FULL_DOWNLOAD: (N_("This collection has no cards. Download the collection from the sync server?"),
                                           ("download",)),
    SyncCollectionResponse.FULL_UPLOAD: (N_("The sync server has no cards. Replace it with this collection?"),
                                         ("upload",)),
    SyncCollectionResponse.FULL_SYNC: (N_("The cards here and on the sync server differ in a way that can't be merged "
                                          "(for example a note type was changed on one side), so one side has to replace "
                                          "the other."), ("download", "upload")),
}


@router.get("/sync/full", response_class=HTMLResponse)
def sync_full_page(request: Request):
    user = core.current_user(request)
    if not user: return core.need_login(request)
    pending = request.session.get("sync_full")
    if not pending or not auth_of(load_conf(user)):
        return RedirectResponse("/sync", 303)
    required = pending[0]
    text, choices = FULL_TEXT.get(required, FULL_TEXT[SyncCollectionResponse.FULL_SYNC])
    opts = []
    if "download" in choices:
        opts.append(f"""<div class="box"><h3 style="margin-top:0">{_e(t("Download"))}</h3><p>{_e(t(
            "Replace the cards here with the ones\non the sync server. Changes made here since the last sync are lost "
            "(a backup of this collection is made first)."))}</p>
<form method="post" action="/sync/full"><input type="hidden" name="choice" value="download">
<label class="chk"><input type="checkbox" name="confirm" value="1" required>{_e(t("Replace the cards here"))}</label>
<button class="primary">{_e(t("Download from the server"))}</button></form></div>""")
    if "upload" in choices:
        opts.append(f"""<div class="box"><h3 style="margin-top:0">{_e(t("Upload"))}</h3><p>{_e(t(
            "Replace the cards on the sync server\nwith the ones here. Changes waiting on the server are lost, "
            "and other Anki apps will need to download."))}</p>
<form method="post" action="/sync/full"><input type="hidden" name="choice" value="upload">
<label class="chk"><input type="checkbox" name="confirm" value="1" required>{_e(t("Replace the cards on the server"))}</label>
<button class="primary">{_e(t("Upload to the server"))}</button></form></div>""")
    body = f"""{SYNC_CSS}<div class="box"><h2 style="margin-top:0">{_e(t("Full sync needed"))}</h2><p>{html.escape(t(text))}</p>
<p class="muted">{_e(t("After this, reviews and new cards from every device are merged again as usual."))}</p></div>
{''.join(opts)}<p><a class="btn" href="/sync">{_e(t("Cancel"))}</a></p>"""
    return core.page(t("Full sync"), body, user, "settings", core.take_flash(request))


@router.post("/sync/full")
async def sync_full(request: Request):
    user = core.current_user(request)
    if not user: return core.need_login(request)
    form = await request.form()
    pending = request.session.get("sync_full")
    conf = load_conf(user)
    if not pending or not auth_of(conf):
        return RedirectResponse("/sync", 303)
    choice = form.get("choice")
    _, allowed = FULL_TEXT.get(pending[0], FULL_TEXT[SyncCollectionResponse.FULL_SYNC])
    if choice not in allowed:
        return RedirectResponse("/sync/full", 303)
    if not form.get("confirm"):
        core.set_flash(request, "!" + t("Tick the box to confirm which side is replaced."))
        return RedirectResponse("/sync/full", 303)
    if not claim(user):
        core.set_flash(request, "!" + t("A sync is already running. Please wait a moment."))
        return RedirectResponse("/sync/full", 303)
    try:
        with core.collection(user) as col:
            msg = full_sync(user, col, conf, choice == "upload", pending[1])
    finally:
        release(user)
    request.session.pop("sync_full", None)
    core.set_flash(request, msg)
    return RedirectResponse("/sync", 303)
