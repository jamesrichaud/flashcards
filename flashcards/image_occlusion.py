"""Image Occlusion: make and edit notes of Anki's own Image Occlusion note type.

Everything goes through the engine calls desktop Anki's mask editor uses:
add_image_occlusion_notetype, add_image_occlusion_note, get_image_occlusion_note and
update_image_occlusion_note. Masks are stored in the "Occlusion" field in Anki's format
({{c1::image-occlusion:rect:left=.1:top=.2:width=.3:height=.4:oi=1}}), with positions as
fractions of the image size, so the notes work in desktop Anki and AnkiDroid. The masks are
drawn on cards by flashcards/io_review.py.
"""
import hashlib, html, json, math, os, re, shutil, tempfile
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse

import app as core
from flashcards.editor import IMAGE_EXTS, MAX_UPLOAD
from flashcards.i18n import t, tn

router = APIRouter()

IO_STOCK_KIND = 6   # StockNotetype.Kind.KIND_IMAGE_OCCLUSION
# Position keys of each shape, in the order Anki writes them. Polygons get left/top from their points.
GEOM = {"rect": ("left", "top", "width", "height"), "ellipse": ("left", "top", "rx", "ry"),
        "polygon": (), "text": ("left", "top")}
SIZE_KEYS = ("width", "height", "rx", "ry")
PROP_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,20}$")
MAX_SHAPES = 500


def user_or_none(request):
    return core.current_user(request)


def is_io_notetype(col, nt):
    """The engine's own test: it only finds the image/header/back extra fields on Image Occlusion types."""
    if not nt:
        return False
    try:
        col._backend.get_image_occlusion_fields(notetype_id=nt["id"])
        return True
    except Exception:
        return False


def io_notetype(col, ntid=0):
    """The Image Occlusion note type to add with: the one asked for, else the current one if it is
    an IO type, else the stock one (added if missing, as Anki does)."""
    for cand in (col.models.get(ntid) if ntid else None, col.models.current()):
        if cand and is_io_notetype(col, cand):
            return cand
    def find():
        for m in col.models.all_names_and_ids():
            nt = col.models.get(m.id)
            if nt.get("originalStockKind") == IO_STOCK_KIND and is_io_notetype(col, nt):
                return nt
        return None
    nt = find()
    if not nt:
        col.add_image_occlusion_notetype()
        nt = find()
    return nt


def fmt_num(v):
    """Numbers as Anki writes them: 4 decimals, no trailing zeros, no leading zero (.1234)."""
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    if s in ("", "-0"):
        return "0"
    if s.startswith("0."):
        return s[1:]
    if s.startswith("-0."):
        return "-" + s[2:]
    return s


def clean_value(v):
    """Property values can't hold the characters that end a cloze or a property."""
    return re.sub(r"[:{}\r\n]", " ", str(v))[:500].strip()


def to_float(v):
    f = float(v)
    if not math.isfinite(f):
        raise ValueError("bad number")
    return f


def build_occlusions(shapes, hide_all, renumber=False):
    """Shapes from the mask editor -> the Occlusion field text. Raises ValueError if no masks."""
    if not isinstance(shapes, list):
        raise ValueError("bad shapes")
    items = []
    for s in shapes[:MAX_SHAPES]:
        if not isinstance(s, dict) or s.get("shape") not in GEOM:
            continue
        kind = s["shape"]
        try:
            ordinal = int(s.get("ordinal"))
            props = [(k, to_float(s.get(k))) for k in GEOM[kind]]
            points = []
            if kind == "polygon":
                points = [(to_float(p[0]), to_float(p[1])) for p in s.get("points") or []]
        except (TypeError, ValueError, IndexError, KeyError):
            continue
        if not 1 <= ordinal <= 9999:
            continue
        if any(k in SIZE_KEYS and v <= 0 for k, v in props):
            continue
        if kind == "polygon":
            if len(points) < 3:
                continue
            props = [("left", min(x for x, _ in points)), ("top", min(y for _, y in points))]
        text = [(k, fmt_num(v)) for k, v in props]
        if points:
            text.append(("points", " ".join(f"{fmt_num(x)},{fmt_num(y)}" for x, y in points)))
        extra = s.get("extra") if isinstance(s.get("extra"), dict) else {}
        for k, v in list(extra.items())[:20]:
            if PROP_NAME.match(str(k)) and k not in GEOM[kind] and k not in ("left", "top", "points", "oi"):
                val = clean_value(v)
                if val:
                    text.append((k, val))
        if hide_all:
            text.append(("oi", "1"))
        items.append([ordinal, kind, text])
    masks = [it for it in items if it[1] != "text"]
    if not masks:
        raise ValueError("no occlusions")
    if renumber:   # a new note: number the cards 1, 2, 3… in the order they were drawn, keeping groups
        order = {}
        for it in masks:
            order.setdefault(it[0], len(order) + 1)
        for it in masks:
            it[0] = order[it[0]]
    return "<br>".join("{{c%d::image-occlusion:%s:%s}}" % (o, k, ":".join(f"{a}={b}" for a, b in t))
                       for o, k, t in items)


def note_shapes(note):
    """The engine's parsed occlusions -> shapes for the mask editor (positions as numbers)."""
    shapes = []
    for occ in note.occlusions:
        for sh in occ.shapes:
            if sh.shape not in GEOM:
                continue
            props = {p.name: p.value for p in sh.properties}
            d = {"shape": sh.shape, "ordinal": occ.ordinal}
            try:
                for k in GEOM[sh.shape]:
                    d[k] = to_float(props.pop(k, 0) or 0)
                if sh.shape == "polygon":
                    pts = [tuple(to_float(c) for c in p.split(",")[:2]) for p in props.pop("points", "").split()]
                    if len(pts) < 3:
                        continue
                    # Anki draws polygon points shifted so that their top-left corner sits at left/top.
                    dx = to_float(props.pop("left")) - min(x for x, _ in pts) if "left" in props else 0
                    dy = to_float(props.pop("top")) - min(y for _, y in pts) if "top" in props else 0
                    d["points"] = [[x + dx, y + dy] for x, y in pts]
            except (TypeError, ValueError):
                continue
            props.pop("oi", None)
            props.pop("left", None)
            props.pop("top", None)
            d["extra"] = props
            shapes.append(d)
    return shapes


def is_legacy(shapes):
    """Notes from Anki 23.10 stored pixel positions; the editor converts them once the image loads."""
    vals = []
    for s in shapes:
        vals += [s.get(k, 0) for k in ("left", "top", "width", "height", "rx", "ry")]
        vals += [c for p in s.get("points", []) for c in p]
    return any(v > 1.5 for v in vals)


# ---------- page ----------
IO_CSS = """<style>
.io-tools{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin:12px 0}
.io-tools button{min-width:44px;padding:8px 12px}
.io-tools button[aria-pressed=true]{background:var(--accent);border-color:var(--accent);color:#fff}
.io-tools button:disabled{opacity:.45;cursor:default}
.io-sep{width:1px;align-self:stretch;background:var(--line);margin:0 2px}
.io-wrap{text-align:center;background:var(--bg);border:1px solid var(--line);border-radius:12px;padding:8px;overflow:auto}
#io-stage{position:relative;display:inline-block;max-width:100%;line-height:0;user-select:none;-webkit-user-select:none;
-webkit-touch-callout:none;vertical-align:top}
#io-stage img{display:block;max-width:100%;max-height:75vh;width:auto;height:auto;-webkit-user-drag:none;pointer-events:none}
#io-svg{position:absolute;left:0;top:0;width:100%;height:100%;touch-action:none;cursor:crosshair}
#io-svg.sel{touch-action:pan-x pan-y pinch-zoom;cursor:default}
#io-svg .shp{touch-action:none;cursor:move;fill:rgba(255,142,142,.78);stroke:#212121}
#io-svg .shp.txt{fill:#111;stroke:none}
#io-svg .shp.on{fill:rgba(47,111,222,.42);stroke:#2f6fde}
#io-svg.see .shp{fill-opacity:.28}
#io-svg .num{fill:#1d1d1f;font-weight:700;text-anchor:middle;dominant-baseline:central;pointer-events:none;
font-family:-apple-system,system-ui,sans-serif;paint-order:stroke;stroke:#fff;stroke-width:3px}
#io-svg .hdl{fill:#fff;stroke:#2f6fde;touch-action:none;cursor:nwse-resize}
.io-pick{border:2px dashed var(--line);border-radius:14px;padding:26px 16px;text-align:center}
.io-pick.over{border-color:var(--accent);background:rgba(47,111,222,.06)}
.io-pick input[type=file]{width:auto;max-width:100%}
#io-msg:empty{display:none}
</style>"""

IO_JS = r"""<script>(function(){
const C=JSON.parse(document.getElementById('io-conf').textContent),$=id=>document.getElementById(id);
const img=$('io-img'),svg=$('io-svg'),stage=$('io-wrap'),form=$('io-form'),msg=$('io-msg'),NS='http://www.w3.org/2000/svg';
let shapes=[],sel=new Set(),tool='rect',W=1,H=1,undo=[],redo=[],drag=null,file=null,pasted=false,nextId=1,multi=false,dirty=false;
function show(m){msg.textContent=m;msg.className=m?'flash err':'';if(m)msg.scrollIntoView({block:'nearest'});}
function load(list){shapes=list.map(s=>Object.assign({extra:{}},s,{id:nextId++}));}
function plain(){return shapes.map(s=>{const o=Object.assign({},s);delete o.id;return o;});}
function snap(){undo.push(JSON.stringify(shapes));if(undo.length>200)undo.shift();redo=[];dirty=true;}
function back(from,to){if(!from.length)return;to.push(JSON.stringify(shapes));shapes=JSON.parse(from.pop());
 sel=new Set([...sel].filter(id=>shapes.some(s=>s.id===id)));dirty=true;render();}
function maxOrd(){return shapes.reduce((m,s)=>Math.max(m,s.ordinal||0),0);}
function get(id){return shapes.find(s=>s.id===id);}
function cl(v,a,b){return Math.min(b,Math.max(a,v));}
function bbox(s){if(s.shape==='rect')return[s.left,s.top,s.width,s.height];
 if(s.shape==='ellipse')return[s.left,s.top,2*s.rx,2*s.ry];
 if(s.shape==='polygon'){const xs=s.points.map(p=>p[0]),ys=s.points.map(p=>p[1]),x=Math.min(...xs),y=Math.min(...ys);
  return[x,y,Math.max(...xs)-x,Math.max(...ys)-y];}
 return[s.left,s.top,0.12,0.05];}
function mk(tag,at){const e=document.createElementNS(NS,tag);for(const k in at)e.setAttribute(k,at[k]);return e;}
function render(){
 svg.textContent='';const u=W/(svg.getBoundingClientRect().width||W);
 svg.classList.toggle('sel',tool==='select');
 for(const s of shapes){let e;
  if(s.shape==='rect')e=mk('rect',{x:s.left*W,y:s.top*H,width:s.width*W,height:s.height*H});
  else if(s.shape==='ellipse')e=mk('ellipse',{cx:(s.left+s.rx)*W,cy:(s.top+s.ry)*H,rx:s.rx*W,ry:s.ry*H});
  else if(s.shape==='polygon')e=mk('polygon',{points:s.points.map(p=>p[0]*W+','+p[1]*H).join(' ')});
  else{e=mk('text',{x:s.left*W,y:s.top*H,'dominant-baseline':'hanging','font-size':Math.max(12*u,(+s.extra.fs||0.04)*H*(+s.extra.scale||1))});
   e.textContent=s.extra.text||'';}
  e.setAttribute('class','shp'+(s.shape==='text'?' txt':'')+(sel.has(s.id)?' on':''));
  e.setAttribute('vector-effect','non-scaling-stroke');e.setAttribute('stroke-width',sel.has(s.id)?3:1.5);
  e.dataset.id=s.id;svg.appendChild(e);}
 for(const s of shapes){if(s.shape==='text')continue;const b=bbox(s);
  const t=mk('text',{class:'num',x:(b[0]+b[2]/2)*W,y:(b[1]+b[3]/2)*H,'font-size':16*u});t.textContent=s.ordinal;svg.appendChild(t);}
 const one=sel.size===1?get([...sel][0]):null;
 if(one&&(one.shape==='rect'||one.shape==='ellipse')){const b=bbox(one),z=26*u;
  const h=mk('rect',{class:'hdl',x:(b[0]+b[2])*W-z/2,y:(b[1]+b[3])*H-z/2,width:z,height:z,rx:4*u});
  h.setAttribute('vector-effect','non-scaling-stroke');h.setAttribute('stroke-width',2);h.dataset.id=one.id;h.dataset.hdl='1';
  svg.appendChild(h);}
 document.querySelectorAll('[data-tool]').forEach(b=>b.setAttribute('aria-pressed',b.dataset.tool===tool));
 const q=s=>document.querySelector('[data-act="'+s+'"]');
 q('delete').disabled=q('dup').disabled=!sel.size;q('group').disabled=sel.size<2;
 q('ungroup').disabled=!shapes.some(s=>sel.has(s.id)&&shapes.some(o=>o!==s&&o.ordinal===s.ordinal));
 q('undo').disabled=!undo.length;q('redo').disabled=!redo.length;q('multi').setAttribute('aria-pressed',multi);
 const n=new Set(shapes.filter(s=>s.shape!=='text').map(s=>s.ordinal)).size;
 $('io-count').textContent=n?cardsText(n):'';}
function cardsText(n){const P=C.msgs.cards,f=P.forms[+P.idx[n<P.idx.length?n:100+n%100]];return f.split('{count}').join(n);}
function setTool(t){tool=t;if(t!=='select')sel.clear();render();}
function pt(e){const r=svg.getBoundingClientRect();return{x:cl((e.clientX-r.left)/r.width,0,1),y:cl((e.clientY-r.top)/r.height,0,1)};}
function groupIds(id){const s=get(id);return shapes.filter(o=>o.ordinal===s.ordinal).map(o=>o.id);}
function copy(s){return JSON.parse(JSON.stringify(s));}
svg.addEventListener('pointerdown',e=>{
 if(!W||e.button>0)return;const p=pt(e),t=e.target,id=+(t.dataset&&t.dataset.id||0);
 if(t.dataset&&t.dataset.hdl){const s=get(id);snap();drag={m:'size',s,o:copy(s),p};}
 else if(tool==='select'||(id&&e.target.classList.contains('on'))){
  if(id){const g=groupIds(id);
   if(multi||e.shiftKey||e.ctrlKey||e.metaKey){const on=sel.has(id);g.forEach(x=>on?sel.delete(x):sel.add(x));}
   else if(!sel.has(id))sel=new Set(g);
   if(sel.has(id)){snap();drag={m:'move',p,o:shapes.filter(s=>sel.has(s.id)).map(copy),moved:false};}}
  else if(!multi)sel.clear();}
 else{snap();const s={id:nextId++,shape:tool,ordinal:maxOrd()+1,left:p.x,top:p.y,extra:{}};
  if(tool==='rect'){s.width=0;s.height=0;}else{s.rx=0;s.ry=0;}
  shapes.push(s);sel=new Set([s.id]);drag={m:'draw',s,p};}
 if(drag){try{svg.setPointerCapture(e.pointerId);}catch(_){}e.preventDefault();}
 render();});
svg.addEventListener('pointermove',e=>{if(!drag)return;const p=pt(e);e.preventDefault();
 if(drag.m==='draw'){const s=drag.s,x0=Math.min(drag.p.x,p.x),y0=Math.min(drag.p.y,p.y),w=Math.abs(p.x-drag.p.x),h=Math.abs(p.y-drag.p.y);
  s.left=x0;s.top=y0;if(s.shape==='rect'){s.width=w;s.height=h;}else{s.rx=w/2;s.ry=h/2;}}
 else if(drag.m==='size'){const s=drag.s,w=cl(p.x-s.left,0.005,1-s.left),h=cl(p.y-s.top,0.005,1-s.top);
  if(s.shape==='rect'){s.width=w;s.height=h;}else{s.rx=w/2;s.ry=h/2;}}
 else{let dx=p.x-drag.p.x,dy=p.y-drag.p.y;
  for(const o of drag.o){const b=bbox(o);dx=cl(dx,-b[0],1-b[0]-b[2]);dy=cl(dy,-b[1],1-b[1]-b[3]);}
  for(const o of drag.o){const s=get(o.id);if(!s)continue;
   if(s.shape==='polygon')s.points=o.points.map(q=>[q[0]+dx,q[1]+dy]);else{s.left=o.left+dx;s.top=o.top+dy;}}
  if(Math.abs(dx)+Math.abs(dy)>0.002)drag.moved=true;}
 render();});
function endDrag(){if(!drag)return;
 if(drag.m==='draw'){const b=bbox(drag.s);if(b[2]<0.006||b[3]<0.006){shapes=shapes.filter(s=>s!==drag.s);sel.clear();undo.pop();}}
 else if(drag.m==='move'&&!drag.moved)undo.pop();
 drag=null;render();}
svg.addEventListener('pointerup',endDrag);svg.addEventListener('pointercancel',endDrag);
function act(a){
 if(a==='undo')return back(undo,redo);if(a==='redo')return back(redo,undo);
 if(a==='multi'){multi=!multi;if(multi)tool='select';return render();}
 if(a==='see'){svg.classList.toggle('see');return document.querySelector('[data-act=see]').setAttribute('aria-pressed',svg.classList.contains('see'));}
 if(a==='all'){tool='select';sel=new Set(shapes.map(s=>s.id));return render();}
 if(!sel.size||(a==='group'&&sel.size<2))return;snap();
 if(a==='delete'){shapes=shapes.filter(s=>!sel.has(s.id));sel.clear();}
 else if(a==='group'){const o=Math.min(...shapes.filter(s=>sel.has(s.id)).map(s=>s.ordinal));
  shapes.forEach(s=>{if(sel.has(s.id))s.ordinal=o;});}
 else if(a==='ungroup'){const seen=new Set();let n=maxOrd();
  shapes.forEach(s=>{if(!sel.has(s.id))return;if(seen.has(s.ordinal)||shapes.some(o=>!sel.has(o.id)&&o.ordinal===s.ordinal))s.ordinal=++n;else seen.add(s.ordinal);});}
 else if(a==='dup'){const map={},add=[];let n=maxOrd();
  for(const s of shapes.filter(s=>sel.has(s.id))){const c=copy(s);c.id=nextId++;c.ordinal=map[s.ordinal]=map[s.ordinal]||++n;
   const b=bbox(c),dx=Math.min(0.03,1-b[0]-b[2]),dy=Math.min(0.03,1-b[1]-b[3]);
   if(c.shape==='polygon')c.points=c.points.map(q=>[q[0]+dx,q[1]+dy]);else{c.left+=dx;c.top+=dy;}add.push(c);}
  shapes=shapes.concat(add);sel=new Set(add.map(s=>s.id));}
 render();}
document.querySelectorAll('[data-tool]').forEach(b=>b.addEventListener('click',()=>setTool(b.dataset.tool)));
document.querySelectorAll('[data-act]').forEach(b=>b.addEventListener('click',()=>act(b.dataset.act)));
document.addEventListener('keydown',e=>{
 const tg=e.target.tagName,mod=e.ctrlKey||e.metaKey;
 if(mod&&e.key==='Enter'){e.preventDefault();form.requestSubmit();return;}
 if(tg==='INPUT'||tg==='TEXTAREA'||tg==='SELECT'||$('io-editor').hidden)return;
 const k=e.key.toLowerCase();let a=null;
 if(mod&&k==='z')a=e.shiftKey?'redo':'undo';else if(mod&&k==='y')a='redo';else if(mod&&k==='g')a=e.shiftKey?'ungroup':'group';
 else if(mod&&k==='a')a='all';else if(mod&&k==='d')a='dup';else if(!mod&&(e.key==='Delete'||e.key==='Backspace'))a='delete';
 if(a){e.preventDefault();return act(a);}
 if(mod||e.altKey)return;
 const t={s:'select',v:'select',r:'rect',e:'ellipse'}[k];if(t){e.preventDefault();return setTool(t);}
 if(e.key==='Escape'){sel.clear();return render();}
 const d={ArrowLeft:[-1,0],ArrowRight:[1,0],ArrowUp:[0,-1],ArrowDown:[0,1]}[e.key];
 if(d&&sel.size){e.preventDefault();snap();const st=e.shiftKey?0.02:0.004;let dx=d[0]*st,dy=d[1]*st;
  const ss=shapes.filter(s=>sel.has(s.id));for(const s of ss){const b=bbox(s);dx=cl(dx,-b[0],1-b[0]-b[2]);dy=cl(dy,-b[1],1-b[1]-b[3]);}
  ss.forEach(s=>{if(s.shape==='polygon')s.points=s.points.map(q=>[q[0]+dx,q[1]+dy]);else{s.left+=dx;s.top+=dy;}});render();}});
img.addEventListener('load',()=>{W=img.naturalWidth||1;H=img.naturalHeight||1;svg.setAttribute('viewBox','0 0 '+W+' '+H);
 if(C.legacy){C.legacy=false;shapes.forEach(s=>{if(s.shape==='polygon')s.points=s.points.map(p=>[p[0]/W,p[1]/H]);
  else{s.left/=W;s.top/=H;['width','rx'].forEach(k=>{if(k in s)s[k]/=W;});['height','ry'].forEach(k=>{if(k in s)s[k]/=H;});}});}
 $('io-editor').hidden=false;show('');render();});
img.addEventListener('error',()=>{if(img.getAttribute('src'))show(C.msgs.badImage);});
window.addEventListener('resize',()=>render());
function useFile(f,fromPaste){
 if(!f)return;if(!/^image\//.test(f.type)&&!/\.(png|jpe?g|gif|webp|svg|bmp|avif|tiff?|ico)$/i.test(f.name||'')){show(C.msgs.notImage);return;}
 if(shapes.length&&!confirm(C.msgs.replace))return;
 file=f;pasted=!!fromPaste;shapes=[];sel.clear();undo=[];redo=[];dirty=true;tool='rect';
 if(img.src&&img.src.startsWith('blob:'))URL.revokeObjectURL(img.src);img.src=URL.createObjectURL(f);
 $('io-pickname').textContent=f.name&&!fromPaste?f.name:'';}
if(C.adding){const fi=$('io-file'),pick=$('io-pick');
 fi.addEventListener('change',()=>{useFile(fi.files[0],false);fi.value='';});
 document.addEventListener('paste',e=>{const t=e.target.tagName;if(t==='INPUT'||t==='TEXTAREA')return;
  const f=[...(e.clipboardData&&e.clipboardData.files||[])].find(f=>f.type.startsWith('image/'));if(f){e.preventDefault();useFile(f,true);}});
 const pb=$('io-paste');
 if(navigator.clipboard&&navigator.clipboard.read&&window.isSecureContext)pb.addEventListener('click',async()=>{
  try{for(const it of await navigator.clipboard.read()){const ty=it.types.find(x=>x.startsWith('image/'));
    if(ty){const b=await it.getType(ty);return useFile(new File([b],'paste.'+ty.split('/')[1],{type:ty}),true);}}
   show(C.msgs.noClipImage);}catch(err){show(C.msgs.pasteKeys);}});
 else pb.addEventListener('click',()=>show(C.msgs.pasteKeys));
 ['dragenter','dragover'].forEach(n=>pick.addEventListener(n,e=>{e.preventDefault();pick.classList.add('over');}));
 ['dragleave','drop'].forEach(n=>pick.addEventListener(n,()=>pick.classList.remove('over')));
 pick.addEventListener('drop',e=>{e.preventDefault();useFile(e.dataTransfer.files[0],false);});}
else{load(C.shapes);img.src=C.image;}
if(img.complete&&img.naturalWidth)img.dispatchEvent(new Event('load'));
form.querySelectorAll('input,textarea,select').forEach(el=>el.addEventListener('input',()=>dirty=true));
window.addEventListener('beforeunload',e=>{if(dirty){e.preventDefault();e.returnValue='';}});
form.addEventListener('submit',async e=>{e.preventDefault();
 if(C.adding&&!file)return show(C.msgs.noImage);
 if(!shapes.some(s=>s.shape!=='text'))return show(C.msgs.noOcclusion);
 const fd=new FormData(form);fd.set('shapes',JSON.stringify(plain()));
 if(C.adding){fd.set('image',file,file.name||'paste.png');if(pasted)fd.set('pasted','1');}
 const btn=$('io-save');btn.disabled=true;show('');
 try{const r=await fetch(form.action,{method:'POST',body:fd,headers:{'X-Requested-With':'fetch'}});
  const j=await r.json();if(j.ok){dirty=false;location.href=j.url;return;}show(j.msg||C.msgs.failed);}
 catch(err){show(C.msgs.offline);}
 btn.disabled=false;});
render();
})();</script>"""


def plural_table(form):
    """Plural forms for page JavaScript: {"forms": [...], "idx": "0111..."}; idx[k] picks the form
    for k (k < 200; larger numbers use 100 + k % 100, which gives the same form in every language
    here). `form(k)` returns the text for k with "{count}" left in for the number."""
    forms, idx = [], []
    for k in range(200):
        s = form(k)
        if s not in forms:
            forms.append(s)
        idx.append(str(forms.index(s)))
    return {"forms": forms, "idx": "".join(idx)}


def tool_bar(col):
    tr = col.tr
    def b(attr, label, text, key=""):
        tip = html.escape(label + (f" ({key})" if key else ""))
        return f'<button type="button" {attr} title="{tip}" aria-label="{html.escape(label)}">{text}</button>'
    return (f'<div class="io-tools" role="toolbar" aria-label="{html.escape(t("Mask tools"))}">'
            + b('data-tool="select"', tr.editing_image_occlusion_select_tool(), "↖ " + html.escape(t("Select")), "S")
            + b('data-tool="rect"', tr.editing_image_occlusion_rectangle_tool(), "▭ " + html.escape(t("Rectangle")), "R")
            + b('data-tool="ellipse"', tr.editing_image_occlusion_ellipse_tool(), "◯ " + html.escape(t("Ellipse")), "E")
            + '<span class="io-sep"></span>'
            + b('data-act="undo"', t("Undo"), "↶", "Ctrl+Z") + b('data-act="redo"', t("Redo"), "↷", "Ctrl+Y")
            + b('data-act="delete"', tr.editing_image_occlusion_delete(), "🗑", "Delete")
            + b('data-act="dup"', tr.editing_image_occlusion_duplicate(), "⧉", "Ctrl+D")
            + b('data-act="group"', tr.editing_image_occlusion_group(), html.escape(t("Group")), "Ctrl+G")
            + b('data-act="ungroup"', tr.editing_image_occlusion_ungroup(), html.escape(t("Ungroup")), "Ctrl+Shift+G")
            + b('data-act="all"', tr.editing_image_occlusion_select_all(), html.escape(t("All")), "Ctrl+A")
            + b('data-act="multi" aria-pressed="false"', t("Select several by tapping"), "+ " + html.escape(t("Multi")))
            + b('data-act="see" aria-pressed="false"', tr.editing_image_occlusion_toggle_translucent(), "◐")
            + '<span class="muted" id="io-count" style="margin-left:auto;font-size:14px"></span></div>')


def editor_html(col, conf, *, action, mode_all, header, back_extra, tags, top="", bottom="", save=None):
    tr = col.tr
    save = t("Add") if save is None else save
    conf["msgs"] = {
        "noImage": t("Choose an image first."),
        "noOcclusion": tr.notetypes_no_occlusion_created(),
        "notImage": t("Please choose a picture (PNG, JPEG, GIF, WebP, SVG…)."),
        "badImage": t("The image could not be shown."),
        "replace": t("Use the new image? The masks drawn so far will be removed."),
        "noClipImage": t("There is no image on the clipboard."),
        "pasteKeys": t("Press Ctrl+V (⌘V on iPad) to paste an image."),
        "failed": t("That did not work."),
        "offline": t("Could not reach the server. Check the connection and try again."),
        "cards": plural_table(lambda k: tn("{count} card", "{count} cards", k, count="{count}")),
    }
    mode_opts = (f'<option value="all" {"selected" if mode_all else ""}>{html.escape(tr.notetypes_hide_all_guess_one())}</option>'
                 f'<option value="one" {"" if mode_all else "selected"}>{html.escape(tr.notetypes_hide_one_guess_one())}</option>')
    return f"""{IO_CSS}<form id="io-form" method="post" action="{html.escape(action)}" enctype="multipart/form-data">
{top}<div id="io-msg" role="alert"></div>
<div id="io-editor" hidden>{tool_bar(col)}
<div class="io-wrap" id="io-wrap"><div id="io-stage"><img id="io-img" alt="{html.escape(t("Image to occlude"))}">
<svg id="io-svg" xmlns="http://www.w3.org/2000/svg" preserveAspectRatio="none" role="application"
aria-label="{html.escape(t("Draw masks on the image"))}"></svg></div></div>
<p class="muted" style="font-size:14px;margin:8px 0 0">{html.escape(t("Draw a box or ellipse over each part to hide. Each number is one card;\n"
                "group shapes to hide them together on one card. Select to move; drag the corner square to resize."))}</p></div>
<label for="io-mode">{html.escape(tr.editing_image_occlusion_mode())}</label>
<select id="io-mode" name="mode">{mode_opts}</select>
<label for="io-header">{html.escape(tr.notetypes_header())}</label>
<textarea id="io-header" name="header" style="min-height:60px">{html.escape(header)}</textarea>
<label for="io-back">{html.escape(tr.notetypes_back_extra_field())}</label>
<textarea id="io-back" name="back_extra" style="min-height:60px">{html.escape(back_extra)}</textarea>
<label for="io-tags">{html.escape(t("Tags (space separated)"))}</label><input id="io-tags" name="tags" value="{html.escape(tags)}" autocapitalize="off">
<p class="row">{bottom}<button class="primary" id="io-save" title="{html.escape(save)} (Ctrl+Enter)">{html.escape(save)}</button></p>
<noscript><p class="flash err">{html.escape(t("Drawing masks needs JavaScript."))}</p></noscript></form>
<script type="application/json" id="io-conf">{core.js(conf)}</script>{IO_JS}"""


# ---------- routes ----------
@router.get("/io/new", response_class=HTMLResponse)
def io_new_page(request: Request, nt: int = 0, did: int = 0):
    user = user_or_none(request)
    if not user: return core.need_login(request)
    with core.collection(user) as col:
        model = io_notetype(col, nt)
        did = did or col.get_config("curDeck", 1)
        decks = sorted(col.decks.all_names_and_ids(include_filtered=False), key=lambda d: d.name)
        if not any(d.id == did for d in decks):
            did = decks[0].id if decks else 1
        deck_opts = "".join(f'<option value="{d.id}" {"selected" if d.id == did else ""}>{html.escape(d.name)}</option>'
                            for d in decks)
        io_types = [m for m in col.models.all_names_and_ids() if is_io_notetype(col, col.models.get(m.id))]
        nt_opts = "".join(f'<option value="{m.id}" {"selected" if m.id == model["id"] else ""}>{html.escape(m.name)}</option>'
                          for m in io_types)
        tr = col.tr
        top = f"""<div class="row"><div style="flex:1;min-width:180px"><label for="io-nt">{html.escape(t("Type"))}</label>
<select id="io-nt" name="ntid">{nt_opts}</select></div>
<div style="flex:1;min-width:180px"><label for="io-deck">{html.escape(t("Deck"))}</label><select id="io-deck" name="did">{deck_opts}</select></div></div>
<div class="io-pick" id="io-pick" style="margin-top:14px"><p style="margin-top:0">{html.escape(t("Choose a picture, drop one here, or paste it."))}</p>
<div class="row" style="justify-content:center"><label class="btn primary" style="margin:0;color:#fff;position:relative;overflow:hidden">{html.escape(tr.notetypes_io_select_image())}
<input type="file" id="io-file" accept="image/*" style="position:absolute;width:1px;height:1px;opacity:0;padding:0;border:0"></label>
<button type="button" id="io-paste">{html.escape(tr.notetypes_io_paste_image_from_clipboard())}</button></div>
<p class="muted" id="io-pickname" style="margin-bottom:0;font-size:14px"></p></div>"""
        conf = {"adding": True, "shapes": [], "image": "", "legacy": False}
        form = editor_html(col, conf, action="/io/new", mode_all=True, header="", back_extra="", tags="", top=top,
                           bottom=f'<a class="btn" href="/add">{html.escape(t("Other note types"))}</a>', save=t("Add"))
        title = tr.notetypes_image_occlusion_name()
    body = f"""<div class="box"><div class="row" style="justify-content:space-between"><h2 style="margin:0">{html.escape(title)}</h2>
<a class="btn" href="/notetypes/{model['id']}/cards">{html.escape(t("Cards…"))}</a></div>{form}</div>"""
    return core.page(title, body + '<span data-noundokey hidden></span>', user, "add", core.take_flash(request))


def reply(request, ok, msg, url):
    """fetch() from the editor gets JSON; a plain form post gets a redirect with the message."""
    if request.headers.get("x-requested-with") == "fetch":
        if ok:
            core.set_flash(request, msg)
        return JSONResponse({"ok": ok, "msg": msg, "url": url}, 200 if ok else 400)
    core.set_flash(request, msg if ok else "!" + msg)
    return RedirectResponse(url, 303)


def parse_shapes(form):
    raw = str(form.get("shapes", "") or "")
    if len(raw) > 500_000:
        raise ValueError(t("Too many masks."))
    try:
        return json.loads(raw or "[]")
    except ValueError:
        raise ValueError(t("The masks could not be read."))


def clean_tags(s):
    return str(s or "").split()


@router.post("/io/new")
async def io_add(request: Request):
    user = user_or_none(request)
    if not user: return core.need_login(request)
    form = await request.form()
    try:
        did = int(form.get("did") or 0)
        ntid = int(form.get("ntid") or 0)
    except ValueError:
        did = ntid = 0
    back = f"/io/new?nt={ntid}&did={did}" if ntid else "/io/new"
    up = form.get("image")
    if up is None or not hasattr(up, "read"):
        return reply(request, False, t("Choose an image first."), back)
    data = await up.read()
    fname = os.path.basename((up.filename or "").replace("\\", "/")).strip()
    fname = re.sub(r'[\x00-\x1f"<>:/\\|?*\[\]]', "", fname)[:120] or "image.png"
    ext = os.path.splitext(fname)[1].lower()
    ctype = (up.content_type or "").split(";")[0].lower()
    if not data:
        return reply(request, False, t("The image file is empty."), back)
    if len(data) > MAX_UPLOAD:
        return reply(request, False, t("The image is larger than 100 MB."), back)
    if ext not in IMAGE_EXTS:
        if not ctype.startswith("image/"):
            return reply(request, False, t("Please choose a picture (PNG, JPEG, GIF, WebP, SVG…)."), back)
        ext = "." + {"jpeg": "jpg", "svg+xml": "svg"}.get(ctype.split("/")[1], ctype.split("/")[1])
        fname += ext
    if form.get("pasted") == "1":   # named like Anki names pasted pictures
        fname = f"paste-{hashlib.sha1(data).hexdigest()}{ext}"
    with core.collection(user) as col:
        nt = col.models.get(ntid) if ntid else None
        if not is_io_notetype(col, nt):
            return reply(request, False, t("Choose an Image Occlusion note type."), "/io/new")
        deck = col.decks.get(did, default=False)
        if not deck or deck.get("dyn"):
            return reply(request, False, t("Choose a normal deck."), back)
        try:
            occ = build_occlusions(parse_shapes(form), form.get("mode") != "one", renumber=True)
        except ValueError as e:
            msg = col.tr.notetypes_no_occlusion_created() if str(e) == "no occlusions" else str(e)
            return reply(request, False, msg, back)
        tmp = tempfile.mkdtemp()
        try:
            path = os.path.join(tmp, fname)
            with open(path, "wb") as fh:
                fh.write(data)
            core.select_deck(col, did)   # the engine adds to the current deck
            before = col.note_count()
            col.add_image_occlusion_note(notetype_id=nt["id"], image_path=path, occlusions=occ,
                                         header=str(form.get("header", "")), back_extra=str(form.get("back_extra", "")),
                                         tags=clean_tags(form.get("tags")))
        except Exception as e:
            return reply(request, False, f"{col.tr.notetypes_error_generating_cloze()}: {e}", back)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        added = col.note_count() - before
        ncards = len(set(re.findall(r"\{\{c(\d+)::image-occlusion:(?!text:)", occ)))
        col.models.set_current(nt)
        col.set_config("curDeck", did)
    msg = tn("Added {n} card.", "Added {n} cards.", ncards) if added else t("Nothing was added.")
    return reply(request, bool(added), msg, f"/io/new?nt={nt['id']}&did={did}")


def load_io_note(col, nid):
    try:
        resp = col.get_image_occlusion_note(nid)
    except Exception:
        return None
    return resp.note if resp.WhichOneof("value") == "note" else None


@router.get("/io/{nid}", response_class=HTMLResponse)
def io_edit_page(request: Request, nid: int, back: str = "/browse"):
    user = user_or_none(request)
    if not user: return core.need_login(request)
    back = core.safe_back(back)
    qback = quote(back, safe="/")
    with core.collection(user) as col:
        n = load_io_note(col, nid)
        if n is None:
            try:
                col.get_note(nid)
            except Exception:
                core.set_flash(request, "!" + t("That note no longer exists."))
                return RedirectResponse(back, 303)
            core.set_flash(request, "!" + t("That is not an Image Occlusion note."))
            return RedirectResponse(f"/edit/{nid}?raw=1&back={qback}", 303)
        note = col.get_note(nid)
        shapes = note_shapes(n)
        conf = {"adding": False, "shapes": shapes, "image": "/media/" + quote(n.image_file_name),
                "legacy": is_legacy(shapes), "nid": nid}
        here = f"/io/{nid}?back={qback}"
        form = editor_html(col, conf, action=here, mode_all=n.occlude_inactive, header=n.header,
                           back_extra=n.back_extra, tags=" ".join(note.tags),
                           bottom=f'<a class="btn" href="{html.escape(back)}">{html.escape(t("Cancel"))}</a>',
                           save=t("Save"))
        cards_box = core.cards_panel(col, note, here)
        ntid = note.mid
        title = col.tr.notetypes_image_occlusion_name()
    body = f"""<div class="box"><h2 style="margin-top:0">{html.escape(t("Edit {name}", name=title))}</h2>{form}
<form method="post" action="/note/{nid}/delete?back={html.escape(qback)}" onsubmit="return confirm({html.escape(core.js(t('Delete this note and its cards?')))})">
<button class="danger">{html.escape(t("Delete note"))}</button></form>
<p class="row" style="margin-bottom:0"><a class="btn" href="/edit/{nid}?raw=1&amp;back={html.escape(qback)}">{html.escape(t("Edit fields as text"))}</a>
<a class="btn" href="/notetypes/{ntid}/cards?nid={nid}">{html.escape(t("Cards…"))}</a></p></div>{cards_box}"""
    return core.page(t("Edit"), body + '<span data-noundokey hidden></span>', user, "browse", core.take_flash(request))


@router.post("/io/{nid}")
async def io_edit(request: Request, nid: int, back: str = "/browse"):
    user = user_or_none(request)
    if not user: return core.need_login(request)
    back = core.safe_back(back)
    here = f"/io/{nid}?back={quote(back, safe='/')}"
    form = await request.form()
    with core.collection(user) as col:
        if load_io_note(col, nid) is None:
            return reply(request, False, t("That Image Occlusion note no longer exists."), back)
        try:
            occ = build_occlusions(parse_shapes(form), form.get("mode") != "one")
        except ValueError as e:
            msg = col.tr.notetypes_no_occlusion_created() if str(e) == "no occlusions" else str(e)
            return reply(request, False, msg, here)
        try:
            col.update_image_occlusion_note(note_id=nid, occlusions=occ, header=str(form.get("header", "")),
                                            back_extra=str(form.get("back_extra", "")), tags=clean_tags(form.get("tags")))
        except Exception as e:
            return reply(request, False, str(e) or t("That did not work."), here)
    return reply(request, True, t("Saved."), back)
