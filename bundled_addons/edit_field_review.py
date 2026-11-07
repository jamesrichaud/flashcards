"""Fix a card's text while you study: tap the ✏️ on the card, edit the fields, save.

Like the desktop add-on "Edit Field During Review". The card stays where it was in the study
session (question or answer side, timer kept).
"""
import html
import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from flashcards import addons

NAME = "Edit Field During Review"

SCRIPT = r"""<div id="efr-btn" title="Edit this card's text" style="position:fixed;top:8px;right:8px;z-index:50;
width:40px;height:40px;border-radius:20px;border:1px solid #8884;background:#fff8;display:flex;align-items:center;
justify-content:center;font-size:18px;cursor:pointer;-webkit-user-select:none;user-select:none">✏️</div>
<script>(function(){
if(!(window.parent&&parent!==window&&parent.location.pathname.indexOf('/study/')===0)){
  var b=document.getElementById('efr-btn');if(b)b.remove();return;}
var NID=__NID__,URL='/addon/edit_field_review/note/'+NID;
function esc(s){var d=document.createElement('div');d.textContent=s;return d.innerHTML;}
document.getElementById('efr-btn').addEventListener('click',async function(e){
 e.stopPropagation();if(parent.stopAudio)parent.stopAudio();
 var j;try{j=await(await fetch(URL)).json();}catch(err){alert("Couldn't load the note.");return;}
 var o=document.createElement('div');o.id='efr';
 o.style.cssText='position:fixed;inset:0;z-index:60;background:rgba(0,0,0,.45);display:flex;align-items:flex-start;justify-content:center;padding:16px;overflow:auto';
 var h='<form style="background:#fff;color:#111;border-radius:14px;padding:16px;width:100%;max-width:640px;font:16px system-ui,sans-serif;text-align:left">'
  +'<b style="font-size:18px">Edit card text</b>';
 j.fields.forEach(function(f,i){h+='<label style="display:block;margin:12px 0 4px;color:#666;font-size:14px">'+esc(f.name)+'</label>'
  +'<textarea data-name="'+esc(f.name)+'" style="width:100%;box-sizing:border-box;min-height:70px;padding:10px;border:1px solid #ccc;border-radius:10px;font:inherit">'+esc(f.value)+'</textarea>';});
 h+='<div style="display:flex;gap:10px;margin-top:14px;justify-content:flex-end">'
  +'<button type="button" id="efr-cancel" style="min-height:44px;padding:0 18px;border-radius:10px;border:1px solid #ccc;background:#fff;font:inherit">Cancel</button>'
  +'<button type="button" id="efr-save" style="min-height:44px;padding:0 18px;border-radius:10px;border:0;background:#2f6fde;color:#fff;font:inherit">Save</button></div>'
  +'<p style="color:#888;font-size:13px;margin:10px 0 0">HTML is kept as is.</p></form>';
 o.innerHTML=h;document.body.appendChild(o);
 try{parent.document.getElementById('frame').style.height=Math.max(parent.innerHeight*.9,600)+'px';}catch(err){}
 o.querySelector('#efr-cancel').onclick=function(){o.remove();};
 o.querySelector('form').onsubmit=function(ev){ev.preventDefault();};
 o.querySelector('#efr-save').onclick=async function(){var fields={};
  o.querySelectorAll('textarea').forEach(function(t){fields[t.dataset.name]=t.value;});
  var r;try{r=await(await fetch(URL,{method:'POST',headers:{'Content-Type':'application/json'},
   body:JSON.stringify({fields:fields})})).json();}catch(err){alert("Couldn't save. Check the connection.");return;}
  if(!r.ok){alert(r.error||"Couldn't save.");return;}
  // navigate the study page itself (the card frame is sandboxed and may not navigate it directly)
  var a=parent.document.createElement('a');a.href=parent.location.pathname+(parent.here?parent.here():'');
  parent.document.body.appendChild(a);a.click();};
 var first=o.querySelector('textarea');if(first)first.focus();
});})();</script>"""


def card_render(text, card, kind, col):
    return text + SCRIPT.replace("__NID__", str(int(card.nid)))


router = APIRouter()


@router.get("/note/{nid}")
def get_note(request: Request, nid: int):
    with addons.collection(request) as col:
        try:
            note = col.get_note(nid)
        except Exception:
            return JSONResponse({"error": "No such note."}, 404)
        return {"fields": [{"name": k, "value": v} for k, v in note.items()]}


@router.post("/note/{nid}")
async def save_note(request: Request, nid: int):
    if not request.headers.get("content-type", "").startswith("application/json"):
        return JSONResponse({"ok": False, "error": "Bad request."}, 400)
    try:
        data = await request.json()
        fields = data["fields"]
        assert isinstance(fields, dict)
    except Exception:
        return JSONResponse({"ok": False, "error": "Bad request."}, 400)
    with addons.collection(request) as col:
        try:
            note = col.get_note(nid)
        except Exception:
            return JSONResponse({"ok": False, "error": "No such note."}, 404)
        for name, value in fields.items():
            if name in note and isinstance(value, str):
                note[name] = value
        col.update_note(note)
    return {"ok": True}
