"""Reads cards out loud with the device's built-in voices (no setup, works on iPad and Chromebook).

Like the desktop add-ons HyperTTS / AwesomeTTS, using the browser's own text-to-speech.
Each person picks a language and speed, and whether cards are read automatically.
A 🔈 button on the card reads it again. (Anki's own {{tts ...}} card tags also work without this add-on.)
"""
import html
import json

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from flashcards import addons

NAME = "Read Aloud (text-to-speech)"
KEY = "addon_read_aloud"
DEFAULTS = {"lang": "en-GB", "rate": 1.0, "auto_q": False, "auto_a": False}
LANGS = [("en-GB", "English (UK)"), ("en-US", "English (US)"), ("en-IE", "English (Ireland)"),
         ("fr-FR", "French"), ("es-ES", "Spanish (Spain)"), ("es-MX", "Spanish (Mexico)"),
         ("de-DE", "German"), ("it-IT", "Italian"), ("pt-PT", "Portuguese (Portugal)"),
         ("pt-BR", "Portuguese (Brazil)"), ("nl-NL", "Dutch"), ("ga-IE", "Irish"), ("pl-PL", "Polish"),
         ("ja-JP", "Japanese"), ("zh-CN", "Chinese (Mandarin)"), ("ko-KR", "Korean"), ("ru-RU", "Russian"),
         ("ar-SA", "Arabic"), ("hi-IN", "Hindi")]
MENU = [("Voice settings", "/")]


def settings(col):
    s = dict(DEFAULTS)
    try:
        s.update(col.get_config(KEY, {}) or {})
    except Exception:
        pass
    return s


SCRIPT = r"""<div id="ra-btn" title="Read aloud" style="position:fixed;bottom:8px;right:8px;z-index:50;width:40px;
height:40px;border-radius:20px;border:1px solid #8884;background:#fff8;display:flex;align-items:center;
justify-content:center;font-size:18px;cursor:pointer;-webkit-user-select:none;user-select:none">🔈</div>
<script>(function(){
if(!window.speechSynthesis){var b=document.getElementById('ra-btn');if(b)b.remove();return;}
var CFG=__CFG__,side='__SIDE__';
function text(){var root=document.body.cloneNode(true);
 root.querySelectorAll('script,style,#ra-btn,#efr-btn,#sfm-bar,#pd-pop,.replay-button,input,textarea').forEach(function(n){n.remove();});
 if(side==='a'){var hr=root.querySelector('hr#answer');
  if(hr){var keep=[],n=hr.nextSibling;while(n){keep.push(n);n=n.nextSibling;}
   var d=document.createElement('div');keep.forEach(function(k){d.appendChild(k);});root=d;}}
 return (root.innerText||root.textContent||'').replace(/\s+/g,' ').trim();}
function speak(){speechSynthesis.cancel();var t=text();if(!t)return;var u=new SpeechSynthesisUtterance(t);
 u.lang=CFG.lang;u.rate=CFG.rate;var v=speechSynthesis.getVoices().filter(function(x){return x.lang.replace('_','-')===CFG.lang;})[0];
 if(v)u.voice=v;speechSynthesis.speak(u);}
document.getElementById('ra-btn').addEventListener('click',function(e){e.stopPropagation();speak();});
if((side==='q'&&CFG.auto_q)||(side==='a'&&CFG.auto_a)){setTimeout(speak,150);}
})();</script>"""


def card_render(text, card, kind, col):
    s = settings(col)
    cfg = {"lang": s["lang"], "rate": float(s["rate"]), "auto_q": bool(s["auto_q"]), "auto_a": bool(s["auto_a"])}
    return text + (SCRIPT.replace("__CFG__", json.dumps(cfg).replace("</", "<\\/"))
                   .replace("__SIDE__", "q" if kind == "question" else "a"))


router = APIRouter()


@router.get("/")
def page(request: Request):
    with addons.collection(request) as col:
        s = settings(col)
    opts = "".join(f'<option value="{c}" {"selected" if c == s["lang"] else ""}>{n}</option>' for c, n in LANGS)
    chk = lambda k: "checked" if s[k] else ""
    body = f"""<div class="box"><h2 style="margin-top:0">Read Aloud</h2>
<p class="muted">Uses the voices built into this iPad, Chromebook or computer. Tap 🔈 on a card to hear it.
On the answer side only the answer part is read.</p>
<form method="post"><div class="row"><div style="flex:2;min-width:200px"><label>Language</label>
<select name="lang">{opts}</select></div><div style="flex:1;min-width:140px"><label>Speed</label>
<input name="rate" type="number" min="0.5" max="2" step="0.1" value="{float(s['rate'])}"></div></div>
<label style="display:flex;gap:10px;align-items:center;color:inherit"><input type="checkbox" name="auto_q" value="1"
style="width:auto" {chk('auto_q')}> Read the question automatically</label>
<label style="display:flex;gap:10px;align-items:center;color:inherit"><input type="checkbox" name="auto_a" value="1"
style="width:auto" {chk('auto_a')}> Read the answer automatically</label>
<p><button class="primary">Save</button> <button type="button" onclick="t()">Test the voice</button>
<a class="btn" href="/addons">Back to add-ons</a></p></form></div>
<script>function t(){{const f=document.forms[0],u=new SpeechSynthesisUtterance(
f.lang.options[f.lang.selectedIndex].text);u.lang=f.lang.value;u.rate=+f.rate.value||1;
speechSynthesis.cancel();speechSynthesis.speak(u);}}</script>"""
    return addons.page(request, "Read Aloud", body, request.session.pop("flash", ""))


@router.post("/")
def save(request: Request, lang: str = Form("en-GB"), rate: float = Form(1.0),
         auto_q: str = Form(""), auto_a: str = Form("")):
    if lang not in dict(LANGS):
        lang = "en-GB"
    with addons.collection(request) as col:
        col.set_config(KEY, {"lang": lang, "rate": max(0.5, min(2.0, rate)),
                             "auto_q": bool(auto_q), "auto_a": bool(auto_a)})
    request.session["flash"] = "Saved."
    return RedirectResponse("/addon/read_aloud/", 303)
