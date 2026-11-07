"""Add and Edit: Anki's note editor in the browser.

A formatting toolbar over contenteditable fields (bold/italic/underline, sub/superscript, text
and highlight colour, lists, remove formatting, cloze, MathJax), an HTML editor per field,
pasted/dropped/attached images, audio recording, sticky fields, the duplicate check and a
preview. The engine does the real work: `Note.fields_check` (duplicates, empty first field,
cloze problems, the same check as Anki's editor and Add window), a `dupe:` search built by
`build_search_string` for "Show Duplicates", `Note.ephemeral_card` for the preview (what
Anki's previewer uses for unsaved notes) and `col.media.write_data` for media files.
"""
import hashlib, html, os
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse

from anki.collection import SearchNode
from anki.consts import MODEL_CLOZE
from anki.notes import NoteFieldsCheckResult as Check

import app as core
from flashcards.i18n import t, tn
from flashcards import addons  # card_render hook for previews

router = APIRouter()

# Sticky field values and tags kept between adds (Anki keeps them in the open Add window),
# and the input of an add that was refused, so the form comes back filled in.
_sticky = {}   # (user, ntid) -> {"fields": {index: html}, "tags": str}
_drafts = {}   # user -> {"ntid", "did", "fields": {index: html}, "tags", "confirm"}

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".avif", ".tif", ".tiff", ".ico")
AV_EXTS = (".mp3", ".m4a", ".aac", ".ogg", ".oga", ".opus", ".wav", ".flac", ".webm", ".3gp", ".amr",
           ".spx") + core.VIDEO_EXTS
MAX_UPLOAD = 100 * 1024 * 1024
STATES = {Check.NORMAL: "normal", Check.EMPTY: "empty", Check.DUPLICATE: "duplicate",
          Check.MISSING_CLOZE: "missing_cloze", Check.NOTETYPE_NOT_CLOZE: "notetype_not_cloze",
          Check.FIELD_NOT_CLOZE: "field_not_cloze"}


def user_or_none(request):
    return core.current_user(request)


def note_check(col, note):
    """The engine's field check, with Anki's own messages."""
    st = note.fields_check()
    tr = col.tr
    msg, dupes = "", ""
    if st == Check.DUPLICATE:
        q = col.build_search_string(SearchNode(dupe=SearchNode.Dupe(notetype_id=note.mid,
                                                                    first_field=note.fields[0])))
        dupes = "/browse?" + urlencode({"q": q})
        msg = tr.editing_show_duplicates()
    elif st == Check.EMPTY:
        msg = tr.adding_the_first_field_is_empty()
    elif st == Check.MISSING_CLOZE:
        msg = tr.adding_you_have_a_cloze_deletion_note()
    elif st == Check.NOTETYPE_NOT_CLOZE:
        msg = tr.adding_cloze_outside_cloze_notetype()
    elif st == Check.FIELD_NOT_CLOZE:
        msg = tr.adding_cloze_outside_cloze_field()
    return {"state": STATES.get(st, "normal"), "msg": msg, "dupes": dupes}


def fill_note(note, model, form):
    for i, f in enumerate(model["flds"]):
        if f"f{i}" in form:
            note[f["name"]] = str(form.get(f"f{i}", ""))


def form_note(col, form):
    """An unsaved note built from the editor's form (an existing note when `nid` is given)."""
    nid = int(form.get("nid", 0) or 0)
    if nid:
        note = col.get_note(nid)
    else:
        note = col.new_note(col.models.get(int(form["ntid"])))
    fill_note(note, note.note_type(), form)
    return note


# ---------- the form ----------
EDITOR_CSS = """<style>
#noteform .tb{display:none}#noteform.ed-on .tb{display:flex}
.tb{flex-wrap:wrap;gap:4px;align-items:center;padding:6px;margin:12px 0 4px;border:1px solid var(--line);
border-radius:12px;background:var(--card);position:sticky;top:0;z-index:4}
.tb button,.tb .sw{min-width:44px;min-height:44px;padding:6px 10px;font-size:16px;border-radius:9px}
.tb .sep{width:1px;align-self:stretch;background:var(--line);margin:4px 2px}
.tb .sw{display:inline-flex;align-items:center;gap:2px;border:1px solid var(--line);padding:0 0 0 4px}
.tb .sw button{border:0;min-width:44px;padding:6px}
.tb input[type=color]{width:44px;height:44px;padding:0;border:0;background:none;border-radius:6px;cursor:pointer}
.tb button.recording{background:var(--again);border-color:var(--again);color:#fff}
#ed-msg{font-size:14px;color:var(--muted);min-height:20px;margin:2px 0 0}
#ed-msg.err{color:var(--again);background:none}
.fld{margin-top:10px}.fhead{display:flex;align-items:center;gap:6px;margin:12px 0 6px}
.fname{border:0;background:none;padding:6px 2px;min-height:44px;color:var(--muted);font-size:14px;margin-right:auto;
text-align:left;cursor:pointer}.fname:before{content:"▾ ";display:inline-block;width:1.1em}
.fld.collapsed .fname:before{content:"▸ "}.fld.collapsed .rich,.fld.collapsed .htmled{display:none!important}
.fhead .fb{display:none;min-width:44px;min-height:44px;padding:4px 10px;font-size:15px}
#noteform.ed-on .fhead .fb{display:inline-block}
.fb[aria-pressed=true]{background:var(--accent);border-color:var(--accent);color:#fff}
.rich{min-height:52px;padding:11px 12px;border:1px solid var(--line);border-radius:10px;background:var(--bg);
color:var(--ink);overflow-wrap:anywhere;outline:none;cursor:text;line-height:1.4}
.rich:focus{border-color:var(--accent);box-shadow:0 0 0 2px rgba(47,111,222,.18)}
.rich:empty:before{content:attr(data-placeholder);color:var(--muted)}
.rich img{max-width:100%;height:auto}.rich ul,.rich ol{margin:4px 0;padding-left:26px}
.fld.dupe .rich,.fld.dupe .htmled{border-color:var(--again);background:rgba(214,69,69,.08)}
#noteform.ed-on .htmled{display:none;margin-top:6px}#noteform.ed-on .fld.showhtml .htmled{display:block}
.htmled{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:14px;min-height:70px}
.dupnote{font-size:14px;color:var(--again);margin:6px 0 0}.dupnote a{color:var(--again);font-weight:600}
.warnnote{font-size:14px;color:var(--hard);margin:6px 0 0}
#ed-preview .pvtabs{display:flex;gap:6px;flex-wrap:wrap;margin:8px 0}
#ed-preview iframe{width:100%;min-height:120px;border:1px solid var(--line);border-radius:12px;background:var(--card)}
.confirmbox{border:1px solid var(--hard);border-radius:12px;padding:12px 14px;margin:12px 0}
</style>"""

EDITOR_JS = r"""<script>
(function(){
const F=document.getElementById('noteform');if(!F||!window.getSelection)return;
const C=JSON.parse(document.getElementById('ed-conf').textContent);
F.classList.add('ed-on');
const hdr=document.querySelector('header'),tbEl=F.querySelector('.tb');
function tbTop(){if(hdr&&getComputedStyle(hdr).position==='sticky')tbEl.style.top=hdr.offsetHeight+'px';}
tbTop();window.addEventListener('resize',tbTop);
const flds=[...F.querySelectorAll('.fld')],msg=document.getElementById('ed-msg');
let last=null,saved=null,shiftPaste=false,chord=0,checkT=null,pvT=null,dirty=false;
function say(t,err){msg.textContent=t||'';msg.className=err?'err':'';}
const ABS=/^([a-z][a-z0-9+.-]*:|\/)/i;
// Field HTML -> what the rich editor shows: media paths point at /media/, scripts and
// event handlers are left out (they are kept in the field unless that field is edited here).
function toDisplay(h){const d=new DOMParser().parseFromString('<body>'+h,'text/html');
 d.body.querySelectorAll('script,style,iframe,object,embed,link,meta').forEach(e=>e.remove());
 d.body.querySelectorAll('*').forEach(e=>{[...e.attributes].forEach(a=>{const n=a.name.toLowerCase();
  if(n.startsWith('on')||((n==='href'||n==='src')&&/^\s*javascript:/i.test(a.value)))e.removeAttribute(a.name);});
  if(e.tagName==='IMG'){const s=e.getAttribute('src');if(s&&!ABS.test(s))e.setAttribute('src','/media/'+s);}});
 return d.body.innerHTML;}
function serialize(rich){const t=document.createElement('template');t.innerHTML=rich.innerHTML;
 t.content.querySelectorAll('img[src^="/media/"]').forEach(im=>im.setAttribute('src',im.getAttribute('src').slice(7)));
 let h=t.innerHTML;if(h==='<br>'||h==='<div><br></div>')h='';return h;}
// Pasted HTML is cut down to basic formatting (Anki filters pasted HTML too); Shift keeps plain text.
const KEEP=new Set(['B','I','U','STRONG','EM','SUB','SUP','BR','DIV','P','UL','OL','LI','IMG','TABLE','THEAD','TBODY','TR','TD','TH']);
const DROP=new Set(['SCRIPT','STYLE','META','TITLE','HEAD','LINK','IFRAME','OBJECT','EMBED','svg','SVG','TEMPLATE']);
function clean(h){const d=new DOMParser().parseFromString(h,'text/html');
 (function walk(n){[...n.childNodes].forEach(c=>{
  if(c.nodeType===8){c.remove();return;}if(c.nodeType!==1)return;
  if(DROP.has(c.tagName)){c.remove();return;}walk(c);
  if(!KEEP.has(c.tagName)){c.replaceWith(...c.childNodes);return;}
  [...c.attributes].forEach(a=>{if(!(c.tagName==='IMG'&&a.name==='src'))c.removeAttribute(a.name);});
  if(c.tagName==='IMG'&&!/^https?:/i.test(c.getAttribute('src')||''))c.remove();});})(d.body);
 return d.body.innerHTML;}
function changed(){dirty=true;clearTimeout(checkT);checkT=setTimeout(check,350);if(pvOpen()){clearTimeout(pvT);pvT=setTimeout(preview,300);}}
flds.forEach((fd,i)=>{const ta=fd.querySelector('textarea'),rich=document.createElement('div');
 rich.className='rich';rich.contentEditable='true';rich.setAttribute('role','textbox');rich.setAttribute('aria-multiline','true');
 rich.setAttribute('aria-label',fd.dataset.name);rich.dataset.placeholder=ta.placeholder||'';
 rich.style.fontFamily=fd.dataset.font?`"${fd.dataset.font}",-apple-system,system-ui,sans-serif`:'';
 if(fd.dataset.size)rich.style.fontSize=Math.max(12,Math.min(60,+fd.dataset.size))+'px';
 if(fd.dataset.rtl==='1')rich.dir='rtl';
 rich.innerHTML=toDisplay(ta.value);ta.before(rich);fd.rich=rich;fd.ta=ta;
 if(fd.dataset.plain==='1')fd.classList.add('showhtml');
 rich.addEventListener('input',()=>{ta.value=serialize(rich);changed();});
 ta.addEventListener('input',()=>{rich.innerHTML=toDisplay(ta.value);changed();});
 rich.addEventListener('focus',()=>{last={fd,mode:'rich'};});
 ta.addEventListener('focus',()=>{last={fd,mode:'ta'};});
 rich.addEventListener('paste',e=>{const cd=e.clipboardData;if(!cd)return;
  let files=[...(cd.files||[])];
  if(!files.length&&cd.items)files=[...cd.items].filter(it=>it.kind==='file').map(it=>it.getAsFile()).filter(Boolean);
  files=files.filter(f=>/^image\//.test(f.type));
  if(files.length){e.preventDefault();files.forEach(f=>upload(f,true));return;}
  const h=cd.getData('text/html'),t=cd.getData('text/plain');e.preventDefault();
  if(h&&!shiftPaste)document.execCommand('insertHTML',false,toDisplay(clean(h)));
  else if(t)document.execCommand('insertText',false,t);shiftPaste=false;});
 [rich,ta].forEach(el=>{el.addEventListener('dragover',e=>{if(e.dataTransfer&&[...e.dataTransfer.types].includes('Files'))e.preventDefault();});
  el.addEventListener('drop',e=>{const fs=e.dataTransfer?[...e.dataTransfer.files]:[];if(!fs.length)return;
   e.preventDefault();el.focus();fs.forEach(f=>upload(f,false));});});
 fd.querySelector('.fname').addEventListener('click',()=>{fd.classList.toggle('collapsed');
  fd.querySelector('.fname').setAttribute('aria-expanded',!fd.classList.contains('collapsed'));});
 const hb=fd.querySelector('.htmlbtn');hb.setAttribute('aria-pressed',fd.classList.contains('showhtml'));
 hb.addEventListener('click',()=>toggleHtml(fd));
 const pin=fd.querySelector('.pin');if(pin)pin.addEventListener('click',async()=>{
  const b=new FormData();b.append('ntid',C.ntid);b.append('idx',i);
  const r=await fetch('/note/sticky',{method:'POST',body:b});if(r.ok){const j=await r.json();pin.setAttribute('aria-pressed',j.sticky);}});
});
function toggleHtml(fd){fd.classList.remove('collapsed');const on=fd.classList.toggle('showhtml');
 fd.querySelector('.htmlbtn').setAttribute('aria-pressed',on);(on?fd.ta:fd.rich).focus();}
document.addEventListener('selectionchange',()=>{const s=getSelection();if(!s.rangeCount)return;
 const r=s.getRangeAt(0),fd=flds.find(f=>f.rich.contains(r.commonAncestorContainer));if(fd){saved=r.cloneRange();last={fd,mode:'rich'};}});
function target(){if(!last||!document.body.contains(last.fd))last={fd:flds[0],mode:'rich'};
 const fd=last.fd;if(fd.classList.contains('collapsed'))fd.classList.remove('collapsed');
 if(last.mode==='ta'&&fd.classList.contains('showhtml')){fd.ta.focus();return last;}
 last.mode='rich';fd.rich.focus();const s=getSelection();
 if(saved&&fd.rich.contains(saved.commonAncestorContainer)){s.removeAllRanges();s.addRange(saved);}
 else if(!s.rangeCount||!fd.rich.contains(s.getRangeAt(0).commonAncestorContainer)){const r=document.createRange();
  r.selectNodeContents(fd.rich);r.collapse(false);s.removeAllRanges();s.addRange(r);}
 return last;}
function taInsert(ta,before,after){const s=ta.selectionStart,e=ta.selectionEnd,sel=ta.value.slice(s,e);
 ta.setRangeText(before+sel+(after||''),s,e,'end');if(!sel&&after)ta.selectionStart=ta.selectionEnd=s+before.length;
 ta.dispatchEvent(new Event('input'));}
function wrap(before,after){const t=target();if(t.mode==='ta'){taInsert(t.fd.ta,before,after);return;}
 const s=getSelection(),r=s.getRangeAt(0),div=document.createElement('div');div.appendChild(r.cloneContents());
 const empty=r.collapsed;
 document.execCommand('insertHTML',false,before+div.innerHTML+after);
 if(empty&&s.modify)for(let k=0;k<after.length;k++)s.modify('move','backward','character');}
const TAGS={bold:'b',italic:'i',underline:'u',subscript:'sub',superscript:'sup'};
function cmd(c,v){const t=target();
 if(t.mode==='ta'){if(TAGS[c])taInsert(t.fd.ta,'<'+TAGS[c]+'>','</'+TAGS[c]+'>');return;}
 if(c==='foreColor'||c==='hiliteColor'){document.execCommand('styleWithCSS',false,true);
  if(!document.execCommand(c,false,v)&&c==='hiliteColor')document.execCommand('backColor',false,v);
  document.execCommand('styleWithCSS',false,false);}
 else document.execCommand(c,false,v||null);}
function cloze(same){if(!C.cloze){say(C.msgs.notCloze,true);return;}let hi=0;
 flds.forEach(f=>{for(const m of f.ta.value.matchAll(/\{\{c(\d+)::/g))hi=Math.max(hi,+m[1]);});
 const n=same?Math.max(hi,1):hi+1;wrap('{{c'+n+'::','}}');}
function insertMedia(snip){const t=target();if(t.mode==='ta'){taInsert(t.fd.ta,snip,'');return;}
 if(snip.startsWith('['))document.execCommand('insertText',false,snip);else document.execCommand('insertHTML',false,toDisplay(snip));}
async function upload(file,pasted){const b=new FormData();b.append('file',file,file.name||'pasted');if(pasted)b.append('pasted','1');
 say(C.msgs.uploading.replace('{name}',file.name||C.msgs.image));
 try{const r=await fetch('/media/upload',{method:'POST',body:b});const j=await r.json();
  if(!r.ok){say(j.error||C.msgs.uploadFailed,true);return;}insertMedia(j.html);say('');}
 catch(e){say(C.msgs.uploadError.replace('{error}',e.message),true);}}
// ---- toolbar ----
const att=document.getElementById('ed-file');
att.addEventListener('change',()=>{[...att.files].forEach(f=>upload(f,false));att.value='';});
let rec=null,chunks=[];
async function record(btn){if(rec){rec.stop();return;}
 if(!navigator.mediaDevices||!navigator.mediaDevices.getUserMedia||!window.MediaRecorder){
  say(window.isSecureContext?C.msgs.noRecord:C.msgs.needSecure,true);return;}
 let stream;try{stream=await navigator.mediaDevices.getUserMedia({audio:true});}catch(e){say(C.msgs.noMic.replace('{error}',e.message),true);return;}
 chunks=[];rec=new MediaRecorder(stream);const tgt=last;
 rec.ondataavailable=e=>{if(e.data&&e.data.size)chunks.push(e.data);};
 rec.onstop=()=>{stream.getTracks().forEach(t=>t.stop());const type=(rec.mimeType||'audio/webm').split(';')[0];rec=null;
  btn.classList.remove('recording');btn.textContent='🎤';say('');
  const ext=type.includes('mp4')||type.includes('aac')?'m4a':type.includes('ogg')?'ogg':'webm';
  const d=new Date(),p=x=>String(x).padStart(2,'0');
  const name=`rec-${d.getFullYear()}${p(d.getMonth()+1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}.${ext}`;
  last=tgt;upload(new File(chunks,name,{type}),false);};
 rec.start();btn.classList.add('recording');btn.textContent='■ '+C.msgs.stop;say(C.msgs.recording);}
F.querySelectorAll('.tb button').forEach(b=>{b.addEventListener('mousedown',e=>e.preventDefault());
 b.addEventListener('click',e=>{e.preventDefault();const a=b.dataset.act,c=b.dataset.cmd;
  if(c){const inp=b.dataset.color&&document.getElementById(b.dataset.color);cmd(c,inp?inp.value:null);}
  else if(a==='cloze')cloze(false);else if(a==='cloze-same')cloze(true);
  else if(a==='mj-inline')wrap('\\(','\\)');else if(a==='mj-block')wrap('\\[','\\]');
  else if(a==='attach')att.click();else if(a==='record')record(b);else if(a==='preview')togglePreview();});});
F.querySelectorAll('.tb input[type=color]').forEach(inp=>{
 inp.addEventListener('change',()=>{const b=F.querySelector(`[data-color="${inp.id}"]`);cmd(b.dataset.cmd,inp.value);
  b.querySelector('span').style.borderColor=inp.value;});});
// ---- keys (Anki's shortcuts) ----
document.addEventListener('keydown',e=>{const inField=flds.some(f=>f.contains(e.target));
 const mod=e.ctrlKey||e.metaKey;
 if(mod&&e.key==='Enter'){e.preventDefault();submit();return;}
 if(!inField)return;
 if(e.key==='Shift')return;
 if(mod&&e.shiftKey&&(e.key==='v'||e.key==='V'))shiftPaste=true;
 if(chord&&Date.now()-chord<2000&&!mod){const k=e.key.toLowerCase();chord=0;
  if(k==='m'){e.preventDefault();wrap('\\(','\\)');return;}if(k==='e'){e.preventDefault();wrap('\\[','\\]');return;}}
 chord=0;
 if(mod&&e.shiftKey&&e.code==='KeyC'){e.preventDefault();cloze(e.altKey);}
 else if(mod&&e.shiftKey&&e.code==='KeyX'){e.preventDefault();toggleHtml(last?last.fd:flds[0]);}
 else if(mod&&e.shiftKey&&e.code==='KeyP'){e.preventDefault();togglePreview();}
 else if(mod&&!e.shiftKey&&e.code==='KeyM'){e.preventDefault();chord=Date.now();say(C.msgs.mathjax);}
 else if(mod&&e.code==='KeyR'){e.preventDefault();cmd('removeFormat');}
 else if(mod&&e.code==='Equal'){e.preventDefault();cmd(e.shiftKey?'superscript':'subscript');}
 else if(mod&&!e.shiftKey&&e.target.tagName==='TEXTAREA'&&['KeyB','KeyI','KeyU'].includes(e.code)){e.preventDefault();
  cmd({KeyB:'bold',KeyI:'italic',KeyU:'underline'}[e.code]);}
 else if(e.key==='F3'){e.preventDefault();att.click();}
 else if(e.key==='F7'){e.preventDefault();cmd('foreColor',document.getElementById('ed-fg').value);}
});
// ---- duplicate and cloze check ----
const dupBox=document.getElementById('ed-dupe');
async function check(){try{const r=await fetch('/note/check',{method:'POST',body:new FormData(F)});if(!r.ok)return;
 const j=await r.json();flds[0].classList.toggle('dupe',j.state==='duplicate');
 if(j.state==='duplicate'){dupBox.className='dupnote';dupBox.innerHTML='';const a=document.createElement('a');
  a.href=j.dupes;a.target='_blank';a.textContent=j.msg;dupBox.append(C.msgs.duplicate+' ',a);}
 else if(j.state==='notetype_not_cloze'||j.state==='field_not_cloze'){dupBox.className='warnnote';dupBox.textContent=j.msg;}
 else{dupBox.className='';dupBox.textContent='';}}catch(e){}}
if(flds.some(f=>f.ta.value))check();
// ---- preview ----
const PV=document.getElementById('ed-preview');let pvIdx=0,pvData=[];
function pvOpen(){return !PV.hidden;}
function togglePreview(){PV.hidden=!PV.hidden;if(!PV.hidden){preview();PV.scrollIntoView({behavior:'smooth',block:'nearest'});}}
function fit(fr){try{fr.style.height=Math.max(120,fr.contentDocument.documentElement.scrollHeight+6)+'px';}catch(e){}}
['pvq','pva'].forEach(id=>{const fr=document.getElementById(id);fr.addEventListener('load',()=>{fit(fr);setTimeout(()=>fit(fr),300);});
 fr.addEventListener('fc-resize',()=>fit(fr));});
function showCard(i){pvIdx=i;const c=pvData[i];if(!c)return;document.getElementById('pvq').srcdoc=c.q;document.getElementById('pva').srcdoc=c.a;
 PV.querySelectorAll('.pvtabs button').forEach((b,k)=>b.classList.toggle('primary',k===i));}
async function preview(){try{const r=await fetch('/note/preview',{method:'POST',body:new FormData(F)});if(!r.ok)return;
 pvData=(await r.json()).cards;const tabs=PV.querySelector('.pvtabs');tabs.innerHTML='';
 pvData.forEach((c,i)=>{const b=document.createElement('button');b.type='button';b.textContent=c.name;b.onclick=()=>showCard(i);tabs.append(b);});
 showCard(Math.min(pvIdx,pvData.length-1));}catch(e){}}
document.getElementById('pvclose').addEventListener('click',()=>{PV.hidden=true;});
// ---- note type switch keeps matching fields (as Anki's Add window does) ----
const ntSel=F.querySelector('select[name=ntid]');
if(ntSel&&C.adding){ntSel.addEventListener('change',()=>{const carry={tags:F.tags.value,f:{}};
  flds.forEach(f=>{if(f.ta.value)carry.f[f.dataset.name]=f.ta.value;});
  try{sessionStorage.setItem('fc-carry',JSON.stringify(carry));}catch(e){}
  dirty=false;location='/add?nt='+ntSel.value+'&did='+F.did.value;});
 try{const c=JSON.parse(sessionStorage.getItem('fc-carry')||'null');sessionStorage.removeItem('fc-carry');
  if(c){flds.forEach(f=>{if(!f.ta.value&&c.f[f.dataset.name]){f.ta.value=c.f[f.dataset.name];f.rich.innerHTML=toDisplay(f.ta.value);}});
   if(!F.tags.value&&c.tags)F.tags.value=c.tags;check();}}catch(e){}}
// ---- save ----
function submit(){flds.forEach(f=>{if(document.activeElement===f.rich)f.ta.value=serialize(f.rich);});dirty=false;
 if(typeof F.requestSubmit==='function')F.requestSubmit();else F.submit();}
F.addEventListener('submit',()=>{dirty=false;});
F.addEventListener('input',e=>{if(e.target.name==='tags')dirty=true;});
window.addEventListener('beforeunload',e=>{if(dirty&&!C.adding){e.preventDefault();e.returnValue='';}});
if(C.focus!==false){const first=flds.find(f=>!f.classList.contains('collapsed'));
 if(first&&!('ontouchstart' in window)){const s=first.classList.contains('showhtml')&&first.dataset.plain==='1'?first.ta:first.rich;
  s.focus();if(s===first.rich){const r=document.createRange();r.selectNodeContents(s);r.collapse(false);getSelection().removeAllRanges();getSelection().addRange(r);}}}
})();
</script>"""


def toolbar(col):
    tr = col.tr
    def b(label, title, **data):
        attrs = "".join(f' data-{k.replace("_", "-")}="{html.escape(v)}"' for k, v in data.items())
        return f'<button type="button" title="{html.escape(title)}" aria-label="{html.escape(title)}"{attrs}>{label}</button>'
    sep = '<span class="sep"></span>'
    return f"""<div class="tb" role="toolbar" aria-label="{html.escape(t('Formatting'))}">
{b('<b>B</b>', tr.editing_bold_text() + ' (Ctrl+B)', cmd='bold')}{b('<i>I</i>', tr.editing_italic_text() + ' (Ctrl+I)', cmd='italic')}
{b('<u>U</u>', tr.editing_underline_text() + ' (Ctrl+U)', cmd='underline')}
{b('x<sub>2</sub>', tr.editing_subscript() + ' (Ctrl+=)', cmd='subscript')}{b('x<sup>2</sup>', tr.editing_superscript() + ' (Ctrl+Shift+=)', cmd='superscript')}{sep}
<span class="sw">{b('<span style="border-bottom:3px solid #d64545;padding:0 2px">A</span>', tr.editing_text_color() + ' (F7)', cmd='foreColor', color='ed-fg')}<input type="color" id="ed-fg" value="#d64545" aria-label="{html.escape(t('Choose {color}', color=tr.editing_text_color()))}"></span>
<span class="sw">{b('<span style="border-bottom:3px solid #ffd43b;padding:0 2px">▤</span>', tr.editing_text_highlight_color(), cmd='hiliteColor', color='ed-hl')}<input type="color" id="ed-hl" value="#ffd43b" aria-label="{html.escape(t('Choose {color}', color=tr.editing_text_highlight_color()))}"></span>
{b('•≡', tr.editing_unordered_list(), cmd='insertUnorderedList')}{b('1≡', tr.editing_ordered_list(), cmd='insertOrderedList')}
{b('T<sub>×</sub>', tr.editing_remove_formatting() + ' (Ctrl+R)', cmd='removeFormat')}{sep}
{b('[…]', tr.editing_cloze_deletion() + ' (Ctrl+Shift+C)', act='cloze')}{b('[…]=', tr.editing_cloze_deletion_repeat() + ' (Ctrl+Shift+Alt+C)', act='cloze-same')}
{b('∑', tr.editing_mathjax_inline() + ' (Ctrl+M, M)', act='mj-inline')}{b('∑∑', tr.editing_mathjax_block() + ' (Ctrl+M, E)', act='mj-block')}{sep}
{b('📎', tr.editing_attach_picturesaudiovideo() + ' (F3)', act='attach')}{b('🎤', tr.editing_record_audio(), act='record')}
{b(html.escape(t('Preview')), t('Preview') + ' (Ctrl+Shift+P)', act='preview')}
<input type="file" id="ed-file" accept="image/*,audio/*,video/*" multiple hidden></div>
<p id="ed-msg" role="status" aria-live="polite"></p>"""


def note_form(col, nt, did, values=None, tags="", action="/add", extra="", adding=True, nid=0, confirm_cloze=False):
    """values: {field index: html}."""
    values = values or {}
    decks = sorted(col.decks.all_names_and_ids(), key=lambda d: d.name)
    deck_opts = "".join(f'<option value="{d.id}" {"selected" if d.id == did else ""}>{html.escape(d.name)}</option>'
                        for d in decks)
    nt_opts = "".join(f'<option value="{m.id}" {"selected" if m.id == nt["id"] else ""}>{html.escape(m.name)}</option>'
                      for m in col.models.all_names_and_ids())
    pin_title = html.escape(col.tr.editing_toggle_sticky())
    html_title = html.escape(col.tr.editing_html_editor() + " (Ctrl+Shift+X)")
    fields = []
    for i, f in enumerate(nt["flds"]):
        name = html.escape(f["name"])
        pin = (f'<button type="button" class="fb pin" title="{pin_title}" aria-label="{pin_title}: {name}" '
               f'aria-pressed="{"true" if f.get("sticky") else "false"}">📌</button>') if adding else ""
        coll = " collapsed" if f.get("collapsed") else ""
        fields.append(f"""<div class="fld{coll}" data-name="{name}" data-font="{html.escape(str(f.get('font', '')))}"
data-size="{int(f.get('size') or 20)}" data-rtl="{1 if f.get('rtl') else 0}" data-plain="{1 if f.get('plainText') else 0}">
<div class="fhead"><button type="button" class="fname" aria-expanded="{'false' if coll else 'true'}">{name}</button>{pin}
<button type="button" class="fb htmlbtn" title="{html_title}" aria-label="{html.escape(col.tr.editing_html_editor())}: {name}">&lt;/&gt;</button></div>
<textarea name="f{i}" id="f{i}" class="htmled" aria-label="{name}" spellcheck="false" autocapitalize="off"
placeholder="{html.escape(f.get('description') or '')}">{html.escape(values.get(i, ''))}</textarea>
{'<div id="ed-dupe" role="status" aria-live="polite"></div>' if i == 0 else ''}</div>""")
    hint = ""
    if nt["type"] == MODEL_CLOZE:
        # The buttons' markup goes in after escaping the sentence.
        text = html.escape(t("Cloze: select text and press {new} (Ctrl+Shift+C) for a new card, {same} "
                             "(Ctrl+Shift+Alt+C) to add to the same card. Or type {example}.",
                             new="\x00", same="\x01", example="\x02"))
        text = text.replace("\x00", "[&hellip;]").replace("\x01", "[&hellip;]=").replace("\x02", "{{c1::answer}}")
        hint = '<p class="muted" style="font-size:14px">' + text + '</p>'
    confirm = ""
    if confirm_cloze:
        confirm = (f'<div class="confirmbox"><p style="margin-top:0">{html.escape(col.tr.adding_you_have_a_cloze_deletion_note())}</p>'
                   f'<button class="primary" name="confirm_cloze" value="1">{html.escape(t("Add anyway"))}</button></div>')
    conf = {"ntid": nt["id"], "nid": nid, "adding": adding, "cloze": nt["type"] == MODEL_CLOZE,
            "msgs": {"notCloze": col.tr.adding_cloze_outside_cloze_notetype(),
                     "uploading": t("Uploading {name}…"), "image": t("image"),
                     "uploadFailed": t("Upload failed."), "uploadError": t("Upload failed: {error}"),
                     "noRecord": t("This browser cannot record audio."),
                     "needSecure": t("Recording needs a secure connection (https, or localhost)."),
                     "noMic": t("Microphone not available: {error}"), "stop": t("Stop"),
                     "recording": t("Recording… press Stop when done."),
                     "mathjax": t("MathJax: press M for inline, E for block."),
                     "duplicate": t("This note is a duplicate.")}}
    type_sel = (f'<select name="ntid">{nt_opts}</select>' if adding else
                f'<select disabled aria-label="{html.escape(t("Note type"))}">{nt_opts}</select>'
                f'<input type="hidden" name="ntid" value="{nt["id"]}">')
    save_title = (html.escape(col.tr.adding_add_shortcut_ctrlandenter()) if adding
                  else html.escape(t("Save") + " (Ctrl+Enter)"))
    return f"""{EDITOR_CSS}<form id="noteform" method="post" action="{action}" enctype="multipart/form-data">
<input type="hidden" name="nid" value="{nid}">
<div class="row"><div style="flex:1"><label>{html.escape(t("Type"))}</label>{type_sel}</div>
<div style="flex:1"><label>{html.escape(t("Deck"))}</label><select name="did">{deck_opts}</select></div></div>
{toolbar(col)}{''.join(fields)}{hint}{confirm}
<label for="tags">{html.escape(t("Tags (space separated)"))}</label><input id="tags" name="tags" value="{html.escape(tags)}" autocapitalize="off">
<noscript><label>{html.escape(t("Image or audio (optional, added to the end of the first field)"))}</label>
<input type="file" name="upload" accept="image/*,audio/*"></noscript>
<p class="row">{extra}<button class="primary" title="{save_title}">{html.escape(t("Add") if adding else t("Save"))}</button></p></form>
<div id="ed-preview" class="box" hidden><div class="row" style="justify-content:space-between"><b>{html.escape(t("Preview"))}</b>
<button type="button" id="pvclose">{html.escape(t("Close"))}</button></div><div class="pvtabs"></div>
<label>{html.escape(t("Front"))}</label><iframe id="pvq" title="{html.escape(t("Front preview"))}"></iframe>
<label>{html.escape(t("Back"))}</label><iframe id="pva" title="{html.escape(t("Back preview"))}"></iframe></div>
<script type="application/json" id="ed-conf">{core.js(conf)}</script>{EDITOR_JS}"""


# ---------- routes ----------
@router.get("/add", response_class=HTMLResponse)
def add_page(request: Request, nt: int = 0, did: int = 0):
    user = user_or_none(request)
    if not user: return core.need_login(request)
    with core.collection(user) as col:
        model = (col.models.get(nt) if nt else None) or col.models.current()
        did = did or col.get_config("curDeck", 1)
        from flashcards.image_occlusion import is_io_notetype
        if is_io_notetype(col, model):   # Anki's Add window switches to the mask editor for these
            return RedirectResponse(f"/io/new?nt={model['id']}&did={did}", 303)
        draft = _drafts.pop(user, None)
        if draft and draft["ntid"] != model["id"]:
            draft = None
        keep = _sticky.get((user, model["id"]), {})
        if draft:
            values, tags, did = draft["fields"], draft["tags"], draft["did"]
        else:
            values = {i: v for i, v in keep.get("fields", {}).items()
                      if i < len(model["flds"]) and model["flds"][i].get("sticky")}
            tags = keep.get("tags", "")
        form = note_form(col, model, did, values, tags, confirm_cloze=bool(draft and draft.get("confirm")))
        body = (f'<div class="box"><div class="row" style="justify-content:space-between"><h2 style="margin:0">{html.escape(t("Add card"))}</h2>'
                f'<div class="row"><a class="btn" href="/io/new?did={did}">{html.escape(t("Image Occlusion"))}</a>'
                f'<a class="btn" href="/notetypes/{model["id"]}/cards">{html.escape(t("Cards…"))}</a></div></div>{form}</div>')
    return core.page(t("Add"), body + '<span data-noundokey hidden></span>', user, "add", core.take_flash(request))


async def save_upload(col, upload):
    """The no-JavaScript file field: added to the end of the first field."""
    if upload is None or not getattr(upload, "filename", ""):
        return ""
    data = await upload.read()
    if not data:
        return ""
    name = col.media.write_data(os.path.basename(upload.filename), data)
    if (upload.content_type or "").startswith("audio"):
        return f"[sound:{name}]"
    return f'<img src="{html.escape(name)}">'


@router.post("/add")
async def add(request: Request):
    user = user_or_none(request)
    if not user: return core.need_login(request)
    form = await request.form()
    with core.collection(user) as col:
        model = col.models.get(int(form["ntid"]))
        note = col.new_note(model)
        fill_note(note, model, form)
        extra = await save_upload(col, form.get("upload"))
        if extra:
            note[model["flds"][0]["name"]] += extra
        tags = str(form.get("tags", ""))
        note.tags = tags.split()
        did = int(form["did"])
        chk = note_check(col, note)
        problem, confirm = "", False
        if chk["state"] in ("empty", "notetype_not_cloze", "field_not_cloze"):
            problem = chk["msg"]
        elif chk["state"] == "missing_cloze" and form.get("confirm_cloze") != "1":
            problem, confirm = chk["msg"], True
        if not problem:
            try:
                col.add_note(note, did)
            except Exception as e:
                problem = str(e)
        if problem:
            _drafts[user] = {"ntid": model["id"], "did": did, "tags": tags, "confirm": confirm,
                             "fields": {i: v for i, v in enumerate(note.fields)}}
            core.set_flash(request, "!" + t("Could not add: {problem}", problem=problem))
        else:
            col.models.set_current(model)
            col.set_config("curDeck", did)
            _sticky[(user, model["id"])] = {
                "tags": tags, "fields": {i: note.fields[i] for i, f in enumerate(model["flds"]) if f.get("sticky")}}
            core.set_flash(request, tn("Added {n} card(s).", "Added {n} card(s).", len(note.cards())))
    return RedirectResponse(f"/add?nt={model['id']}&did={did}", 303)


@router.get("/edit/{nid}", response_class=HTMLResponse)
def edit_page(request: Request, nid: int, back: str = "/browse", raw: int = 0):
    user = user_or_none(request)
    if not user: return core.need_login(request)
    back = core.safe_back(back)
    qback = quote(back, safe="/")   # back may carry its own ?query (e.g. the study screen's card and side)
    with core.collection(user) as col:
        try:
            note = col.get_note(nid)
        except Exception:
            core.set_flash(request, "!" + t("That note no longer exists."))
            return RedirectResponse(back, 303)
        model = note.note_type()
        from flashcards.image_occlusion import is_io_notetype
        if not raw and is_io_notetype(col, model):   # masks are edited in the image occlusion editor
            return RedirectResponse(f"/io/{nid}?back={qback}", 303)
        cards = note.cards()
        did = cards[0].did if cards else 1
        form = note_form(col, model, did, dict(enumerate(note.fields)), " ".join(note.tags),
                         action=html.escape(f"/edit/{nid}?back={qback}"), adding=False, nid=nid,
                         extra=f'<a class="btn" href="{html.escape(back)}">{html.escape(t("Cancel"))}</a>')
        cards_box = core.cards_panel(col, note, f"/edit/{nid}?back={qback}")
    body = f"""<div class="box"><h2 style="margin-top:0">{html.escape(t("Edit note"))}</h2>{form}
<form method="post" action="/note/{nid}/delete?back={html.escape(qback)}" onsubmit="return confirm({html.escape(core.js(t('Delete this note and its cards?')))})">
<button class="danger">{html.escape(t("Delete note"))}</button></form>
<p class="row" style="margin-bottom:0"><a class="btn" href="/notetypes/{model['id']}/cards?nid={nid}">{html.escape(t("Cards…"))}</a>
<a class="btn" href="/notetypes/{model['id']}/fields">{html.escape(t("Fields…"))}</a>
<a class="btn" href="/notetypes/change?nids={nid}&amp;back={html.escape(qback)}">{html.escape(t("Change note type"))}</a></p></div>{cards_box}"""
    return core.page(t("Edit"), body + '<span data-noundokey hidden></span>', user, "browse", core.take_flash(request))


@router.post("/edit/{nid}")
async def edit(request: Request, nid: int, back: str = "/browse"):
    user = user_or_none(request)
    if not user: return core.need_login(request)
    back = core.safe_back(back)
    form = await request.form()
    with core.collection(user) as col:
        note = col.get_note(nid)
        model = note.note_type()
        fill_note(note, model, form)
        extra = await save_upload(col, form.get("upload"))
        if extra:
            note[model["flds"][0]["name"]] += extra
        note.tags = str(form.get("tags", "")).split()
        col.update_note(note)
        new_did = int(form.get("did", 0) or 0)
        if new_did:
            col.set_deck([c.id for c in note.cards()], new_did)
    core.set_flash(request, t("Saved."))
    return RedirectResponse(back, 303)


@router.post("/media/upload")
async def media_upload(request: Request):
    """A file pasted, dropped, attached or recorded in the editor. Returns the field HTML for it:
    <img> for pictures, [sound:] for audio and video, as Anki's editor does."""
    user = user_or_none(request)
    if not user: return JSONResponse({"error": t("Please log in again.")}, 401)
    form = await request.form()
    up = form.get("file")
    if up is None or not hasattr(up, "read"):
        return JSONResponse({"error": t("No file was sent.")}, 400)
    data = await up.read()
    if not data:
        return JSONResponse({"error": t("The file is empty.")}, 400)
    if len(data) > MAX_UPLOAD:
        return JSONResponse({"error": t("The file is larger than 100 MB.")}, 413)
    ctype = (up.content_type or "").split(";")[0].lower()
    fname = os.path.basename(up.filename or "").strip() or "file"
    ext = os.path.splitext(fname)[1].lower()
    if not ext and "/" in ctype:
        ext = "." + {"jpeg": "jpg", "svg+xml": "svg", "mpeg": "mp3"}.get(ctype.split("/")[1], ctype.split("/")[1])
        fname += ext
    if ctype.startswith("image/") or ext in IMAGE_EXTS:
        kind = "image"
    elif ctype.startswith(("audio/", "video/")) or ext in AV_EXTS:
        kind = "av"
    else:
        return JSONResponse({"error": t("Only pictures, audio and video can be added.")}, 415)
    if form.get("pasted") == "1":   # Anki names pasted pictures after their checksum
        fname = f"paste-{hashlib.sha1(data).hexdigest()}{ext or '.png'}"
    with core.collection(user) as col:
        name = col.media.write_data(fname, data)
    snippet = f'<img src="{html.escape(name)}">' if kind == "image" else f"[sound:{name}]"
    return {"name": name, "html": snippet}


@router.post("/note/check")
async def check_route(request: Request):
    user = user_or_none(request)
    if not user: return JSONResponse({"error": "login"}, 401)
    form = await request.form()
    with core.collection(user) as col:
        try:
            note = form_note(col, form)
        except Exception:
            return JSONResponse({"error": t("Unknown note or note type.")}, 404)
        return note_check(col, note)


def preview_doc(col, card, text, kind="question"):
    text = core.render_side(col, card, addons.card_render(col, card, kind, text))
    box = ('<center><input type="text" id="typeans" placeholder="'
           + html.escape(t("(typing the answer)")) + '" disabled></center>')
    text = core.TYPE_PAT.sub(lambda m: box, text)
    return core.card_doc(text, core.theme_of(col))


@router.post("/note/preview")
async def preview_route(request: Request):
    """Cards the unsaved note would make, rendered by the engine without saving anything."""
    user = user_or_none(request)
    if not user: return JSONResponse({"error": "login"}, 401)
    form = await request.form()
    with core.collection(user) as col:
        try:
            note = form_note(col, form)
        except Exception:
            return JSONResponse({"error": t("Unknown note or note type.")}, 404)
        nt = note.note_type()
        if nt["type"] == MODEL_CLOZE:
            ords = sorted(n - 1 for n in note.cloze_numbers_in_fields()) or [0]
            names = [t("Cloze {n}", n=o + 1) for o in ords]
        else:
            ords = list(range(len(nt["tmpls"])))
            names = [tm["name"] for tm in nt["tmpls"]]
        cards = []
        for o, name in zip(ords, names):
            card = note.ephemeral_card(o)
            q = card.question()
            if "#front-of-card-is-blank" in q and nt["type"] != MODEL_CLOZE:
                continue   # this card type would not be made for the note
            cards.append({"name": name, "q": preview_doc(col, card, q),
                          "a": preview_doc(col, card, card.answer(), "answer")})
        if not cards:
            card = note.ephemeral_card(ords[0])
            cards.append({"name": names[0], "q": preview_doc(col, card, card.question()),
                          "a": preview_doc(col, card, card.answer(), "answer")})
        return {"cards": cards}


@router.post("/note/sticky")
async def sticky_route(request: Request):
    """Pin a field so its content stays after Add (Anki's 'Toggle sticky', saved in the note type)."""
    user = user_or_none(request)
    if not user: return JSONResponse({"error": "login"}, 401)
    form = await request.form()
    with core.collection(user) as col:
        try:
            nt = col.models.get(int(form.get("ntid", 0)))
            idx = int(form.get("idx", -1))
            fld = nt["flds"][idx] if nt and 0 <= idx < len(nt["flds"]) else None
        except (TypeError, ValueError):
            fld = None
        if not fld:
            return JSONResponse({"error": t("Unknown field.")}, 404)
        fld["sticky"] = not fld.get("sticky", False)
        col.models.update_dict(nt)
        if not fld["sticky"]:
            _sticky.get((user, nt["id"]), {}).get("fields", {}).pop(idx, None)
        return {"sticky": fld["sticky"]}
