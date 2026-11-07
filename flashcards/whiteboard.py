"""Whiteboard on the study screen (like AnkiMobile/AnkiDroid): a transparent drawing layer over
the card, for Apple Pencil, finger and mouse.

The settings (on/off, pen colour, pen width) are kept in the collection's config under
`flashcardsWhiteboard`, so they follow the person to every device (desktop Anki ignores the key).
The drawing itself lives only in the page: a new card is a new page, so it starts clean.

How input is handled (see NOTES.md, "Whiteboard"):
- pen and mouse draw; a finger draws too until a pen has been used in this browser tab, then
  fingers only scroll and tap (palm-safe);
- two fingers always scroll; a short still tap with a finger or mouse goes through to the card
  (links, buttons, add-on buttons, the type-in box, and tap-to-answer if it is on).
"""
import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

import app as core

router = APIRouter()

WB_KEY = "flashcardsWhiteboard"
COLORS = {"black": "#111111", "white": "#ffffff", "red": "#e5484d", "blue": "#2f6fde"}
WIDTHS = {2: "Thin", 4: "Medium", 8: "Thick"}
DEFAULT = {"on": False, "color": "", "width": 4}   # colour "" = black, or white in dark mode


def settings(col):
    raw = col.get_config(WB_KEY, None)
    out = dict(DEFAULT)
    if isinstance(raw, dict):
        out["on"] = raw.get("on") is True
        if raw.get("color") in COLORS:
            out["color"] = raw["color"]
        if raw.get("width") in WIDTHS:
            out["width"] = raw["width"]
    return out


@router.post("/study/whiteboard")
async def save(request: Request):
    """Change any of on/color/width. JSON body; unknown or bad values are refused."""
    user = core.current_user(request)
    if not user:
        return Response(status_code=401)
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001
        data = None
    if not isinstance(data, dict) or not data or set(data) - {"on", "color", "width"}:
        return JSONResponse({"ok": False}, status_code=400)
    if ("on" in data and not isinstance(data["on"], bool)) or \
            ("color" in data and data["color"] not in COLORS and data["color"] != "") or \
            ("width" in data and (isinstance(data["width"], bool) or data["width"] not in WIDTHS)):
        return JSONResponse({"ok": False}, status_code=400)
    with core.collection(user) as col:
        conf = settings(col)
        conf.update(data)
        if conf != settings(col) or col.get_config(WB_KEY, None) is None:
            col.set_config(WB_KEY, conf)
        conf = settings(col)
    return {"ok": True, **conf, "stamp": core.sync_mod.stamp(user)}


def toggle_html(conf):
    on = "true" if conf["on"] else "false"
    return (f'<button type="button" id="wbtoggle" aria-pressed="{on}" title="Whiteboard" '
            f'aria-label="Whiteboard">✏️<span class="wbl"> Whiteboard</span></button>')


def bar_html(conf):
    sw = "".join(f'<button type="button" class="wbc" data-wbc="{k}" aria-label="{k.title()} pen" title="{k.title()}">'
                 f'<span style="background:{v}"></span></button>' for k, v in COLORS.items())
    ww = "".join(f'<button type="button" class="wbw" data-wbw="{w}" aria-label="{t} pen" title="{t}">'
                 f'<span style="width:{w + 4}px;height:{w + 4}px"></span></button>' for w, t in WIDTHS.items())
    return f"""<div id="wbbar" class="row" style="{'' if conf['on'] else 'display:none'}">
<div class="wbg">{sw}</div><div class="wbg">{ww}</div>
<button type="button" id="wberase" aria-pressed="false">Eraser</button>
<button type="button" id="wbundo" title="Undo last stroke">↶ Undo stroke</button>
<button type="button" id="wbclear">Clear</button></div>"""


CSS = """#wbwrap{position:relative}#wb{position:absolute;left:0;top:0;border-radius:14px;touch-action:none;
-webkit-user-select:none;user-select:none;-webkit-touch-callout:none;cursor:crosshair;z-index:2}
#wbtoggle[aria-pressed=true],#wbbar button[aria-pressed=true]{background:var(--accent);border-color:var(--accent);color:#fff}
#wbbar{margin-bottom:10px;gap:8px}.wbg{display:flex;gap:2px;border:1px solid var(--line);border-radius:10px;padding:2px;background:var(--card)}
.wbg button{min-width:44px;padding:0;border:0;display:flex;align-items:center;justify-content:center;background:none}
.wbg span{display:block;border-radius:50%}.wbc span{width:22px;height:22px;border:1px solid var(--muted)}
.wbw span{background:var(--ink)}
@media (max-width:700px){.wbl{display:none}}"""


# Plain JavaScript (not an f-string). fcWhiteboard(o) wires a canvas over the card frame.
# o: canvas, frame, bar, toggle, conf {on,color,width}, dark() -> bool,
#    tap(x, y) (a tap at canvas position x,y should go to the card), save(changes).
JS = r"""
function fcWhiteboard(o){
 const COL=%COLORS%;
 const cv=o.canvas,fr=o.frame,ctx=cv.getContext('2d');
 const W={on:!!o.conf.on,color:o.conf.color||'',width:o.conf.width||4,erase:false};
 let strokes=[],cur=null,dpr=1,penAt=0;const ptrs=new Map();
 let pen=false;try{pen=sessionStorage.getItem('fcWbPen')==='1';}catch(e){}
 function color(){return W.color?COL[W.color]:(o.dark()?COL.white:COL.black);}
 function size(){const w=fr.offsetWidth,h=fr.offsetHeight;dpr=window.devicePixelRatio||1;
  cv.style.width=w+'px';cv.style.height=h+'px';
  const pw=Math.round(w*dpr),ph=Math.round(h*dpr);
  if(cv.width!==pw||cv.height!==ph){cv.width=pw;cv.height=ph;redraw();}}
 function seg(s,i){const p=s.p;ctx.save();ctx.globalCompositeOperation=s.erase?'destination-out':'source-over';
  ctx.strokeStyle=ctx.fillStyle=s.erase?'#000':s.c;ctx.lineCap=ctx.lineJoin='round';ctx.beginPath();
  if(i===0){ctx.arc(p[0][0],p[0][1],p[0][2]/2,0,2*Math.PI);ctx.fill();}
  else{ctx.lineWidth=(p[i-1][2]+p[i][2])/2;ctx.moveTo(p[i-1][0],p[i-1][1]);ctx.lineTo(p[i][0],p[i][1]);ctx.stroke();}
  ctx.restore();}
 function redraw(){ctx.setTransform(dpr,0,0,dpr,0,0);ctx.clearRect(0,0,cv.width/dpr,cv.height/dpr);
  for(const s of strokes)for(let i=0;i<s.p.length;i++)seg(s,i);}
 function pos(e){const r=cv.getBoundingClientRect();return[e.clientX-r.left,e.clientY-r.top];}
 function wid(e){const b=W.erase?W.width*4+8:W.width;
  return e.pointerType==='pen'&&e.pressure>0?b*(0.4+1.2*e.pressure):b;}
 function touches(){let n=0;for(const p of ptrs.values())if(p.type==='touch')n++;return n;}
 function dropCur(){if(!cur)return;const i=strokes.indexOf(cur.s);if(i>=0)strokes.splice(i,1);cur=null;redraw();}
 cv.addEventListener('pointerdown',e=>{
  if(!W.on)return;e.preventDefault();
  if(e.pointerType==='mouse'&&e.button!==0)return;
  if(e.pointerType==='pen'){penAt=Date.now();if(!pen){pen=true;try{sessionStorage.setItem('fcWbPen','1');}catch(_){}}
   if(cur&&cur.type==='touch')dropCur();}
  const[x,y]=pos(e);
  ptrs.set(e.pointerId,{type:e.pointerType,sx:x,sy:y,cy:e.clientY,t:Date.now(),moved:false,
   multi:e.pointerType==='touch'&&touches()>0,palm:e.pointerType==='touch'&&(e.width>40||e.height>40)});
  try{cv.setPointerCapture(e.pointerId);}catch(_){}
  if(e.pointerType==='touch'){
   if(touches()>1){for(const p of ptrs.values())if(p.type==='touch')p.multi=true;
    if(cur&&cur.type==='touch')dropCur();}   // a second finger: scroll, not draw
   if(pen||touches()>1||(cur&&cur.type==='pen'))return;}
  if(cur)return;   // one stroke at a time
  cur={id:e.pointerId,type:e.pointerType,s:{c:color(),erase:W.erase,p:[[x,y,wid(e)]]}};
  strokes.push(cur.s);seg(cur.s,0);});
 cv.addEventListener('pointermove',e=>{const p=ptrs.get(e.pointerId);if(!p)return;e.preventDefault();
  const[x,y]=pos(e);if(Math.abs(x-p.sx)>10||Math.abs(y-p.sy)>10)p.moved=true;
  if(cur&&cur.id===e.pointerId){
   const list=e.getCoalescedEvents?e.getCoalescedEvents():[];
   for(const c of(list.length?list:[e])){const[cx,cy]=pos(c);cur.s.p.push([cx,cy,wid(c)]);seg(cur.s,cur.s.p.length-1);}
   return;}
  if(p.type==='touch'){   // a finger that isn't drawing scrolls the page
   if(p.palm||Date.now()-penAt<500||(cur&&cur.type==='pen'))return;
   const dy=e.clientY-p.cy;p.cy=e.clientY;if(p.moved)window.scrollBy(0,-dy/Math.max(1,touches()));}});
 function end(e,cancel){const p=ptrs.get(e.pointerId);if(!p)return;ptrs.delete(e.pointerId);
  const still=!cancel&&!p.moved&&!p.multi&&!p.palm&&Date.now()-p.t<600;
  if(cur&&cur.id===e.pointerId){
   if(still&&p.type!=='pen'){dropCur();o.tap(p.sx,p.sy);}   // a finger/mouse tap goes to the card; pen taps are dots
   cur=null;return;}
  if(still&&p.type==='touch'&&Date.now()-penAt>500&&!(cur&&cur.type==='pen'))o.tap(p.sx,p.sy);}
 cv.addEventListener('pointerup',e=>end(e,false));
 cv.addEventListener('pointercancel',e=>end(e,true));
 cv.addEventListener('contextmenu',e=>e.preventDefault());
 function pressed(){
  for(const b of o.bar.querySelectorAll('[data-wbc]'))b.setAttribute('aria-pressed',String(!W.erase&&b.dataset.wbc===(W.color||(o.dark()?'white':'black'))));
  for(const b of o.bar.querySelectorAll('[data-wbw]'))b.setAttribute('aria-pressed',String(+b.dataset.wbw===W.width));
  o.bar.querySelector('#wberase').setAttribute('aria-pressed',String(W.erase));
  o.toggle.setAttribute('aria-pressed',String(W.on));}
 function show(){cv.style.display=W.on?'block':'none';o.bar.style.display=W.on?'':'none';pressed();if(W.on)size();}
 o.toggle.addEventListener('click',()=>{W.on=!W.on;if(!W.on){cur=null;ptrs.clear();}show();o.save({on:W.on});});
 o.bar.addEventListener('click',e=>{const b=e.target.closest&&e.target.closest('button');if(!b)return;
  if(b.dataset.wbc){W.color=b.dataset.wbc;W.erase=false;o.save({color:W.color});}
  else if(b.dataset.wbw){W.width=+b.dataset.wbw;o.save({width:W.width});}
  else if(b.id==='wberase')W.erase=!W.erase;
  else if(b.id==='wbundo'){cur=null;strokes.pop();redraw();}
  else if(b.id==='wbclear'){cur=null;strokes=[];redraw();}
  pressed();});
 if(window.ResizeObserver)new ResizeObserver(()=>{if(W.on)size();}).observe(fr);
 window.addEventListener('resize',()=>{if(W.on)size();});
 show();
 return{state:W,strokes:()=>strokes,size,redraw,penMode:()=>pen};
}
""".replace("%COLORS%", json.dumps(COLORS))


def study_script(conf):
    """Wiring for the study screen; expects fr, wbTap() and fcStampSeen from the page."""
    return f"""<script>{JS}
const WB=fcWhiteboard({{canvas:document.getElementById('wb'),frame:fr,bar:document.getElementById('wbbar'),
 toggle:document.getElementById('wbtoggle'),conf:{json.dumps(conf)},tap:wbTap,
 dark:()=>{{const c=document.documentElement.classList;return c.contains('dark')||(!c.contains('light')&&matchMedia('(prefers-color-scheme:dark)').matches);}},
 save:async ch=>{{try{{const r=await(await fetch('/study/whiteboard',{{method:'POST',body:JSON.stringify(ch),
  headers:{{'Content-Type':'application/json','X-Requested-With':'fetch'}}}})).json();
  if(r.stamp&&window.fcStampSeen)fcStampSeen(r.stamp);}}catch(e){{toast("Couldn't save the whiteboard setting.");}}}}}});
</script>"""
