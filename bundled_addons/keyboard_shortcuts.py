"""Change the keys used while studying: show, answer, undo, bury, suspend, flag and more.

Like the desktop add-on "Customize Keyboard Shortcuts". Each person sets their own keys on the
add-on's page (kept in their collection). A changed action no longer answers to its old key, so
the old key is free for something else. Keys with Ctrl/Cmd/Alt (Ctrl+Z, Ctrl+1…7) keep working.
"""
import html, json

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from flashcards import addons
from flashcards.i18n import t, N_

NAME = "Customize Keyboard Shortcuts"   # plain literal: flashcards/addons.py reads it without importing
N_("Customize Keyboard Shortcuts")      # ...so mark it for translation here
N_('Change the keys used while studying: show, answer, undo, bury, suspend, flag and more.')
KEY = "addon_keyboard_shortcuts"
MENU = [(N_("Choose keys"), "/")]

# action -> (label, the study screen's own key or "" for none). Keys are KeyboardEvent.key values.
ACTIONS = {
    "show": (N_("Show answer"), " "), "again": (N_("Again"), "1"), "hard": (N_("Hard"), "2"),
    "good": (N_("Good"), "3"), "easy": (N_("Easy"), "4"), "undo": (N_("Undo"), ""),
    "edit": (N_("Edit"), "e"), "info": (N_("Card info"), "i"), "replay": (N_("Replay audio"), "r"),
    "mark": (N_("Mark note"), "*"), "bury": (N_("Bury card"), "-"), "bury_note": (N_("Bury note"), "="),
    "suspend": (N_("Suspend card"), "@"), "suspend_note": (N_("Suspend note"), "!"),
    "flag1": (N_("Red"), ""), "flag2": (N_("Orange"), ""), "flag3": (N_("Green"), ""), "flag4": (N_("Blue"), ""),
}
FLAG_ACTIONS = ("flag1", "flag2", "flag3", "flag4")
NAMED = ["Enter", "Backspace", "Delete", "Home", "End", "PageUp", "PageDown", "ArrowLeft", "ArrowRight",
         "ArrowUp", "ArrowDown", "Insert"] + [f"F{i}" for i in range(1, 13)]


def norm(k):
    """Stored form of a key: one character (letters in lower case), a named key, or "" for none."""
    k = str(k or "")
    if k in ("Space", " "):
        return " "
    if len(k) == 1:
        return k.lower() if k.isprintable() else ""
    low = {n.lower(): n for n in NAMED}
    return low.get(k.strip().lower(), None if k.strip() else "")


def shown(k):
    return {" ": "Space"}.get(k, k.upper() if len(k) == 1 else k)


def settings(col):
    """{action: key} for every action: the person's choice, else the study screen's own key."""
    try:
        saved = col.get_config(KEY, {}) or {}
    except Exception:
        saved = {}
    out = {}
    for a, (_, default) in ACTIONS.items():
        k = saved.get(a, default) if isinstance(saved, dict) else default
        k = norm(k)
        out[a] = default if k is None else k
    return out


def plan(keys):
    """(key -> action for changed actions, keys to switch off): what the study screen must do differently."""
    mapping = {k: a for a, k in keys.items() if k and k != ACTIONS[a][1]}
    off = []
    for a, k in keys.items():
        old = ACTIONS[a][1]
        if old and k != old and old not in mapping:
            off.append(old)
            if a == "show":
                off.append("Enter")    # Enter shows the answer too
    return mapping, sorted(set(off))


SCRIPT = r"""<script>(function(){
var MAP=__MAP__,OFF=__OFF__,fr=document.getElementById('frame');
function norm(k){return k.length===1?k.toLowerCase():k;}
function run(a){
 if(a==='show'){if(!shown)show();return;}
 var n={again:1,hard:2,good:3,easy:4}[a];if(n){answer(n);return;}
 if(a==='undo'){window.fcUndo&&fcUndo();return;}
 if(a==='edit'){edit();return;}if(a==='info'){info();return;}if(a==='replay'){replay();return;}
 if(a.indexOf('flag')===0){act('flag',+a.slice(4));return;}
 act(a);}
function key(e){
 if(e.ctrlKey||e.metaKey||e.altKey||!e.key)return;
 var t=e.target,tg=t&&t.tagName;if(tg==='INPUT'||tg==='TEXTAREA'||tg==='SELECT'||(t&&t.isContentEditable))return;
 var k=norm(e.key);
 if(Object.prototype.hasOwnProperty.call(MAP,k)){
  if((k===' '||k==='Enter')&&(tg==='BUTTON'||tg==='SUMMARY'))return;
  e.preventDefault();e.stopImmediatePropagation();run(MAP[k]);return;}
 if(OFF.indexOf(k)>=0){
  if((k===' '||k==='Enter')&&shown&&OFF_SHOW_ONLY)return;   // Space/Enter still answer Good once shown
  e.preventDefault();e.stopImmediatePropagation();}}
var OFF_SHOW_ONLY=__SHOWONLY__;
document.addEventListener('keydown',key,true);
if(fr)fr.addEventListener('load',function(){try{fr.contentDocument.addEventListener('keydown',key,true);}catch(e){}});
})();</script>"""


def study_page(col, card):
    mapping, off = plan(settings(col))
    if not mapping and not off:
        return ""
    # Space/Enter switched off only stops showing the answer (they still mean Good after it, like Anki).
    show_only = " " in off and settings(col)["good"] == "3"
    return (SCRIPT.replace("__MAP__", json.dumps(mapping)).replace("__OFF__", json.dumps(off))
            .replace("__SHOWONLY__", "true" if show_only else "false"))


router = APIRouter()

PAGE_JS = r"""<script>
document.querySelectorAll('input.kb').forEach(function(i){
 i.addEventListener('keydown',function(e){
  if(e.key==='Tab'||e.ctrlKey||e.metaKey||e.altKey)return;
  e.preventDefault();
  if(e.key==='Escape'){i.value='';return;}
  i.value=e.key===' '?'Space':(e.key.length===1?e.key.toUpperCase():e.key);});
});
document.querySelectorAll('button.kbclear').forEach(function(b){
 b.addEventListener('click',function(){var i=document.getElementById(b.dataset.for);i.value='';i.focus();});});
</script>"""


@router.get("/")
def page(request: Request):
    with addons.collection(request) as col:
        keys = settings(col)
    e = lambda x: html.escape(x, quote=False)
    rows = []
    for a, (label, default) in ACTIONS.items():
        name = t("{flag} flag", flag=t(label)) if a in FLAG_ACTIONS else t(label)
        std = shown(default) if default else "—"
        rows.append(f"""<tr><td><label for="kb-{a}" style="margin:0;color:var(--ink)">{e(name)}</label></td>
<td class="muted">{html.escape(std)}</td>
<td><div class="row" style="flex-wrap:nowrap;gap:6px"><input class="kb" id="kb-{a}" name="{a}" value="{html.escape(shown(keys[a]))}"
autocomplete="off" autocapitalize="none" spellcheck="false" style="width:130px">
<button type="button" class="kbclear" data-for="kb-{a}" aria-label="{html.escape(t("Clear"))}">✕</button></div></td></tr>""")
    body = f"""<div class="box"><h2 style="margin-top:0">{e(t("Customize Keyboard Shortcuts"))}</h2>
<p class="muted">{e(t("Tap a box and press the key you want. Esc or ✕ leaves an action without a key."))}
{e(t("Ctrl+Z (undo) and Ctrl+1…7 (flags) always work."))}</p>
<form method="post"><div style="overflow-x:auto"><table><tr><th>{e(t("Action"))}</th><th>{e(t("Standard key"))}</th>
<th>{e(t("Your key"))}</th></tr>{"".join(rows)}</table></div>
<p class="row"><button class="primary">{e(t("Save"))}</button><button name="reset" value="1">{e(t("Standard keys"))}</button>
<a class="btn" href="/addons">{e(t("Back to add-ons"))}</a></p></form></div>{PAGE_JS}"""
    return addons.page(request, t("Customize Keyboard Shortcuts"), body, request.session.pop("flash", ""))


@router.post("/")
async def save(request: Request):
    form = await request.form()
    if form.get("reset"):
        with addons.collection(request) as col:
            col.set_config(KEY, {})
        request.session["flash"] = t("Standard keys restored.")
        return RedirectResponse("/addon/keyboard_shortcuts/", 303)
    keys, bad = {}, []
    for a in ACTIONS:
        k = norm(str(form.get(a, ""))[:20])
        if k is None:
            bad.append(str(form.get(a, ""))[:20])
            k = ""
        keys[a] = k
    used = {}
    for a, k in keys.items():
        if k:
            used.setdefault(k, []).append(a)
    clash = [k for k, acts in used.items() if len(acts) > 1]
    if bad:
        request.session["flash"] = "!" + t("Not a key that can be used: {keys}", keys=", ".join(bad))
    elif clash:
        request.session["flash"] = "!" + t("One key can only do one thing: {keys}", keys=", ".join(shown(k) for k in clash))
    else:
        with addons.collection(request) as col:
            col.set_config(KEY, {a: k for a, k in keys.items() if k != ACTIONS[a][1]})
        request.session["flash"] = t("Saved.")
    return RedirectResponse("/addon/keyboard_shortcuts/", 303)
