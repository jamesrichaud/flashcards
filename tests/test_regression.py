"""Full regression: build a collection with every kind of content, then visit every page the app
links to (a crawler starting from the deck list) and check each one:
  - loads (200, HTML), no server error text, a full document with the viewport tag
  - no duplicate element ids, every <label for> points at an element
  - every in-app link and image source works (no 404/500)
  - every inline script parses (node --check, using Playwright's bundled node if present)
Screenshots at iPad and Chromebook sizes are in tests/shots_all.py (needs Chromium)."""
import glob, html, json, os, re, shutil, struct, subprocess, sys, tempfile, zlib
from urllib.parse import urljoin, urlsplit
os.environ["DATA_DIR"] = tempfile.mkdtemp()
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from fastapi.testclient import TestClient
import app as webapp
from flashcards import image_occlusion as io

ok = lambda m: print("PASS", m)
c = TestClient(webapp.app)


def png(w, h):
    raw = b"".join(b"\x00" + b"\x20\x80\xc0" * w for _ in range(h))
    def chunk(t, b): return struct.pack(">I", len(b)) + t + b + struct.pack(">I", zlib.crc32(t + b))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


# ---------- the logged-out pages ----------
for url in ("/login", "/offline"):
    r = c.get(url, headers={"Accept": "text/html"})
    assert r.status_code == 200 and "<html" in r.text, url
r = c.get("/no-such-page", headers={"Accept": "text/html"})
assert r.status_code == 404 and "<html" in r.text and "Back" in r.text
ok("login, offline and 404 pages load while logged out")

# ---------- a realistic collection ----------
c.post("/login", data={"username": "regress", "password": "secret123"})
with webapp.collection("regress") as col:
    col.media.write_data("pic.png", png(40, 20))
    bio = col.decks.id("Biology")
    cells = col.decks.id("Biology::Cells")
    lang = col.decks.id("French")
    basic = col.models.by_name("Basic")
    for i in range(6):
        n = col.new_note(basic); n["Front"] = f"Question {i} <img src=\"pic.png\">"; n["Back"] = f"Answer {i}"
        n.tags = ["regress"] + (["marked"] if i == 0 else [])
        col.add_note(n, cells if i % 2 else bio)
    n = col.new_note(col.models.by_name("Cloze")); n["Text"] = "{{c1::DNA}} has {{c2::two}} strands"
    col.add_note(n, bio)
    n = col.new_note(col.models.by_name("Basic (type in the answer)")); n["Front"] = "chien"; n["Back"] = "dog"
    col.add_note(n, lang)
    n = col.new_note(col.models.by_name("Basic (and reversed card)")); n["Front"] = "maison"; n["Back"] = "house"
    col.add_note(n, lang)
    io_nt = io.io_notetype(col)
    col.decks.set_current(bio)
    cids = list(col.find_cards(f"did:{bio}"))
    col.sched.suspend_cards(cids[-1:])
    col.set_user_flag_for_cards(1, cids[:1])
shapes = [{"shape": "rect", "ordinal": 1, "left": .1, "top": .2, "width": .3, "height": .4},
          {"shape": "ellipse", "ordinal": 2, "left": .5, "top": .5, "rx": .1, "ry": .2}]
r = c.post("/io/new", data={"ntid": io_nt["id"], "did": bio, "mode": "all", "header": "Heart", "back_extra": "",
                            "tags": "", "shapes": json.dumps(shapes)},
           files={"image": ("heart.png", png(200, 100), "image/png")}, headers={"X-Requested-With": "fetch"})
assert r.json()["ok"], r.text
# some study history so stats, card info and undo have something to show
for _ in range(5):
    r = c.get(f"/study/{bio}")
    m = re.search(r'name="card_id" value="(\d+)"', r.text)
    if not m:
        break
    c.post(f"/study/{bio}/answer", data={"card_id": m.group(1), "rating": 3, "ms": 3000})
c.post("/filtered/new", data={"name": "Filtered Regress", "search": "tag:regress", "limit": "100", "order": "0",
                              "reschedule": "on"})
with webapp.collection("regress") as col:
    assert col.find_cards("") and col.db.scalar("select count() from revlog") >= 1
    nids = list(col.find_notes(""))
    io_nid = col.find_notes(f"mid:{io_nt['id']}")[0]
    ntids = [m["id"] for m in col.models.all()]
    dids = [d.id for d in col.decks.all_names_and_ids()]
ok(f"collection built: {len(nids)} notes (basic, cloze, type-in, reversed, image occlusion), reviews, filtered deck")

# ---------- crawl ----------
SEEDS = ["/", "/browse", "/browse?search=deck:*&mode=notes", "/settings", "/stats", "/stats?range=3",
         "/add", "/notetypes", "/notetypes/change", "/import", "/export", "/preferences", "/sync", "/addons",
         "/backups", "/check-media", "/check-database", "/empty-cards", "/io/new", "/filtered/new",
         f"/edit/{nids[0]}", f"/io/{io_nid}"]
SEEDS += [f"/deck/{d}" for d in dids] + [f"/study/{d}" for d in dids] + [f"/deck/{d}/options" for d in dids]
SEEDS += [f"/notetypes/{m}/fields" for m in ntids] + [f"/notetypes/{m}/cards" for m in ntids]
# GET links that change state, download files, or aren't pages
SKIP = re.compile(r"^/(logout|media/|backups/file/|addon/|sync/stamp|undo/status|sw\.js|manifest\.json|"
                  r"favicon|apple-touch-icon|icon-\d+)|/cancel$|/export$|^/export\?|^/deck/\d+/export")
ASSET = re.compile(r"^/(media/|apple-touch-icon|icon-\d+|favicon|manifest\.json)")


def path_of(base, href):
    href = html.unescape(href.strip())
    if not href or href.startswith(("#", "javascript:", "mailto:", "data:", "blob:")):
        return None
    u = urlsplit(urljoin("http://testserver" + base, href))
    if u.netloc != "testserver":
        return None
    return u.path + ("?" + u.query if u.query else "")


def strip_scripts(text):
    return re.sub(r"<(script|template|style)\b.*?</\1>", "", text, flags=re.S | re.I)


def shape(url):
    """Pages that differ only in ids or query values (Browse sort/page links, every card's info page)
    are the same page; visit a few of each, not all."""
    u = urlsplit(url)
    keys = sorted({q.split("=")[0] for q in u.query.split("&") if q})
    return re.sub(r"\d+", "N", u.path) + "?" + "&".join(keys)


problems, scripts, assets = [], {}, set()
seen, queue, per_shape, visited = set(), list(SEEDS), {}, []
while queue:
    url = queue.pop(0)
    if url in seen:
        continue
    seen.add(url)
    if url not in SEEDS:
        per_shape[shape(url)] = per_shape.get(shape(url), 0) + 1
        if per_shape[shape(url)] > 3:
            continue
    assert len(seen) < 2000, "crawler runaway"
    visited.append(url)
    r = c.get(url, headers={"Accept": "text/html"})
    if r.status_code != 200:
        problems.append(f"{url}: status {r.status_code}")
        continue
    if "text/html" not in r.headers.get("content-type", ""):
        continue
    t = r.text
    for bad in ("Traceback (most recent call last)", "Internal Server Error"):
        if bad in t:
            problems.append(f"{url}: shows '{bad}'")
    if not t.lstrip().lower().startswith("<!doctype html") or "</html>" not in t:
        problems.append(f"{url}: not a full HTML document")
    if 'name="viewport"' not in t:
        problems.append(f"{url}: no viewport tag")
    body = strip_scripts(t)
    ids = re.findall(r'\sid="([^"]+)"', body)
    dups = sorted({i for i in ids if ids.count(i) > 1})
    if dups:
        problems.append(f"{url}: duplicate ids {dups}")
    for f in set(re.findall(r'<label[^>]*\sfor="([^"]+)"', body)):
        if f not in ids:
            problems.append(f"{url}: <label for={f!r}> has no element")
    for m in re.finditer(r"<script(?![^>]*\bsrc=)([^>]*)>(.*?)</script>", t, re.S | re.I):
        attrs, code = m.group(1), m.group(2)
        if "json" in attrs or "text/template" in attrs or not code.strip():
            continue
        scripts.setdefault(code, url)
    for href in re.findall(r'<a\b[^>]*\shref="([^"]*)"', body):
        p = path_of(url, href)
        if p is None:
            continue
        if ASSET.match(p):
            assets.add(p)
        elif not SKIP.search(p) and p not in seen:
            queue.append(p)
    for src in re.findall(r'<(?:img|link)\b[^>]*\s(?:src|href)="([^"]*)"', body):
        p = path_of(url, src)
        if p and not p.startswith("/addon/"):
            assets.add(p)

for a in sorted(assets):
    r = c.get(a)
    if r.status_code != 200:
        problems.append(f"asset {a}: status {r.status_code}")
assert len(visited) >= 60, f"crawler only reached {len(visited)} pages"
assert not problems, "\n".join(problems)
ok(f"{len(visited)} pages ({len(per_shape) + len(SEEDS)} kinds) crawled: all load, full documents, unique ids, labels match, links and {len(assets)} assets work")

# ---------- inline scripts parse ----------
node = shutil.which("node") or next(iter(glob.glob(os.path.join(
    os.path.dirname(__import__("playwright").__file__) if __import__("importlib").util.find_spec("playwright")
    else "/nonexistent", "driver", "node*"))), None)
if node:
    d = tempfile.mkdtemp()
    bad = []
    for i, (code, url) in enumerate(scripts.items()):
        f = os.path.join(d, f"s{i}.js")
        with open(f, "w") as fh:
            fh.write(code)
        r = subprocess.run([node, "--check", f], capture_output=True, text=True)
        if r.returncode:
            bad.append(f"{url}: {r.stderr.strip().splitlines()[-1] if r.stderr.strip() else 'syntax error'}")
    assert not bad, "\n".join(bad)
    ok(f"{len(scripts)} distinct inline scripts parse (node --check)")
else:
    print("SKIP inline script syntax check (no node found)")

# ---------- dark mode and touch basics on every page ----------
r = c.get("/")
assert "prefers-color-scheme: dark" in r.text or "prefers-color-scheme:dark" in r.text
assert "safe-area-inset" in r.text and "touch-action" in r.text
ok("shared CSS has dark mode, safe areas and touch-action")
print("ALL REGRESSION TESTS PASSED")
