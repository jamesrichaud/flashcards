"""Import from link: fetch a shared deck (.apkg) on the server, then run the normal import.

Accepts a direct link to an .apkg/.colpkg file, or an AnkiWeb shared-deck page
(https://ankiweb.net/shared/info/<id>), which is turned into AnkiWeb's download address.

Safety (the server fetches what a person types, so it must not be usable to reach the
server's own network):
- only http and https;
- the host name is resolved once and every address must be public (no loopback, private,
  link-local, carrier-grade NAT, multicast or reserved ranges, IPv4 or IPv6); the connection is
  then made to that checked address, so DNS can't switch to a private one in between; every
  redirect hop is checked the same way (at most 5);
- at most MAX_BYTES (300 MB), checked against Content-Length and again while reading;
- a socket timeout per read and an overall time limit.

The download runs in a background thread; /import/url/<job> shows a progress bar that polls
/import/url/<job>/status. After the download the file goes through the same engine call as an
uploaded file (import_export.import_package), or to the .colpkg confirm page.
"""
import http.client, ipaddress, os, re, secrets, socket, ssl, threading, time
from urllib.parse import urlsplit, urljoin, unquote

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse

import app as core
from flashcards import import_export as ie
from flashcards.i18n import t, use_lang, current

router = APIRouter()

MAX_BYTES = 300 * 1024 * 1024
READ_TIMEOUT = 30          # seconds per connect/read
TOTAL_TIMEOUT = 20 * 60    # seconds for the whole download
MAX_REDIRECTS = 5
CHUNK = 1 << 16
USER_AGENT = "Flashcards (self-hosted Anki web app)"
SHARED_DECKS_URL = "https://ankiweb.net/shared/decks"
ANKIWEB_INFO = re.compile(r"^https?://(?:www\.)?ankiweb\.net/shared/(?:info|download|downloadDeck)/(\d+)", re.I)
JOB_MAX_AGE = 3600

# job id -> dict(user, state, received, total, name, error, token, log_msg, started, opts)
_jobs = {}
_jobs_lock = threading.Lock()


class FetchError(Exception):
    """A problem to show the person (already translated)."""


# ---------- URL checks ----------
def download_url(url):
    """The address to fetch for what the person typed. AnkiWeb shared-deck pages become AnkiWeb's
    download address. Raises FetchError for anything that isn't an http(s) link."""
    url = (url or "").strip()
    m = ANKIWEB_INFO.match(url)
    if m:
        return f"https://ankiweb.net/shared/downloadDeck/{m.group(1)}"
    parts = urlsplit(url)
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        raise FetchError(t("Please paste a link that starts with http:// or https://."))
    return url


_NAT64 = ipaddress.ip_network("64:ff9b::/96")


def address_allowed(ip):
    """True only for public internet addresses."""
    a = ipaddress.ip_address(ip.split("%", 1)[0])
    if isinstance(a, ipaddress.IPv6Address):
        # IPv6 forms that carry an IPv4 address: judge the IPv4 address inside
        if a.ipv4_mapped:
            a = a.ipv4_mapped
        elif a.sixtofour:
            a = a.sixtofour
        elif a in _NAT64:
            a = ipaddress.IPv4Address(int(a) & 0xFFFFFFFF)
    return a.is_global and not (a.is_multicast or a.is_reserved or a.is_loopback or a.is_private
                                or a.is_link_local or a.is_unspecified)


def resolve(host, port):
    """One public address for host, or FetchError. Every address the name resolves to must be public."""
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError):
        raise FetchError(t("Could not find the server {host}.", host=host))
    addrs = [i[4][0] for i in infos]
    if not addrs or not all(address_allowed(a) for a in addrs):
        raise FetchError(t("That link points to a local or private address, which is not allowed."))
    return addrs[0]


class _PinnedHTTP(http.client.HTTPConnection):
    def __init__(self, host, port, ip, timeout):
        super().__init__(host, port, timeout=timeout)
        self._ip = ip

    def connect(self):
        self.sock = socket.create_connection((self._ip, self.port), self.timeout)


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, port, ip, timeout):
        super().__init__(host, port, timeout=timeout, context=ssl.create_default_context())
        self._ip = ip

    def connect(self):
        sock = socket.create_connection((self._ip, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)  # certificate checked for host


def _open(url):
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https") or not parts.hostname:
        raise FetchError(t("Please paste a link that starts with http:// or https://."))
    try:
        port = parts.port or (443 if scheme == "https" else 80)
    except ValueError:
        raise FetchError(t("Please paste a link that starts with http:// or https://."))
    ip = resolve(parts.hostname, port)
    cls = _PinnedHTTPS if scheme == "https" else _PinnedHTTP
    conn = cls(parts.hostname, port, ip, READ_TIMEOUT)
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    conn.request("GET", path, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    return conn, conn.getresponse()


def file_name(url, resp):
    cd = resp.getheader("Content-Disposition") or ""
    m = re.search(r"filename\*\s*=\s*[^']*''([^;]+)", cd) or re.search(r'filename\s*=\s*"?([^";]+)"?', cd)
    name = unquote(m.group(1)) if m else unquote(urlsplit(url).path.rsplit("/", 1)[-1])
    name = os.path.basename(name.replace("\\", "/")).strip() or "shared deck.apkg"
    if not name.lower().endswith((".apkg", ".colpkg")):
        name += ".apkg"
    return name


def fetch(url, dest, progress=lambda received, total: None, max_bytes=None, deadline=None):
    """Download url into dest. Returns the file name to show. Raises FetchError."""
    max_bytes = MAX_BYTES if max_bytes is None else max_bytes
    deadline = deadline or time.time() + TOTAL_TIMEOUT
    limit_mb = max_bytes // (1024 * 1024)
    for _ in range(MAX_REDIRECTS + 1):
        try:
            conn, resp = _open(url)
        except FetchError:
            raise
        except (OSError, http.client.HTTPException) as e:
            raise FetchError(t("Could not download the file: {error}", error=e))
        try:
            if resp.status in (301, 302, 303, 307, 308):
                loc = resp.getheader("Location")
                if not loc:
                    raise FetchError(t("The server sent a redirect without an address."))
                url = urljoin(url, loc)
                continue
            if resp.status != 200:
                raise FetchError(t("The server answered {status} {reason}.", status=resp.status, reason=resp.reason))
            try:
                total = int(resp.getheader("Content-Length") or 0)
            except ValueError:
                total = 0
            if total > max_bytes:
                raise FetchError(t("The file is bigger than {mb} MB.", mb=limit_mb))
            name = file_name(url, resp)
            received = 0
            with open(dest, "wb") as fh:
                while True:
                    if time.time() > deadline:
                        raise FetchError(t("The download took too long and was stopped."))
                    try:
                        chunk = resp.read(CHUNK)
                    except (OSError, http.client.HTTPException) as e:
                        raise FetchError(t("Could not download the file: {error}", error=e))
                    if not chunk:
                        break
                    received += len(chunk)
                    if received > max_bytes:
                        raise FetchError(t("The file is bigger than {mb} MB.", mb=limit_mb))
                    fh.write(chunk)
                    progress(received, total)
            with open(dest, "rb") as fh:
                if fh.read(4) != b"PK\x03\x04":  # .apkg and .colpkg are zip files
                    if ANKIWEB_INFO.match(url) or "ankiweb.net" in (urlsplit(url).hostname or ""):
                        raise FetchError(t("AnkiWeb did not hand the deck to the server (it may need you to sign in). "
                                           "Download the deck in your browser, then choose the file above."))
                    raise FetchError(t("That link is not an Anki deck file (.apkg)."))
            return name
        finally:
            conn.close()
    raise FetchError(t("Too many redirects."))


# ---------- background jobs ----------
def _clean_jobs():
    now = time.time()
    with _jobs_lock:
        for jid in [j for j, job in _jobs.items() if now - job["started"] > JOB_MAX_AGE and job["state"] in ("done", "error")]:
            _jobs.pop(jid, None)


def _run(jid, url, lang):
    job = _jobs[jid]
    with use_lang(lang):  # messages made in the thread use the person's language
        os.makedirs(ie.UPLOAD_DIR, exist_ok=True)
        token = secrets.token_hex(12)
        tmp = os.path.join(ie.UPLOAD_DIR, token + ".part")

        def progress(received, total):
            job["received"], job["total"] = received, total
        try:
            name = fetch(url, tmp, progress)
            ext = ".colpkg" if name.lower().endswith(".colpkg") else ".apkg"
            path = os.path.join(ie.UPLOAD_DIR, token + ext)
            os.replace(tmp, path)
            ie._uploads[token] = {"user": job["user"], "path": path, "name": name}
            job["name"] = name
            if ext == ".colpkg":  # replaces everything: the person confirms on the usual page
                job["go"] = f"/import/colpkg/{token}"
                job["state"] = "done"
                return
            job["state"] = "importing"
            ok, msg = ie.import_package(job["user"], path, name, job["opts"])
            ie.drop_upload(token)
            job["message"] = msg if ok else "!" + msg
            job["go"] = "/" if ok else "/import"
            job["state"] = "done"
        except FetchError as e:
            job["error"], job["state"] = str(e), "error"
        except Exception as e:  # never leave the progress page spinning
            job["error"], job["state"] = t("Could not download the file: {error}", error=e), "error"
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)


def start(user, url, opts):
    """Start a download+import job; returns its id. Raises FetchError for a bad link."""
    _clean_jobs()
    target = download_url(url)
    jid = secrets.token_hex(10)
    with _jobs_lock:
        _jobs[jid] = {"user": user, "state": "downloading", "received": 0, "total": 0, "name": "",
                      "error": "", "message": "", "go": "", "started": time.time(), "opts": opts, "url": url}
    threading.Thread(target=_run, args=(jid, target, current()), daemon=True).start()
    return jid


def get_job(user, jid):
    job = _jobs.get(jid)
    return job if job and job["user"] == user else None


def mb(n):
    return f"{n / (1024 * 1024):.1f}"


def status_text(job):
    if job["state"] == "downloading":
        if job["total"]:
            return t("Downloading… {got} of {total} MB", got=mb(job["received"]), total=mb(job["total"]))
        return t("Downloading… {got} MB", got=mb(job["received"]))
    if job["state"] == "importing":
        return t("Importing {name}…", name=job["name"])
    if job["state"] == "error":
        return job["error"]
    return t("Done.")


def finish(request, job):
    """Hand the result to the person's next page (flash message) and say where to go."""
    if job["message"]:
        core.set_flash(request, job["message"])
        job["message"] = ""
    return job["go"] or "/import"


# ---------- pages ----------
def form_html(tr, presets):
    """The "Import from link" box on the Import page."""
    return f"""<div class="box" id="fromlink"><h2 style="margin-top:0">{ie.esc(t("Import from link"))}</h2>
<p>{ie.esc(t("Paste a link to an .apkg file, or to a deck's page on AnkiWeb. The server downloads it (up to 300 MB) and imports it."))}</p>
<form method="post" action="/import/url"><input type="hidden" name="opts" value="1">
<input type="url" name="url" required inputmode="url" autocapitalize="none" autocorrect="off" spellcheck="false"
 placeholder="https://ankiweb.net/shared/info/…" aria-label="{core.html.escape(t("Link"))}">
{ie.package_options_html(tr, presets)}
<p><button class="primary">{ie.esc(t("Download and import"))}</button></p></form>
<div class="row" style="justify-content:space-between;margin-top:6px"><span class="muted" style="font-size:15px">{ie.esc(t("Or find a deck on AnkiWeb: download it there, then use Import."))}</span>
<a class="btn" href="{SHARED_DECKS_URL}" target="_blank" rel="noopener">{ie.esc(t("Open AnkiWeb shared decks"))}</a></div></div>"""


@router.post("/import/url")
async def import_url(request: Request):
    user = core.current_user(request)
    if not user: return core.need_login(request)
    form = await request.form()
    with core.collection(user) as col:
        opts = ie.package_options(form, col._backend.get_import_anki_package_presets())
    try:
        jid = start(user, form.get("url", ""), opts)
    except FetchError as e:
        core.set_flash(request, "!" + str(e))
        return RedirectResponse("/import#fromlink", 303)
    return RedirectResponse(f"/import/url/{jid}", 303)


PROGRESS_JS = """<script>
(function(){var box=document.getElementById('dl'),bar=document.getElementById('dlbar'),txt=document.getElementById('dltext');
function poll(){fetch(box.dataset.status,{credentials:'same-origin'}).then(function(r){return r.json()}).then(function(s){
txt.textContent=s.text;if(s.total){bar.max=s.total;bar.value=s.received}else if(s.state!=='downloading'){bar.removeAttribute('value')}
if(s.state==='error'){box.classList.add('failed');document.getElementById('dlback').style.display='';return}
if(s.go){location.href=s.go;return}setTimeout(poll,700)}).catch(function(){setTimeout(poll,2000)})}
setTimeout(poll,300);})();
</script>"""


@router.get("/import/url/{jid}", response_class=HTMLResponse)
def progress_page(request: Request, jid: str):
    user = core.current_user(request)
    if not user: return core.need_login(request)
    job = get_job(user, jid)
    if not job:
        core.set_flash(request, "!" + t("That download is no longer running. Please paste the link again."))
        return RedirectResponse("/import", 303)
    if job["state"] == "done":
        return RedirectResponse(finish(request, job), 303)
    failed = job["state"] == "error"
    value = f' value="{job["received"]}" max="{job["total"]}"' if job["total"] else ""
    link = core.html.escape(job["url"])
    refresh = "" if failed else '<noscript><meta http-equiv="refresh" content="2"></noscript>'
    body = f"""{refresh}<div class="box" id="dl" data-status="/import/url/{jid}/status">
<h2 style="margin-top:0">{ie.esc(t("Import from link"))}</h2>
<p class="muted" style="word-break:break-all;font-size:15px">{link}</p>
<progress id="dlbar" style="width:100%;height:22px"{value}></progress>
<p id="dltext" role="status" aria-live="polite"{' class="flash err"' if failed else ''}>{ie.esc(status_text(job))}</p>
<p id="dlback"{'' if failed else ' style="display:none"'}><a class="btn" href="/import">{ie.esc(t("← Back"))}</a></p></div>
{'' if failed else PROGRESS_JS}"""
    return core.page(t("Import from link"), body, user, "import")


@router.get("/import/url/{jid}/status")
def progress_status(request: Request, jid: str):
    user = core.current_user(request)
    if not user: return JSONResponse({"error": "login"}, 401)
    job = get_job(user, jid)
    if not job:
        return JSONResponse({"state": "error", "text": t("That download is no longer running. Please paste the link again.")}, 404)
    out = {"state": job["state"], "received": job["received"], "total": job["total"], "text": status_text(job)}
    if job["state"] == "done":
        out["go"] = finish(request, job)
    return JSONResponse(out)
