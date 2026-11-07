"""Keeps you moving: shows the answer and answers the card automatically after a set time.

Like the desktop add-on "Speed Focus Mode". Each person sets their own times on the add-on's page.
A thin bar at the top of the card shows the time running out.
"""
import html

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from flashcards import addons
from flashcards.i18n import t, N_

NAME = "Speed Focus Mode"   # plain literal: flashcards/addons.py reads it without importing
N_("Speed Focus Mode")      # ...so mark it for translation here
N_('Keeps you moving: shows the answer and answers the card automatically after a set time.')  # the first docstring line, shown on the Add-ons page
KEY = "addon_speed_focus"
DEFAULTS = {"show_after": 0, "answer_after": 0, "action": 1}
ACTIONS = {1: N_("Again"), 2: N_("Hard"), 3: N_("Good")}
MENU = [(N_("Set the times"), "/")]


def settings(col):
    s = dict(DEFAULTS)
    try:
        s.update(col.get_config(KEY, {}) or {})
    except Exception:
        pass
    return s


SCRIPT = r"""<div id="sfm-bar" style="position:fixed;top:0;left:0;height:4px;width:100%;background:#d64545;
transform-origin:left;z-index:40"></div><script>(function(){
if(!(window.parent&&parent!==window&&parent.location.pathname.indexOf('/study/')===0)){
 var b=document.getElementById('sfm-bar');if(b)b.remove();return;}
var secs=__SECS__,act=__ACT__,side='__SIDE__',bar=document.getElementById('sfm-bar'),t0=Date.now();
bar.style.background=side==='q'?'#2f6fde':'#d64545';
var h=setInterval(function(){var left=1-(Date.now()-t0)/(secs*1000);
 if(document.getElementById('efr')){t0+=250;return;}          // paused while editing the card
 bar.style.transform='scaleX('+Math.max(0,left)+')';
 if(left<=0){clearInterval(h);if(side==='q'){parent.show&&parent.show();}else{parent.answer&&parent.answer(act);}}},250);
})();</script>"""


def card_render(text, card, kind, col):
    s = settings(col)
    secs = s["show_after"] if kind == "question" else s["answer_after"]
    try:
        secs = float(secs)
    except (TypeError, ValueError):
        return text
    if secs <= 0:
        return text
    act = int(s.get("action", 1)) if int(s.get("action", 1)) in ACTIONS else 1
    return text + (SCRIPT.replace("__SECS__", repr(secs)).replace("__ACT__", str(act))
                   .replace("__SIDE__", "q" if kind == "question" else "a"))


router = APIRouter()


@router.get("/")
def page(request: Request):
    with addons.collection(request) as col:
        s = settings(col)
    opts = "".join(f'<option value="{k}" {"selected" if int(s["action"]) == k else ""}>{html.escape(t(v))}</option>'
                   for k, v in ACTIONS.items())
    e = lambda x: html.escape(x, quote=False)
    body = f"""<div class="box"><h2 style="margin-top:0">{e(t("Speed Focus Mode"))}</h2>
<p class="muted">{e(t("Use 0 to switch a timer off. The times only apply on the study screen."))}</p>
<form method="post"><div class="row"><div style="flex:1;min-width:180px">
<label>{e(t("Show the answer after (seconds)"))}</label><input name="show_after" type="number" min="0" max="600" step="0.5"
value="{html.escape(str(s['show_after']))}"></div><div style="flex:1;min-width:180px">
<label>{e(t("Answer the card after (seconds)"))}</label><input name="answer_after" type="number" min="0" max="600" step="0.5"
value="{html.escape(str(s['answer_after']))}"></div><div style="flex:1;min-width:180px">
<label>{e(t("…by pressing"))}</label><select name="action">{opts}</select></div></div>
<p><button class="primary">{e(t("Save"))}</button> <a class="btn" href="/addons">{e(t("Back to add-ons"))}</a></p></form></div>"""
    return addons.page(request, t("Speed Focus Mode"), body, request.session.pop("flash", ""))


@router.post("/")
def save(request: Request, show_after: float = Form(0), answer_after: float = Form(0), action: int = Form(1)):
    with addons.collection(request) as col:
        col.set_config(KEY, {"show_after": max(0.0, min(600.0, show_after)),
                             "answer_after": max(0.0, min(600.0, answer_after)),
                             "action": action if action in ACTIONS else 1})
    request.session["flash"] = t("Saved.")
    return RedirectResponse("/addon/speed_focus/", 303)
