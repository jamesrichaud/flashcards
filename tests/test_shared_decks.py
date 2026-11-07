"""Import from link: URL checks (no local/private addresses), size limit, redirects, AnkiWeb links,
and a full download + import through the progress page, against a local test server."""
import html, os, re, sys, tempfile, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
os.environ["DATA_DIR"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fastapi.testclient import TestClient
import app as webapp
from anki.collection import Collection, ExportAnkiPackageOptions
from flashcards import shared_decks as sd

ok = lambda m: print("PASS", m)
c = TestClient(webapp.app)
c.post("/login", data={"username": "tester", "password": "secret123"})
tmp = tempfile.mkdtemp()


def flash(r):
    m = re.search(r'<div class="flash( err)?">(.*?)</div>', r.text, re.S)
    return ("!" if m and m.group(1) else "") + html.unescape(m.group(2)) if m else ""


# ---------- address and URL checks ----------
for ip in ["127.0.0.1", "10.1.2.3", "172.16.0.1", "192.168.1.1", "169.254.169.254", "100.64.0.1", "0.0.0.0",
           "224.0.0.1", "255.255.255.255", "::1", "::", "fe80::1", "fc00::1", "::ffff:127.0.0.1",
           "::ffff:192.168.0.1", "2002:c0a8:0101::1", "64:ff9b::a00:1", "fe80::1%eth0"]:
    assert not sd.address_allowed(ip), ip
for ip in ["8.8.8.8", "1.1.1.1", "2606:4700:4700::1111", "::ffff:8.8.8.8"]:
    assert sd.address_allowed(ip), ip
ok("only public addresses are allowed (loopback, private, link-local, CGNAT, multicast, mapped IPv6 refused)")

for bad in ["ftp://example.com/deck.apkg", "file:///etc/passwd", "javascript:alert(1)", "example.com/deck.apkg", "", "http://"]:
    try:
        sd.download_url(bad)
        raise AssertionError(bad)
    except sd.FetchError:
        pass
assert sd.download_url("https://ankiweb.net/shared/info/1104981491") == "https://ankiweb.net/shared/downloadDeck/1104981491"
assert sd.download_url("https://ankiweb.net/shared/info/123?x=1") == "https://ankiweb.net/shared/downloadDeck/123"
assert sd.download_url(" https://example.com/a.apkg ") == "https://example.com/a.apkg"
ok("only http/https links; AnkiWeb shared-deck pages become AnkiWeb's download address")

for host in ["127.0.0.1", "localhost", "2130706433", "0x7f.1", "[::1]"]:
    try:
        sd.fetch(f"http://{host}:9/x.apkg", os.path.join(tmp, "x"))
        raise AssertionError(host)
    except sd.FetchError as e:
        assert "local or private" in str(e) or "Could not find" in str(e), (host, e)
ok("links to this machine are refused before connecting, however the address is written")

# ---------- a local test server ----------
src = Collection(os.path.join(tmp, "src.anki2"))
d = src.decks.id("Linked Deck")
for q, a in [("uno", "one"), ("dos", "two"), ("tres", "three")]:
    n = src.new_note(src.models.by_name("Basic")); n["Front"], n["Back"] = q, a; src.add_note(n, d)
apkg = os.path.join(tmp, "Spanish Numbers.apkg")
src.export_anki_package(out_path=apkg, options=ExportAnkiPackageOptions(
    with_scheduling=False, with_deck_configs=False, with_media=True, legacy=False), limit=None)
src.close()
APKG = open(apkg, "rb").read()
hits = []


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, status, body=b"", headers=()):
        self.send_response(status)
        for k, v in headers:
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        hits.append(self.path)
        p = self.path
        if p == "/deck.apkg":
            self.send(200, APKG)
        elif p == "/download?id=7":
            self.send(200, APKG, [("Content-Disposition", 'attachment; filename="Spanish Numbers.apkg"')])
        elif p == "/slow.apkg":  # no Content-Length, sent in pieces
            self.send_response(200); self.end_headers()
            for i in range(0, len(APKG), 4096):
                self.wfile.write(APKG[i:i + 4096]); self.wfile.flush(); time.sleep(0.01)
        elif p == "/redirect":
            self.send(302, headers=[("Location", "/deck.apkg")])
        elif p == "/loop":
            self.send(302, headers=[("Location", "/loop")])
        elif p == "/to-private":
            self.send(302, headers=[("Location", "http://10.0.0.1/deck.apkg")])
        elif p == "/page.html":
            self.send(200, b"<html>not a deck</html>", [("Content-Type", "text/html")])
        elif p == "/huge.apkg":
            self.send_response(200); self.send_header("Content-Length", str(400 * 1024 * 1024)); self.end_headers()
        elif p == "/col.colpkg":
            self.send(200, APKG)
        else:
            self.send(404, b"no")


srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{srv.server_address[1]}"
real_allowed = sd.address_allowed
sd.address_allowed = lambda ip: ip == "127.0.0.1" or real_allowed(ip)  # let the tests reach the local server


def fetch(path, **kw):
    return sd.fetch(BASE + path, os.path.join(tmp, "dl"), **kw)


def fails(path, words, **kw):
    try:
        fetch(path, **kw)
    except sd.FetchError as e:
        assert words in str(e), (path, str(e))
        return
    raise AssertionError(path + " should fail")


got = []
assert fetch("/deck.apkg", progress=lambda r, t: got.append((r, t))) == "deck.apkg"
assert open(os.path.join(tmp, "dl"), "rb").read() == APKG and got[-1] == (len(APKG), len(APKG))
assert fetch("/download?id=7") == "Spanish Numbers.apkg"
assert fetch("/redirect") == "deck.apkg"
assert fetch("/slow.apkg") == "slow.apkg"
ok("downloads a deck, with progress, the name from Content-Disposition or the link, and follows redirects")

fails("/loop", "Too many redirects")
n = len(hits); fails("/to-private", "local or private"); assert len(hits) == n + 1
fails("/page.html", "not an Anki deck file")
fails("/huge.apkg", "bigger than 300 MB")
fails("/slow.apkg", "bigger than 0 MB", max_bytes=10000)
fails("/deck.apkg", "took too long", deadline=time.time() - 1)
fails("/missing.apkg", "404")
ok("refuses redirect loops, redirects to private addresses, non-deck pages, files over the limit, slow downloads, errors")


# ---------- the Import page and the whole flow ----------
r = c.get("/import")
for s in ['action="/import/url"', 'name="url"', "Import from link", "Download and import",
          'href="https://ankiweb.net/shared/decks" target="_blank"', "download it there, then use Import"]:
    assert s in r.text, s
ok("Import page has the link box and an 'Open AnkiWeb shared decks' button (new tab)")


def run(url, data=None):
    r = c.post("/import/url", data={"url": url, **(data or {})}, follow_redirects=False)
    assert r.status_code == 303, r.status_code
    loc = r.headers["location"]
    if not loc.startswith("/import/url/"):
        return loc, None
    jid = loc.rsplit("/", 1)[1]
    # Don't follow redirects: if the job already finished, the page redirects to "/" and following
    # it would use up the flash message the caller checks next.
    page = c.get(loc, follow_redirects=False)
    if page.status_code == 303:
        assert page.headers["location"] in ("/", "/import") or page.headers["location"].startswith("/import/colpkg/")
        s = c.get(f"/import/url/{jid}/status").json()
        assert s["state"] == "done" and s["go"] == page.headers["location"], s
        return s, jid
    assert 'data-status="/import/url/' in page.text, page.text[:300]
    for _ in range(200):
        s = c.get(f"/import/url/{jid}/status").json()
        if s["state"] == "error" or s.get("go"):
            return s, jid
        time.sleep(0.05)
    raise AssertionError("job never finished")


s, jid = run(BASE + "/deck.apkg")
assert s["state"] == "done" and s["go"] == "/", s
with webapp.collection("tester") as col:
    assert len(col.find_notes('"deck:Linked Deck"')) == 3
assert "Imported 3 new notes from deck.apkg" in flash(c.get("/"))
assert "Last import: deck.apkg" in c.get("/import").text
ok("a link is downloaded in the background and imported with the engine; flash and import log as for an upload")

s, jid = run(BASE + "/deck.apkg", {"opts": "1", "update_notes": "1"})
assert s["state"] == "done"
assert flash(c.get("/")).startswith("Imported 0 new notes from deck.apkg"), flash(c.get("/"))
r = c.get(f"/import/url/{jid}", follow_redirects=False)
assert r.status_code == 303
ok("the options sent with the link are used; a finished job's page redirects onwards")

s, jid = run(BASE + "/page.html")
assert s["state"] == "error" and "not an Anki deck file" in s["text"]
r = c.get(f"/import/url/{jid}")
assert "not an Anki deck file" in r.text and 'class="flash err"' in r.text and 'href="/import"' in r.text
ok("a failed download shows the reason and a way back")

s, jid = run(BASE + "/col.colpkg")
assert s["go"].startswith("/import/colpkg/"), s
assert "Replace your whole collection?" in c.get(s["go"]).text
c.get(s["go"] + "/cancel")
ok("a .colpkg link goes to the usual 'replace everything' confirm page")

loc, _ = run("ftp://example.com/x.apkg")
assert loc.startswith("/import") and "http://" in flash(c.get("/import"))
sd.address_allowed = real_allowed
s, jid = run(BASE + "/deck.apkg")
assert s["state"] == "error" and "local or private" in s["text"]
ok("bad links and links to this server's own network are refused with a clear message")

webapp.create_user("other", "secret123")
c2 = TestClient(webapp.app)
c2.post("/login", data={"username": "other", "password": "secret123"})
assert c2.get(f"/import/url/{jid}/status").status_code == 404
assert c2.get(f"/import/url/{jid}", follow_redirects=False).headers["location"] == "/import"
assert TestClient(webapp.app).get(f"/import/url/{jid}/status").status_code == 401
ok("a download job is only visible to the person who started it")

srv.shutdown()
print("ALL SHARED DECK TESTS PASSED")
