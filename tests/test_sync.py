"""Sync: two browsers as the same user share one collection, and the web app syncs with
Anki's own sync server (`python -m anki.syncserver`) exactly like AnkiDroid/desktop do. A
second collection plays the part of a phone, syncing through the engine's sync client."""
import html, json, os, re, socket, stat, subprocess, sys, tempfile, time
os.environ["DATA_DIR"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fastapi.testclient import TestClient
from anki.collection import Collection
from anki.scheduler.v3 import CardAnswer
import app as webapp
from flashcards import sync as sm

ok = lambda m: print("PASS", m)


def flash(r):
    m = re.search(r'<div class="flash( err)?">(.*?)</div>', r.text, re.S)
    return ("!" if m and m.group(1) else "") + html.unescape(m.group(2)) if m else ""


def add_note(client, did, front, back):
    time.sleep(0.01)   # collection times are in ms: a change in the same ms as a sync would look unsynced
    with webapp.collection("friend") as col:
        basic = col.models.by_name("Basic")["id"]
    r = client.post("/add", data={"ntid": basic, "did": did, "f0": front, "f1": back})
    assert "Added 1 card" in r.text, r.text[-500:]


def answer_top(col, did, rating=CardAnswer.GOOD):
    col.decks.select(did)
    q = col.sched.get_queued_cards()
    card = col.get_card(q.cards[0].card.id)
    card.start_timer()
    col.sched.answer_card(col.sched.build_answer(card=card, states=q.cards[0].states, rating=rating))
    return card.id


# ---------- 1. two browsers, same user ----------
anon = TestClient(webapp.app)
for path in ("/sync", "/sync/full"):
    r = anon.get(path, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login", path
for path in ("/sync/login", "/sync/now", "/sync/full", "/sync/logout", "/sync/options"):
    r = anon.post(path, data={}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login", path
assert anon.get("/sync/stamp").json() == {"stamp": 0}
ok("sync pages need a login; the stamp says nothing to strangers")

ipad, chromebook = TestClient(webapp.app), TestClient(webapp.app)
ipad.post("/login", data={"username": "friend", "password": "secret123"})
r = chromebook.post("/login", data={"username": "friend", "password": "secret123"})
assert "Log out" in r.text
ipad.post("/deck/new", data={"name": "Spanish"})
with webapp.collection("friend") as col:
    did = col.decks.id_for_name("Spanish")
assert "Spanish" in chromebook.get("/").text
add_note(ipad, did, "perro", "dog")
add_note(chromebook, did, "gato", "cat")
for client in (ipad, chromebook):
    r = client.get("/browse", params={"q": "deck:Spanish"})
    assert "perro" in r.text and "gato" in r.text
ok("a deck and notes added on one device show up on the other at once")

s1 = ipad.get("/sync/stamp").json()["stamp"]
assert s1 and s1 == chromebook.get("/sync/stamp").json()["stamp"]
r = ipad.get(f"/study/{did}")
with webapp.collection("friend") as col:
    col.decks.select(did)
    top = col.sched.get_queued_cards().cards[0].card.id
assert str(top) in chromebook.get(f"/study/{did}").text and str(top) in r.text
ok("both devices show the same card at the top of the study queue")

ipad.post(f"/study/{did}/answer", data={"card_id": top, "rating": 3})
s2 = chromebook.get("/sync/stamp").json()["stamp"]
assert s2 != s1, "answering changes the stamp the other device checks"
chromebook.post(f"/study/{did}/answer", data={"card_id": top, "rating": 1})   # stale page on the other device
with webapp.collection("friend") as col:
    assert col.get_card(top).reps == 1, "the second device's stale answer must not answer the card again"
    col.decks.select(did)
    nxt = col.sched.get_queued_cards().cards[0].card.id
assert nxt != top and str(nxt) in chromebook.get(f"/study/{did}").text
ok("after one device answers, the other's stale answer is ignored and it gets the next card")

r = chromebook.get("/")
assert "/sync/stamp" in r.text and "visibilitychange" in r.text and f"let S={s2}," in r.text
assert "data-noundokey" in r.text and "dirty" in r.text
ok("every page carries the live-refresh check with the current stamp")

ipad.post("/undo", data={"back": "/"})
s3 = chromebook.get("/sync/stamp").json()["stamp"]
assert s3 != s2
with webapp.collection("friend") as col:
    assert col.get_card(top).reps == 0
ok("an undo on one device is seen by the other (stamp changes)")

time.sleep(0.01)
j = chromebook.post(f"/card/{nxt}/action", data={"action": "flag", "arg": "1"},
                    headers={"X-Requested-With": "fetch"}).json()
assert j["ok"] and j["stamp"] == ipad.get("/sync/stamp").json()["stamp"] != s3
assert "fcStampSeen(r.stamp)" in chromebook.get(f"/study/{did}").text
with webapp.collection("friend") as col:
    assert col.get_card(nxt).user_flag() == 1
ok("a page's own flag/mark change hands it the new stamp, so it doesn't reload itself")

# ---------- 2. links ----------
assert 'href="/sync"' in ipad.get("/settings").text and 'href="/sync"' in ipad.get("/").text
r = ipad.get("/sync")
assert "Your devices" in r.text and 'action="/sync/login"' in r.text and "AnkiWeb" in r.text
ok("Settings and the deck list link to Sync; logged out it shows the login form")

# ---------- 3. Anki's sync server ----------
base = tempfile.mkdtemp()
s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
env = dict(os.environ, SYNC_BASE=os.path.join(base, "server"), SYNC_HOST="127.0.0.1", SYNC_PORT=str(port),
           SYNC_USER1="friend:syncpw", SYNC_USER2="other:otherpw", RUST_LOG="error")
server = subprocess.Popen([sys.executable, "-m", "anki.syncserver"], env=env,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
URL = f"http://127.0.0.1:{port}/"


def wait_port():
    for _ in range(200):
        try:
            socket.create_connection(("127.0.0.1", port), 0.2).close()
            return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError("sync server did not start")


def media_idle(col):
    for _ in range(200):
        try:
            if not col.media_sync_status().active:
                return
        except Exception:
            return
        time.sleep(0.05)
    raise RuntimeError("media sync did not finish")


phone = None


def psync(media=False):
    time.sleep(0.01)
    out = phone.sync_collection(pauth, media)
    time.sleep(0.01)
    return out

try:
    wait_port()
    r = ipad.post("/sync/login", data={"server": "ftp://x", "username": "friend", "password": "syncpw"})
    assert flash(r).startswith("!The server address")
    r = ipad.post("/sync/login", data={"server": URL, "username": "friend", "password": "wrong"})
    assert flash(r).startswith("!") and "incorrect" in flash(r)
    assert not sm.load_conf("friend").get("hkey")
    assert f'value="{URL}"' in r.text, "the typed server is kept for the next try"
    ok("a bad address or password is refused with the engine's message")

    r = ipad.post("/sync/login", data={"server": URL.rstrip("/"), "username": "friend", "password": "syncpw"})
    assert "Logged in to" in flash(r) and "Sync now" in r.text
    conf = sm.load_conf("friend")
    assert conf["hkey"] and conf["endpoint"] == URL and "syncpw" not in open(sm.conf_path("friend")).read()
    assert stat.S_IMODE(os.stat(sm.conf_path("friend")).st_mode) == 0o600
    with webapp.collection("friend") as col:
        assert "syncpw" not in json.dumps(col.all_config()) and conf["hkey"] not in json.dumps(col.all_config())
    assert URL in r.text and "A full sync is needed" in r.text
    ok("login keeps only the server's key (file readable by the app only, not in the synced collection)")

    with webapp.collection("friend") as col:
        col.media.write_data("perro.png", b"\x89PNG fake")
        n = col.get_note(col.find_notes("perro")[0]); n["Back"] = 'dog <img src="perro.png">'; col.update_note(n)
    r = ipad.post("/sync/now")
    assert str(r.url).endswith("/sync/full") and "The sync server has no cards" in r.text
    assert "Upload to the server" in r.text and "Download from the server" not in r.text
    r = ipad.post("/sync/full", data={"choice": "download", "confirm": "1"})
    assert str(r.url).endswith("/sync/full")
    r = ipad.post("/sync/full", data={"choice": "upload"})
    assert flash(r).startswith("!Tick the box")
    r = ipad.post("/sync/full", data={"choice": "upload", "confirm": "1"})
    assert flash(r) == "Uploaded this collection to the sync server.", flash(r)
    with webapp.collection("friend") as col:
        media_idle(col)
        assert col.note_count() == 2
    r = ipad.get("/sync")
    assert "No changes here since the last sync." in r.text and "never" not in r.text
    ok("first sync: the empty server needs a confirmed upload, then the page says up to date")

    # The "phone": a separate collection syncing with the engine's client, like AnkiDroid.
    phone = Collection(os.path.join(base, "phone.anki2"))
    pauth = phone.sync_login("friend", "syncpw", URL)
    out = psync(True)
    assert out.required == out.FULL_DOWNLOAD
    phone.close_for_full_sync()
    phone.full_upload_or_download(auth=pauth, server_usn=out.server_media_usn, upload=False)
    phone.reopen(after_full_sync=True)
    phone.sync_media(pauth); media_idle(phone)
    assert phone.note_count() == 2 and phone.media.have("perro.png")
    ok("a second Anki client downloads the cards and pictures the web app uploaded")

    pdid = phone.decks.id_for_name("Spanish")
    pn = phone.new_note(phone.models.by_name("Basic")); pn["Front"] = "pájaro"; pn["Back"] = "bird"
    phone.add_note(pn, pdid)
    answered = answer_top(phone, pdid)
    phone.media.write_data("bird.mp3", b"ID3 fake")
    assert psync().required == 0
    phone.sync_media(pauth); media_idle(phone)
    r = ipad.post("/sync/now")
    assert flash(r) == "Collection sync complete.", flash(r)
    with webapp.collection("friend") as col:
        media_idle(col)
        assert col.find_notes("pájaro") and col.get_card(answered).reps == 1
        assert col.media.have("bird.mp3")
    assert "pájaro" in chromebook.get("/browse", params={"q": "deck:Spanish"}).text
    ok("a note, a review and a sound from the phone arrive with Sync now, on every browser")

    add_note(chromebook, did, "caballo", "horse")
    assert "Changes made here are waiting to be synced." in chromebook.get("/sync").text
    r = chromebook.post("/sync/now")
    assert flash(r) == "Collection sync complete."
    assert psync().required == 0
    assert phone.find_notes("caballo")
    ok("a note added in the browser reaches the phone")

    # ---------- automatic sync from the deck list ----------
    pn = phone.new_note(phone.models.by_name("Basic")); pn["Front"] = "vaca"; pn["Back"] = "cow"
    phone.add_note(pn, pdid)
    assert psync().required == 0
    ipad.get("/")
    with webapp.collection("friend") as col:
        assert not col.find_notes("vaca"), "auto sync is off by default"
    r = ipad.post("/sync/options", data={"auto": "1", "media": "1"})
    assert 'name="auto" value="1" checked' in r.text
    r = ipad.get("/")
    with webapp.collection("friend") as col:
        assert col.find_notes("vaca"), (flash(r), sm.load_conf("friend"))
    assert not flash(r), flash(r)
    pn = phone.new_note(phone.models.by_name("Basic")); pn["Front"] = "oveja"; pn["Back"] = "sheep"
    phone.add_note(pn, pdid); psync()
    ipad.get("/")
    with webapp.collection("friend") as col:
        assert not col.find_notes("oveja"), f"at most every {sm.AUTO_EVERY // 60} minutes"
    sm.update_conf("friend", last_try=0)
    ipad.get("/")
    with webapp.collection("friend") as col:
        assert col.find_notes("oveja")
    ok("automatic sync when the deck list opens: off by default, then at most every 10 minutes")

    # ---------- conflict: a schema change on the phone ----------
    m = phone.models.by_name("Basic")
    phone.models.add_field(m, phone.models.new_field("Extra"))
    phone.models.update_dict(m)
    out = psync()
    assert out.required == out.FULL_SYNC
    phone.close_for_full_sync()
    phone.full_upload_or_download(auth=pauth, server_usn=out.server_media_usn, upload=True)
    phone.reopen(after_full_sync=True)
    add_note(ipad, did, "lost", "this note is lost by the download")
    sm.update_conf("friend", last_try=0)
    r = ipad.get("/")
    assert flash(r).startswith("!The sync server needs you to choose")
    with webapp.collection("friend") as col:
        assert col.find_notes("lost"), "auto sync never does a full sync by itself"
    r = ipad.post("/sync/now")
    assert str(r.url).endswith("/sync/full") and "be merged" in r.text, (r.url, flash(r))
    assert "Upload to the server" in r.text and "Download from the server" in r.text
    from flashcards import maintenance
    before = len(maintenance.list_backups("friend"))
    r = ipad.post("/sync/full", data={"choice": "download", "confirm": "1"})
    assert flash(r).startswith("Downloaded the collection from the sync server"), flash(r)
    assert len(maintenance.list_backups("friend")) == before + 1, "a backup is made before a download"
    with webapp.collection("friend") as col:
        assert not col.find_notes("lost") and "Extra" in col.models.field_names(col.models.by_name("Basic"))
        assert col.note_count() == phone.note_count()
    r = chromebook.get("/browse", params={"q": "deck:Spanish"})
    assert "oveja" in r.text and "lost" not in r.text
    assert ipad.get("/sync/full", follow_redirects=False).status_code == 303
    ok("a schema change on the phone needs a confirmed full sync; download replaces the web copy after a backup")

    # The web collection still works normally after the full download.
    r = ipad.get(f"/study/{did}")
    assert "Show answer" in r.text or "Show Answer" in r.text
    add_note(ipad, did, "after", "full sync")
    assert ipad.post("/sync/now").url.path == "/sync"
    assert psync().required == 0 and phone.find_notes("after")
    ok("after the download the web app studies, adds and syncs as before")

    # ---------- errors ----------
    sm.update_conf("friend", hkey="not-a-real-key")
    r = ipad.post("/sync/now")
    assert flash(r).startswith("!") and "log in to the sync server again" in flash(r)
    assert 'action="/sync/login"' in r.text
    r = ipad.post("/sync/login", data={"server": URL, "username": "friend", "password": "syncpw"})
    assert "Logged in" in flash(r)
    ok("a rejected key logs out of the sync server and asks for the password again")
finally:
    server.terminate()
    server.wait(10)

r = ipad.post("/sync/now")
assert flash(r).startswith("!")
assert "Last error" in ipad.get("/sync").text
with webapp.collection("friend") as col:
    assert col.note_count() > 0
assert "Spanish" in ipad.get("/").text
ok("with the server down, Sync now shows the error and the app keeps working")

r = ipad.post("/sync/logout")
assert "Logged out of the sync server" in flash(r) and 'action="/sync/login"' in r.text
assert not sm.load_conf("friend").get("hkey") and sm.load_conf("friend")["username"] == "friend"
r = ipad.post("/sync/now")
assert flash(r) == "!Log in to a sync server first."
ok("logging out of sync keeps the cards and remembers the user name")

assert sm.normalise_server("") == "" and sm.normalise_server(" https://a.b ") == "https://a.b/"
for bad in ("a.b", "javascript:x", "http://", "http://a/?q=1"):
    try:
        sm.normalise_server(bad); raise AssertionError(bad)
    except ValueError:
        pass
ok("server addresses: blank is AnkiWeb, http(s) only, trailing slash added")

if phone:
    phone.close()
print("ALL SYNC TESTS PASSED")
