"""Study reminders: VAPID keys, subscription storage, settings page, the scheduler's timing and
due-card check, and sending (with the push service mocked)."""
import datetime as dt, os, re, shutil, subprocess, sys, tempfile, glob
from zoneinfo import ZoneInfo
os.environ["DATA_DIR"] = tempfile.mkdtemp()
os.environ["REMINDERS"] = "0"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fastapi.testclient import TestClient
import app as webapp
from flashcards import reminders

ok = lambda m: print("PASS", m)
DATA = os.environ["DATA_DIR"]
c = TestClient(webapp.app)
c.post("/login", data={"username": "remi", "password": "secret123"})

sent = []
status_for = {}
def fake_send(sub, payload):
    sent.append((sub["endpoint"], payload))
    return status_for.get(sub["endpoint"], 201)
real_send = reminders.send_push
reminders.send_push = fake_send

SUB = {"endpoint": "https://fcm.googleapis.com/fcm/send/abc", "expirationTime": None,
       "keys": {"p256dh": "BNcRdreALRFXTkOOUHK1EtK2wtaz5Ry4YfYCA_0QTpQtUbVlUls0VJXg7A8u-Ts1XbjhazAkj7I99e8QcYP7DkM",
                "auth": "tBHItJI5svbpez7KI4CCXg"}}
SUB2 = {"endpoint": "https://web.push.apple.com/QGx", "keys": {"p256dh": "BOther", "auth": "a2"}}

# ---------- VAPID keys ----------
priv, pub = reminders.vapid_keys()
assert os.path.dirname(priv) == DATA and oct(os.stat(priv).st_mode & 0o777) == "0o600"
assert re.fullmatch(r"[A-Za-z0-9_-]{87}", pub)        # 65-byte uncompressed P-256 point, base64url
assert reminders.vapid_keys() == (priv, pub)            # made once, then reused
from py_vapid import Vapid
assert Vapid.from_file(priv).public_key is not None     # pywebpush can read it
ok("VAPID keys are made once in the data folder (private key 0600) and readable by pywebpush")

# ---------- page and settings link ----------
r = c.get("/settings")
assert 'href="/reminders"' in r.text and "Study reminders" in r.text
r = c.get("/reminders")
assert r.status_code == 200
for s in ("Turn on notifications here", "Send a test notification", "HTTPS", "Add to Home Screen", "iPadOS 16.4",
          'type="time"', 'value="19:00"', "No devices yet.", pub, "pushManager.subscribe", "Right now: 0 cards due"):
    assert s in r.text, s
assert r.text.count('name="days"') == 7 and r.text.count('name="days" value="') == 7
assert TestClient(webapp.app).get("/reminders", follow_redirects=False).status_code == 303
ok("Settings links to the Reminders page, which explains HTTPS and the iPad Home Screen")

r = c.get("/sw.js")
assert "addEventListener('push'" in r.text and "showNotification" in r.text and "notificationclick" in r.text
assert "'/offline'" in r.text
ok("the service worker shows pushes and opens the app when one is tapped")

# ---------- subscriptions ----------
anon = TestClient(webapp.app)
assert anon.post("/reminders/subscribe", json={"subscription": SUB}).status_code == 401
for bad in ({}, {"subscription": {"endpoint": "http://x", "keys": SUB["keys"]}},
            {"subscription": {"endpoint": SUB["endpoint"]}}, {"subscription": "x"}):
    assert c.post("/reminders/subscribe", json=bad).status_code == 400, bad
assert c.post("/reminders/subscribe", content=b"nope", headers={"Content-Type": "application/json"}).status_code == 400
r = c.post("/reminders/subscribe", json={"subscription": SUB, "tz": "Europe/Dublin"},
           headers={"User-Agent": "Mozilla/5.0 (X11; CrOS x86_64 14541.0.0) Chrome/120.0 Safari/537.36"})
assert r.status_code == 200 and r.json()["ok"]
c.post("/reminders/subscribe", json={"subscription": SUB, "tz": "Europe/Dublin"},   # same device again
       headers={"User-Agent": "Mozilla/5.0 (X11; CrOS x86_64 14541.0.0) Chrome/121.0 Safari/537.36"})
c.post("/reminders/subscribe", json={"subscription": SUB2, "tz": "Not/AZone"},
       headers={"User-Agent": "Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) Safari/604.1"})
conf = reminders.load("remi")
assert [s["endpoint"] for s in conf["subscriptions"]] == [SUB["endpoint"], SUB2["endpoint"]]
assert conf["subscriptions"][0]["origin"] == "https://testserver" and conf["tz"] == "Europe/Dublin"
assert os.path.exists(os.path.join(DATA, "users", "remi", "reminders.json"))
r = c.get("/reminders")
assert "Chromebook, Chrome" in r.text and "iPad, Safari" in r.text and r.text.count(">Remove</button>") == 2
ok("subscriptions are stored per person and per device (no duplicates), with the time zone and origin")

# ---------- settings ----------
r = c.post("/reminders/settings", data={"on": "1", "time": "08:30", "days": ["0", "2", "4"], "tz": "America/New_York"},
           follow_redirects=False)
assert r.status_code == 303
conf = reminders.load("remi")
assert conf["on"] and conf["time"] == "08:30" and conf["days"] == [0, 2, 4] and conf["tz"] == "America/New_York"
assert "Saved." in c.get("/reminders").text
for bad in ({"time": "25:00"}, {"time": "x"}, {"time": "08:00", "days": ["9"]}, {"time": "08:00", "days": ["a"]}):
    assert "Please choose a time" in c.post("/reminders/settings", data=bad).text, bad
    assert reminders.load("remi")["time"] == "08:30"
c.post("/reminders/settings", data={"on": "1", "time": "08:30", "days": ["0", "2", "4"], "tz": "Evil/../zone"})
assert reminders.load("remi")["tz"] == "America/New_York"   # bad zone is ignored
assert "no days are ticked" in c.post("/reminders/settings", data={"on": "1", "time": "08:30", "tz": "UTC"}).text
c.post("/reminders/settings", data={"on": "1", "time": "19:00", "days": [str(i) for i in range(7)], "tz": "Europe/Dublin"})
r = c.get("/reminders")
assert 'value="19:00"' in r.text and r.text.count(" checked") == 8
ok("time, days, on/off and time zone are saved; bad values are refused")

# damaged file falls back to defaults, keeping valid parts
with open(reminders.conf_path("remi")) as f:
    good = f.read()
open(reminders.conf_path("remi"), "w").write('{"on": "yes", "time": "99:99", "days": [1, 9, true], "tz": "<x>", '
                                             '"subscriptions": [{"endpoint": "ftp://x"}, 3]}')
d = reminders.load("remi")
assert d["on"] is False and d["time"] == "19:00" and d["days"] == [1] and d["tz"] == "UTC" and d["subscriptions"] == []
open(reminders.conf_path("remi"), "w").write("not json")
assert reminders.load("remi")["subscriptions"] == []
open(reminders.conf_path("remi"), "w").write(good)
ok("a damaged reminders file falls back to safe defaults")

# ---------- timing ----------
tz = ZoneInfo("Europe/Dublin")
def at(y, mo, d, h, mi):
    return dt.datetime(y, mo, d, h, mi, tzinfo=tz).timestamp()
conf = reminders.load("remi")   # on, 19:00 every day, Europe/Dublin, two devices
assert not reminders.is_due_now(conf, at(2026, 11, 9, 18, 59))
assert reminders.is_due_now(conf, at(2026, 11, 9, 19, 0))
assert reminders.is_due_now(conf, at(2026, 11, 9, 21, 59))
assert not reminders.is_due_now(conf, at(2026, 11, 9, 22, 0))         # too late: 3 hours after
assert reminders.is_due_now(conf, at(2026, 7, 9, 19, 5))               # summer time is local too
conf2 = dict(conf, days=[0])                                          # 9 Nov 2026 is a Monday
assert reminders.is_due_now(conf2, at(2026, 11, 9, 19, 0)) and not reminders.is_due_now(conf2, at(2026, 11, 10, 19, 0))
assert not reminders.is_due_now(dict(conf, last_sent="2026-11-09"), at(2026, 11, 9, 19, 30))
assert not reminders.is_due_now(dict(conf, on=False), at(2026, 11, 9, 19, 30))
assert not reminders.is_due_now(dict(conf, subscriptions=[]), at(2026, 11, 9, 19, 30))
ny = dict(conf, tz="America/New_York")
assert not reminders.is_due_now(ny, at(2026, 11, 9, 19, 0)) and reminders.is_due_now(ny, at(2026, 11, 10, 0, 30))
ok("the reminder is due from the chosen local time for 3 hours, on chosen days, once a day")

# ---------- nothing due: no push, but the day is done ----------
now = at(2026, 11, 9, 19, 1)
assert reminders.check_user("remi", now) is None and sent == []
assert reminders.load("remi")["last_sent"] == "2026-11-09"
ok("no notification when no cards are due")

# ---------- cards due: one push to every device, once ----------
with webapp.collection("remi") as col:
    for i in range(3):
        n = col.new_note(col.models.by_name("Basic")); n["Front"] = f"q{i}"; n["Back"] = "a"
        col.add_note(n, 1)
    assert reminders.due_counts(col) == (3, 0, 0)
    n = col.new_note(col.models.by_name("Basic")); n["Front"] = "rev"; n["Back"] = "a"; col.add_note(n, 1)
    col.sched.set_due_date([n.cards()[0].id], "0")                     # one review due today
    assert reminders.due_counts(col) == (3, 0, 1)
assert reminders.message((0, 0, 0)) is None
m = reminders.message((3, 0, 31))
assert m["title"] == "You have 34 cards due" and m["body"].startswith("31 reviews, 3 new")
assert reminders.message((0, 1, 0))["title"] == "You have 1 card due"
assert "Right now: 4 cards due" in c.get("/reminders").text

now = at(2026, 11, 10, 19, 0)
p = reminders.check_user("remi", now)
assert p and p["title"] == "You have 4 cards due" and p["body"] == "1 review, 3 new. Tap to study." and p["url"] == "/"
assert [e for e, _ in sent] == [SUB["endpoint"], SUB2["endpoint"]] and sent[0][1]["tag"] == "fc-reminder"
assert reminders.check_user("remi", now + 60) is None and len(sent) == 2
ok("cards due: 'You have 4 cards due' goes to every device once that day")

# tick() runs every person, and one broken person doesn't stop the others
sent.clear()
webapp.create_user("other", "secret123")
c2 = TestClient(webapp.app); c2.post("/login", data={"username": "other", "password": "secret123"})
reminders.update("other", lambda cf: cf.update(on=True, time="07:00", days=list(range(7)), tz="Europe/Dublin",
                                               subscriptions=[dict(SUB, endpoint="https://push.example/o")]))
with webapp.collection("other") as col:
    n = col.new_note(col.models.by_name("Basic")); n["Front"] = "x"; n["Back"] = "y"; col.add_note(n, 1)
real_check = reminders.check_user
def flaky(user, now=None):
    if user == "remi":
        raise RuntimeError("boom")
    return real_check(user, now)
reminders.check_user = flaky
reminders.tick(at(2026, 11, 11, 7, 30))
reminders.check_user = real_check
assert [e for e, _ in sent] == ["https://push.example/o"]
ok("the scheduler checks every person; an error for one person doesn't stop the others")

# ---------- expired subscriptions are dropped ----------
sent.clear()
status_for[SUB2["endpoint"]] = 410
p = reminders.check_user("remi", at(2026, 11, 11, 20, 0))
assert p and len(sent) == 2
assert [s["endpoint"] for s in reminders.load("remi")["subscriptions"]] == [SUB["endpoint"]]
status_for.clear()
ok("a device the push service says is gone (410) is removed")

# ---------- test notification ----------
sent.clear()
r = c.post("/reminders/test", json={"endpoint": SUB["endpoint"]})
assert r.json()["sent"] == 1 and sent[0][1]["title"] == "Test: You have 4 cards due" and sent[0][1]["tag"] == "fc-test"
r = c.post("/reminders/test", json={"endpoint": "https://unknown.example/x"})
assert r.json()["sent"] == 0 and "Turn on notifications" in r.json()["error"]
status_for[SUB["endpoint"]] = 500
r = c.post("/reminders/test", json={})
assert r.json()["sent"] == 0 and r.json()["failed"] == 1 and r.json()["error"]
assert len(reminders.load("remi")["subscriptions"]) == 1    # 500 is not "gone"
status_for.clear()
def boom(sub, payload): raise OSError("network down")
reminders.send_push = boom
assert c.post("/reminders/test", json={}).json()["sent"] == 0
reminders.send_push = fake_send
with webapp.collection("other") as col:
    col.remove_notes(col.find_notes(""))
sent.clear()
r = c2.post("/reminders/test", json={})
assert r.json()["sent"] == 1 and sent[0][1]["title"] == "Test: No cards due"
assert anon.post("/reminders/test", json={}).status_code == 401
ok("Send a test notification goes to this device (mocked push service); failures are reported")

# ---------- removing devices ----------
assert "Device removed." in c.post("/reminders/remove", data={"endpoint": SUB["endpoint"]}).text
assert reminders.load("remi")["subscriptions"] == []
r = c2.post("/reminders/unsubscribe", json={"endpoint": "https://push.example/o"})
assert r.json()["ok"] and reminders.load("other")["subscriptions"] == []
assert c2.post("/reminders/unsubscribe", json={}).status_code == 400
ok("devices can be removed from the list or turned off on the device")

# ---------- real pywebpush encryption with the stored key (network mocked) ----------
import requests
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
import base64
k = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(
    serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
b64 = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=").decode()
real_sub = {"endpoint": "https://fcm.googleapis.com/fcm/send/real", "keys": {"p256dh": b64(k), "auth": b64(os.urandom(16))},
            "origin": "https://flashcards.example"}
posted = []
class FakeResp:
    status_code = 201; text = ""; headers = {}
def fake_post(url, data=None, headers=None, timeout=None, **kw):
    posted.append((url, data, headers)); return FakeResp()
real_post = requests.post
requests.post = fake_post
try:
    assert real_send(real_sub, {"title": "t"}) == 201
finally:
    requests.post = real_post
url, body, headers = posted[0]
assert url == real_sub["endpoint"] and headers["content-encoding"] == "aes128gcm" and len(body) > 16
assert headers["authorization"].startswith("vapid t=") and f"k={pub}" in headers["authorization"]
ok("the real sender encrypts the payload and signs it with the stored VAPID key")

# ---------- the page script is valid JavaScript ----------
node = (glob.glob(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".venv/lib/python3*/site-packages/playwright/driver/node"))
        or [shutil.which("node")])[0]
if node:
    page = c.get("/reminders").text
    for i, script in enumerate(re.findall(r"<script>(.*?)</script>", page, re.S)):
        f = os.path.join(tempfile.mkdtemp(), f"s{i}.js"); open(f, "w").write(script)
        subprocess.run([node, "--check", f], check=True)
    f = os.path.join(tempfile.mkdtemp(), "sw.js"); open(f, "w").write(c.get("/sw.js").text)
    subprocess.run([node, "--check", f], check=True)
    ok("page and service worker scripts pass node --check")
else:
    print("SKIP node not found")

# the scheduler thread isn't started when REMINDERS=0, and starts once otherwise
reminders.start(); assert reminders._thread is None
print("all reminder tests passed")
