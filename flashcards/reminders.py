"""Study reminders: a daily Web Push notification at a chosen time, per person, only when cards
are due ("You have 34 cards due").

- VAPID keys are made once and kept in `<DATA>/vapid_private.pem` (0600) and `<DATA>/vapid_public.txt`.
- Each person's settings and their devices' push subscriptions are in
  `<DATA>/users/<name>/reminders.json`: {"on", "time": "HH:MM", "days": [0..6, Monday = 0],
  "tz": "Europe/Dublin", "last_sent": "YYYY-MM-DD", "subscriptions": [{endpoint, keys, origin, ...}]}.
  Subscriptions belong to one browser on one device, so they live on the server, not in the
  collection (they would mean nothing to desktop Anki or another server after a sync).
- A background thread (started with the app) checks every 30 seconds. A reminder goes out once
  per local day, on a chosen weekday, from the chosen time until 3 hours after it (so a server
  restart doesn't lose the day's reminder, but a server that was off all day doesn't send a stale
  one at night). Nothing is sent when no cards are due; the day still counts as done.
- Push needs HTTPS (or localhost). On iPad it only works in the app added to the Home Screen
  (iPadOS 16.4+). The page explains this.
"""
import base64, datetime as dt, html, json, logging, os, re, threading, time
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

import app as core
from flashcards import i18n
from flashcards.i18n import t, tn, N_

router = APIRouter()
log = logging.getLogger("flashcards.reminders")

DAY_NAMES = (N_("Mon"), N_("Tue"), N_("Wed"), N_("Thu"), N_("Fri"), N_("Sat"), N_("Sun"))
DEFAULT = {"on": False, "time": "19:00", "days": [0, 1, 2, 3, 4, 5, 6], "tz": "UTC",
           "last_sent": "", "subscriptions": []}
WINDOW = 3 * 3600          # how late a missed reminder may still be sent
TICK = 30                  # seconds between scheduler checks
MAX_DEVICES = 20
TIME_RE = re.compile(r"([01]\d|2[0-3]):[0-5]\d")

_lock = threading.Lock()   # guards the reminders.json files and the VAPID key creation


# ---------- VAPID keys ----------
def _b64(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def key_paths():
    return os.path.join(core.DATA, "vapid_private.pem"), os.path.join(core.DATA, "vapid_public.txt")


def vapid_keys():
    """(path to the private key PEM, public key as base64url), made on first use."""
    priv, pub = key_paths()
    with _lock:
        if not (os.path.exists(priv) and os.path.exists(pub)):
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric import ec
            key = ec.generate_private_key(ec.SECP256R1())
            pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption())
            raw = key.public_key().public_bytes(serialization.Encoding.X962,
                                                serialization.PublicFormat.UncompressedPoint)
            fd = os.open(priv, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(pem)
            with open(pub, "w") as f:
                f.write(_b64(raw))
        return priv, open(pub).read().strip()


# ---------- per-person settings ----------
def conf_path(user):
    return os.path.join(core.DATA, "users", user, "reminders.json")


def load(user):
    out = json.loads(json.dumps(DEFAULT))
    try:
        raw = json.load(open(conf_path(user)))
    except (OSError, ValueError):
        return out
    if not isinstance(raw, dict):
        return out
    out["on"] = raw.get("on") is True
    if isinstance(raw.get("time"), str) and TIME_RE.fullmatch(raw["time"]):
        out["time"] = raw["time"]
    if isinstance(raw.get("days"), list):
        out["days"] = sorted({d for d in raw["days"] if isinstance(d, int) and not isinstance(d, bool) and 0 <= d <= 6})
    if isinstance(raw.get("tz"), str) and valid_tz(raw["tz"]):
        out["tz"] = raw["tz"]
    if isinstance(raw.get("last_sent"), str):
        out["last_sent"] = raw["last_sent"]
    if isinstance(raw.get("subscriptions"), list):
        out["subscriptions"] = [s for s in raw["subscriptions"] if valid_subscription(s)]
    return out


def save(user, conf):
    path = conf_path(user)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(conf, f)
    os.replace(tmp, path)


def update(user, fn):
    """Load, change with fn(conf), save; under the lock so the scheduler and pages don't clash."""
    with _lock:
        conf = load(user)
        fn(conf)
        save(user, conf)
        return conf


def valid_tz(name):
    if not isinstance(name, str) or not name or len(name) > 64 or not re.fullmatch(r"[A-Za-z0-9_+\-/]+", name):
        return False
    try:
        ZoneInfo(name)
        return True
    except Exception:  # noqa: BLE001
        return False


def valid_subscription(s):
    if not isinstance(s, dict):
        return False
    ep, keys = s.get("endpoint"), s.get("keys")
    return (isinstance(ep, str) and ep.startswith("https://") and len(ep) < 2048 and isinstance(keys, dict)
            and all(isinstance(keys.get(k), str) and 0 < len(keys[k]) < 256 for k in ("p256dh", "auth")))


# ---------- what to say ----------
def due_counts(col):
    """(new, learning, review) the deck list shows for today, all decks, with the deck limits."""
    tree = col.sched.deck_due_tree()
    n = l = r = 0
    for child in tree.children:
        n += child.new_count
        l += child.learn_count
        r += child.review_count
    return n, l, r


def message(counts):
    n, l, r = counts
    total = n + l + r
    if not total:
        return None
    parts = []
    if r:
        parts.append(tn("{n} review", "{n} reviews", r))
    if l:
        parts.append(t("{n} learning", n=l))
    if n:
        parts.append(t("{n} new", n=n))
    title = tn("You have {n} card due", "You have {n} cards due", total)
    return {"title": title, "body": t("{counts}. Tap to study.", counts=", ".join(parts)), "url": "/"}


# ---------- sending ----------
def send_push(sub, payload):
    """Send one push. Returns the HTTP status. Tests replace this function."""
    from pywebpush import webpush, WebPushException
    priv, _ = vapid_keys()
    origin = sub.get("origin") or "https://localhost"
    try:
        r = webpush({"endpoint": sub["endpoint"], "keys": sub["keys"]}, json.dumps(payload),
                    vapid_private_key=priv, vapid_claims={"sub": origin}, ttl=12 * 3600, timeout=15)
        return getattr(r, "status_code", 201)
    except WebPushException as e:
        return getattr(e.response, "status_code", 0) or 0


def send_all(user, payload, only_endpoint=None):
    """Send to every device of `user` (or just one). Drops subscriptions the push service says are
    gone (404/410). Returns (sent, failed)."""
    subs = [s for s in load(user)["subscriptions"] if only_endpoint in (None, s["endpoint"])]
    sent, failed, gone = 0, 0, set()
    for s in subs:
        try:
            status = send_push(s, payload)
        except Exception as e:  # noqa: BLE001  (network errors etc.)
            log.warning("push to %s failed: %s", s["endpoint"][:60], e)
            status = 0
        if 200 <= status < 300:
            sent += 1
        else:
            failed += 1
            if status in (404, 410):
                gone.add(s["endpoint"])
    if gone:
        update(user, lambda c: c.__setitem__(
            "subscriptions", [s for s in c["subscriptions"] if s["endpoint"] not in gone]))
    return sent, failed


# ---------- scheduler ----------
def local_now(conf, now=None):
    now = now if now is not None else time.time()
    return dt.datetime.fromtimestamp(now, ZoneInfo(conf["tz"]))


def is_due_now(conf, now=None):
    """True when today's reminder should go out now (not counting whether cards are due)."""
    if not conf["on"] or not conf["subscriptions"] or not conf["days"]:
        return False
    here = local_now(conf, now)
    if here.weekday() not in conf["days"] or conf["last_sent"] == here.date().isoformat():
        return False
    h, m = map(int, conf["time"].split(":"))
    target = here.replace(hour=h, minute=m, second=0, microsecond=0)
    return 0 <= (here - target).total_seconds() < WINDOW


def check_user(user, now=None):
    """Send `user`'s reminder if it is time. Returns the payload sent, or None."""
    conf = load(user)
    if not is_due_now(conf, now):
        return None
    day = local_now(conf, now).date().isoformat()
    # No request here (the scheduler thread): use the person's own language.
    with i18n.use_lang(core.user_lang(user) or "en"), core.collection(user) as col:
        payload = message(due_counts(col))
    update(user, lambda c: c.__setitem__("last_sent", day))   # once a day, even with nothing due
    if payload:
        payload["tag"] = "fc-reminder"
        send_all(user, payload)
    return payload


def tick(now=None):
    for user in list(core.load_users()):
        if not os.path.exists(conf_path(user)):
            continue
        try:
            check_user(user, now)
        except Exception:  # noqa: BLE001  (one person's problem mustn't stop the others)
            log.exception("reminder for %s failed", user)


_thread = None


def start():
    """Start the scheduler thread once (FastAPI startup). REMINDERS=0 turns it off."""
    global _thread
    if os.environ.get("REMINDERS", "1") == "0" or (_thread and _thread.is_alive()):
        return

    def loop():
        while True:
            tick()
            time.sleep(TICK)

    _thread = threading.Thread(target=loop, name="flashcards-reminders", daemon=True)
    _thread.start()


# ---------- pages ----------
def device_label(sub):
    ua = sub.get("ua", "")
    for needle, name in (("iPad", "iPad"), ("iPhone", "iPhone"), ("CrOS", "Chromebook"), ("Android", "Android"),
                         ("Macintosh", "Mac"), ("Windows", "Windows"), ("Linux", "Linux")):
        if needle in ua:
            dev = name
            break
    else:
        dev = t("Device")
    for needle, name in (("Edg/", "Edge"), ("Firefox/", "Firefox"), ("CriOS", "Chrome"), ("Chrome/", "Chrome"),
                         ("Safari/", "Safari")):
        if needle in ua:
            return f"{dev}, {name}"
    return dev


SCRIPT = """<script>(function(){
const st=document.getElementById('rmstate'),onb=document.getElementById('rmon'),offb=document.getElementById('rmoff'),
 testb=document.getElementById('rmtest'),tz=document.getElementById('rmtz'),key=%(key)s;
try{tz.value=Intl.DateTimeFormat().resolvedOptions().timeZone||tz.value;}catch(e){}
const L=%(texts)s;
function fill(s,v){return s.replace(/[{]([A-Za-z_]+)[}]/g,(m,k)=>k in v?String(v[k]):m);}
function say(t,err){st.textContent=t;st.className=err?'flash err':'flash';st.hidden=false;}
const ios=/iPad|iPhone|iPod/.test(navigator.userAgent)||(navigator.platform==='MacIntel'&&navigator.maxTouchPoints>1);
const standalone=navigator.standalone===true||matchMedia('(display-mode: standalone)').matches;
let supported=window.isSecureContext&&'serviceWorker' in navigator&&'PushManager' in window&&'Notification' in window;
function b64(s){s=s.replace(/-/g,'+').replace(/_/g,'/');s+='='.repeat((4-s.length%%4)%%4);
 return Uint8Array.from(atob(s),c=>c.charCodeAt(0));}
async function current(){if(!supported)return null;const reg=await navigator.serviceWorker.register('/sw.js');
 await navigator.serviceWorker.ready;return {reg,sub:await reg.pushManager.getSubscription()};}
async function post(url,data){const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
 if(!r.ok)throw new Error((await r.json().catch(()=>({}))).error||fill(L.serverSaid,{status:r.status}));return r.json();}
async function show(){if(!supported){onb.disabled=true;offb.hidden=true;testb.disabled=true;
  say(!window.isSecureContext?L.needHttps:ios&&!standalone?L.ipad:L.unsupported,true);return;}
 try{const c=await current();const on=!!(c.sub&&Notification.permission==='granted');
  document.getElementById('rmdev').textContent=on?L.on:L.off;onb.hidden=on;offb.hidden=!on;
  if(Notification.permission==='denied')say(L.blocked,true);
 }catch(e){say(fill(L.cantCheck,{error:e.message}),true);}}
onb.onclick=async()=>{onb.disabled=true;try{
  const p=await Notification.requestPermission();if(p!=='granted'){say(L.notAllowed,true);return;}
  const c=await current();let sub=c.sub;
  if(sub&&sub.options&&sub.options.applicationServerKey){const k=new Uint8Array(sub.options.applicationServerKey),want=b64(key);
   if(k.length!==want.length||k.some((v,i)=>v!==want[i])){await sub.unsubscribe();sub=null;}}
  if(!sub)sub=await c.reg.pushManager.subscribe({userVisibleOnly:true,applicationServerKey:b64(key)});
  await post('/reminders/subscribe',{subscription:sub.toJSON(),tz:tz.value});location.reload();
 }catch(e){say(fill(L.cantTurnOn,{error:e.message}),true);}finally{onb.disabled=false;}};
offb.onclick=async()=>{offb.disabled=true;try{const c=await current();
  if(c.sub){await post('/reminders/unsubscribe',{endpoint:c.sub.endpoint});await c.sub.unsubscribe();}location.reload();
 }catch(e){say(fill(L.cantTurnOff,{error:e.message}),true);}finally{offb.disabled=false;}};
testb.onclick=async()=>{testb.disabled=true;try{const c=await current();
  const r=await post('/reminders/test',{endpoint:c&&c.sub?c.sub.endpoint:null});
  say(r.sent?L.testSent:(r.error||L.nothingSent),!r.sent);
 }catch(e){say(fill(L.cantSend,{error:e.message}),true);}finally{testb.disabled=false;}};
show();})();</script>"""


@router.get("/reminders", response_class=HTMLResponse)
def reminders_page(request: Request):
    user = core.current_user(request)
    if not user:
        return core.need_login(request)
    conf = load(user)
    _, pub = vapid_keys()
    with core.collection(user) as col:
        counts = due_counts(col)
    total = sum(counts)
    e = lambda s: html.escape(s, False)   # translated text in an HTML text node
    days = "".join(f'<label class="rmday"><input type="checkbox" name="days" value="{i}"'
                   f'{" checked" if i in conf["days"] else ""}> {e(t(d))}</label>' for i, d in enumerate(DAY_NAMES))
    devices = "".join(
        f'<tr><td>{html.escape(device_label(s))}<br><span class="muted" style="font-size:14px">'
        f'{e(t("added {date}", date=s.get("added", "")))}</span></td><td style="text-align:right">'
        f'<form method="post" action="/reminders/remove" style="margin:0"><input type="hidden" name="endpoint" '
        f'value="{html.escape(s["endpoint"])}"><button class="danger">{e(t("Remove"))}</button></form></td></tr>'
        for s in conf["subscriptions"]) or f'<tr><td class="muted">{e(t("No devices yet."))}</td></tr>'
    texts = {
        "serverSaid": t("Server said {status}"),
        "needHttps": t("Notifications need the HTTPS link to this app (not plain http://)."),
        "ipad": t("On iPad: tap Share, then Add to Home Screen, open Flashcards from the Home Screen and turn "
                  "reminders on there."),
        "unsupported": t("This browser can't show notifications from web apps."),
        "on": t("on"), "off": t("off"),
        "blocked": t("Notifications are blocked for this site. Allow them in the browser's site settings, then reload."),
        "cantCheck": t("Could not check this device: {error}"),
        "notAllowed": t("Notifications were not allowed."),
        "cantTurnOn": t("Could not turn on notifications: {error}"),
        "cantTurnOff": t("Could not turn off: {error}"),
        "testSent": t("Test notification sent. It can take a few seconds to arrive."),
        "nothingSent": t("Nothing was sent."),
        "cantSend": t("Could not send: {error}"),
    }
    # Sentences with a little markup: escape the translated sentence, then put the markup in.
    state_line = e(t("Notifications on this device are {state}.")).replace("{state}", '<b id="rmdev">…</b>')
    help_line = e(t("Reminders need the {https} link to this app.\nOn {ipad} (iPadOS 16.4 or later) add the app to "
                    "the Home Screen first (Share › Add to Home Screen),\nopen it from there and turn notifications "
                    "on inside it; Safari tabs can't get notifications.\nOn a {chromebook}, Chrome shows them while "
                    "Chrome is running (an open tab isn't needed).")) \
        .replace("{https}", "<b>HTTPS</b>").replace("{ipad}", "<b>iPad</b>").replace("{chromebook}", "<b>Chromebook</b>")
    body = f"""<p><a href="/settings">{e(t("‹ Settings"))}</a></p><h2>{e(t("Study reminders"))}</h2>
<div id="rmstate" class="flash" hidden></div>
<div class="box"><h3 style="margin-top:0">{e(t("This device"))}</h3>
<p>{state_line}</p>
<div class="row"><button id="rmon" class="primary">{e(t("Turn on notifications here"))}</button>
<button id="rmoff" hidden>{e(t("Turn off here"))}</button><button id="rmtest">{e(t("Send a test notification"))}</button></div>
<p class="muted" style="font-size:14px">{help_line}</p></div>
<form class="box" method="post" action="/reminders/settings"><h3 style="margin-top:0">{e(t("When"))}</h3>
<label class="rmday" style="font-size:17px;color:var(--ink)"><input type="checkbox" name="on" value="1"{" checked" if conf["on"] else ""}>
 {e(t("Remind me every day I have cards due"))}</label>
<label for="rmtime">{e(t("Time"))}</label><input id="rmtime" type="time" name="time" value="{conf["time"]}" required style="max-width:180px">
<label>{e(t("Days"))}</label><div class="row">{days}</div>
<input type="hidden" id="rmtz" name="tz" value="{html.escape(conf["tz"])}">
<p class="muted" style="font-size:14px">{e(t(
        "Only sent when cards are due, at most once a day\n(“{example}”). Time zone: {tz} (set from this device when "
        "you save).", example=tn("You have {n} card due", "You have {n} cards due", 34), tz=conf["tz"]))}
{e(tn("Right now: {n} card due.", "Right now: {n} cards due.", total))}</p>
<button class="primary">{e(t("Save"))}</button></form>
<div class="box"><h3 style="margin-top:0">{e(t("Devices that get reminders"))}</h3><table>{devices}</table></div>
<style>.rmday{{display:inline-flex;align-items:center;gap:6px;min-height:44px;margin:0 10px 0 0;color:var(--ink);font-size:16px}}
.rmday input{{width:auto}}</style>
{SCRIPT % {"key": json.dumps(pub), "texts": core.js(texts)}}"""
    return core.page(t("Study reminders"), body, user, "settings", core.take_flash(request))


@router.post("/reminders/settings")
async def reminders_settings(request: Request):
    user = core.current_user(request)
    if not user:
        return core.need_login(request)
    form = await request.form()
    hhmm = str(form.get("time", "")).strip()[:5]
    tz = str(form.get("tz", "")).strip()
    try:
        days = sorted({int(d) for d in form.getlist("days")})
    except ValueError:
        days = None
    if not TIME_RE.fullmatch(hhmm) or days is None or any(not 0 <= d <= 6 for d in days):
        core.set_flash(request, "!" + t("Please choose a time and days."))
        return RedirectResponse("/reminders", 303)
    on = form.get("on") == "1"

    def change(c):
        if (c["time"], c["tz"]) != (hhmm, tz if valid_tz(tz) else c["tz"]) or (on and not c["on"]):
            c["last_sent"] = ""   # a new time today should still fire today
        c.update(on=on, time=hhmm, days=days)
        if valid_tz(tz):
            c["tz"] = tz
    conf = update(user, change)
    if on and not days:
        msg = "!" + t("Saved, but no days are ticked, so no reminders will be sent.")
    elif on and not conf["subscriptions"]:
        msg = t("Saved. Now turn on notifications on a device so the reminder has somewhere to go.")
    else:
        msg = t("Saved.") if on else t("Saved. Reminders are off.")
    core.set_flash(request, msg)
    return RedirectResponse("/reminders", 303)


async def _json(request):
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001
        return None
    return data if isinstance(data, dict) else None


@router.post("/reminders/subscribe")
async def subscribe(request: Request):
    user = core.current_user(request)
    if not user:
        return Response(status_code=401)
    data = await _json(request)
    sub = data and data.get("subscription")
    if not valid_subscription(sub):
        return JSONResponse({"ok": False, "error": t("That isn't a push subscription.")}, status_code=400)
    host = request.headers.get("host", "localhost")
    entry = {"endpoint": sub["endpoint"], "keys": {"p256dh": sub["keys"]["p256dh"], "auth": sub["keys"]["auth"]},
             "origin": f"https://{host}" if re.fullmatch(r"[A-Za-z0-9.\-:\[\]]+", host) else "https://localhost",
             "ua": request.headers.get("user-agent", "")[:300], "added": dt.date.today().isoformat()}
    tz = data.get("tz")

    def change(c):
        c["subscriptions"] = [s for s in c["subscriptions"] if s["endpoint"] != entry["endpoint"]]
        c["subscriptions"] = (c["subscriptions"] + [entry])[-MAX_DEVICES:]
        if valid_tz(tz) and (c["tz"] == "UTC" or not c["on"]):
            c["tz"] = tz
    update(user, change)
    return {"ok": True}


@router.post("/reminders/unsubscribe")
async def unsubscribe(request: Request):
    user = core.current_user(request)
    if not user:
        return Response(status_code=401)
    data = await _json(request)
    ep = data and data.get("endpoint")
    if not isinstance(ep, str):
        return JSONResponse({"ok": False}, status_code=400)
    update(user, lambda c: c.__setitem__("subscriptions", [s for s in c["subscriptions"] if s["endpoint"] != ep]))
    return {"ok": True}


@router.post("/reminders/remove")
async def remove(request: Request):
    user = core.current_user(request)
    if not user:
        return core.need_login(request)
    ep = str((await request.form()).get("endpoint", ""))
    update(user, lambda c: c.__setitem__("subscriptions", [s for s in c["subscriptions"] if s["endpoint"] != ep]))
    core.set_flash(request, t("Device removed."))
    return RedirectResponse("/reminders", 303)


@router.post("/reminders/test")
async def test(request: Request):
    user = core.current_user(request)
    if not user:
        return Response(status_code=401)
    data = await _json(request) or {}
    ep = data.get("endpoint") if isinstance(data.get("endpoint"), str) else None
    subs = load(user)["subscriptions"]
    if not subs or (ep and all(s["endpoint"] != ep for s in subs)):
        return {"ok": True, "sent": 0, "error": t("Turn on notifications on this device first.")}
    with core.collection(user) as col:
        payload = message(due_counts(col)) or {"title": t("No cards due"), "body": t("You're all caught up."), "url": "/"}
    payload["title"] = t("Test: {title}", title=payload["title"])
    payload["tag"] = "fc-test"
    sent, failed = send_all(user, payload, ep)
    return {"ok": True, "sent": sent, "failed": failed,
            "error": "" if sent else t("The push service refused it. Turn notifications off and on again here.")}
