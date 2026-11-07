"""Undo / redo everywhere, through the engine (Anki's Edit > Undo / Redo).

Every page header shows Undo and Redo buttons while the engine has something to undo or
redo; their tooltips carry the engine's own label ("Undo Answer Card"). Ctrl+Z (Cmd+Z on
an iPad keyboard) undoes and Ctrl+Shift+Z / Ctrl+Y redoes on every page, except while
typing in a field and on pages that handle those keys themselves (the note editor and the
image occlusion mask editor). After an undo or redo the page reloads and a toast says what
happened, in the engine's words ("Answer Card undone").

Calls: `col.undo()`, `col.redo()`, `col.undo_status()`; texts from `col.tr`.
"""
import html, re
from urllib.parse import urlsplit

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from anki.errors import UndoEmpty

import app as core

router = APIRouter()

def clean(s):
    return re.sub(r"[⁨⁩]", "", s or "")

def status(user):
    """(undo label, redo label) for the header. Read without the per-user lock: page() is
    often called while the request already holds it, and this is a read-only engine call
    (the engine serialises its own calls)."""
    col = core._cols.get(user) if user else None
    if col is None:
        return "", ""
    try:
        st = col.undo_status()
        return st.undo, st.redo
    except Exception:
        return "", ""

def header_buttons(user):
    undo, redo = status(user)
    out = []
    for kind, label, icon, keys in (("undo", undo, "↶", "Ctrl+Z"), ("redo", redo, "↷", "Ctrl+Shift+Z")):
        if not label:
            continue
        title = html.escape(f"{kind.title()} {label} ({keys})")
        out.append(f'<form method="post" action="/{kind}" class="fc-ur"><input type="hidden" name="back" value="">'
                   f'<button title="{title}" aria-label="{title}" data-label="{html.escape(label)}">{icon}</button></form>')
    return "".join(out)

# Shared by every page (added by core.page). Pages that use Ctrl+Z themselves mark the page
# with an element carrying data-noundokey, or call preventDefault before this runs.
SCRIPT = """<div id="fc-toast" role="status" aria-live="polite"></div><script>
(function(){
const T=document.getElementById('fc-toast');
function toast(m){T.textContent=m;T.classList.add('on');clearTimeout(T._h);T._h=setTimeout(()=>T.classList.remove('on'),2200);}
window.fcToast=toast;
try{const m=sessionStorage.getItem('fc-toast');if(m){sessionStorage.removeItem('fc-toast');toast(m);}}catch(e){}
document.querySelectorAll('form.fc-ur input[name=back]').forEach(i=>i.value=location.pathname+location.search);
let busy=false;
async function run(kind){if(busy)return;busy=true;
 try{const r=await fetch('/'+kind,{method:'POST',headers:{'X-Requested-With':'fetch'}});const j=await r.json();
  if(!j.ok){busy=false;toast(j.msg);return;}
  try{sessionStorage.setItem('fc-toast',j.msg);}catch(e){}
  // The page may show something the undo removed (a deck, a note): fall back to the deck list.
  const url=window.FC_AFTER_UNDO||(location.pathname+location.search);let ok=true;
  try{ok=(await fetch(url,{headers:{'X-Requested-With':'fc-check'}})).ok;}catch(e){}
  location.replace(ok?url:'/');
 }catch(e){busy=false;toast('That did not work.');}}
window.fcUndo=()=>run('undo');window.fcRedo=()=>run('redo');
document.querySelectorAll('form.fc-ur').forEach(f=>f.addEventListener('submit',e=>{e.preventDefault();
 run(f.getAttribute('action').slice(1));}));
function key(e){if(e.defaultPrevented||!(e.ctrlKey||e.metaKey)||e.altKey)return;
 const k=(e.key||'').toLowerCase();const redo=(k==='z'&&e.shiftKey)||(k==='y'&&!e.shiftKey);
 if(!(redo||(k==='z'&&!e.shiftKey)))return;
 const t=e.target;if(t&&(t.isContentEditable||/^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName)))return;
 if(document.querySelector('[data-noundokey]'))return;
 e.preventDefault();run(redo?'redo':'undo');}
window.fcUndoKey=key;document.addEventListener('keydown',key);
})();
</script>"""

def back_of(request, form_back):
    back = str(form_back or "")
    if not back:
        ref = request.headers.get("referer", "")
        if ref:
            p = urlsplit(ref)
            back = p.path + (f"?{p.query}" if p.query else "")
    return core.safe_back(back, "/")

async def undo_redo(request: Request, redo: bool):
    user = core.current_user(request)
    if not user: return core.need_login(request)
    form = await request.form()
    ajax = request.headers.get("x-requested-with") == "fetch"
    with core.collection(user) as col:
        try:
            out = col.redo() if redo else col.undo()
            done = col.tr.undo_action_redone if redo else col.tr.undo_action_undone
            msg, ok = clean(done(action=out.operation)), True
        except UndoEmpty:
            msg = clean(col.tr.actions_nothing_to_redo() if redo else col.tr.actions_nothing_to_undo())
            ok = False
        except Exception as e:
            msg, ok = str(e) or "That did not work.", False
        st = col.undo_status()
    if ajax:
        return {"ok": ok, "msg": msg, "undo": st.undo, "redo": st.redo}
    core.set_flash(request, msg if ok else "!" + msg)
    return RedirectResponse(back_of(request, form.get("back")), 303)

@router.post("/undo")
async def undo_route(request: Request):
    return await undo_redo(request, False)

@router.post("/redo")
async def redo_route(request: Request):
    return await undo_redo(request, True)

@router.get("/undo/status")
def undo_status(request: Request):
    user = core.current_user(request)
    if not user: return {"undo": "", "redo": ""}
    with core.collection(user) as col:
        st = col.undo_status()
        return {"undo": st.undo, "redo": st.redo}
