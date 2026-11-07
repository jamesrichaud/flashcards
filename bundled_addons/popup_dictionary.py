"""Look up a word on a card: select (or long-press) a word to see its meaning in a small pop-up.

Like the desktop pop-up dictionary add-ons. English definitions come from the free
dictionaryapi.dev; every pop-up also links to Wiktionary, which covers most languages.
"""
import json

from flashcards.i18n import t, N_

NAME = "Pop-up Dictionary"   # plain literal: flashcards/addons.py reads it without importing
N_("Pop-up Dictionary")      # ...so mark it for translation here
N_('Look up a word on a card: select (or long-press) a word to see its meaning in a small pop-up.')  # the first docstring line, shown on the Add-ons page

SCRIPT = r"""<script>(function(){
var T=__T__,pop=null,timer=null;
function close(){if(pop){pop.remove();pop=null;}}
function esc(s){var d=document.createElement('div');d.textContent=s;return d.innerHTML;}
function wordAt(){var s=document.getSelection();if(!s||s.isCollapsed)return '';
 var w=s.toString().trim();if(!w||w.length>40||/\s{2,}|\n/.test(w)||w.split(' ').length>3)return '';
 return w.replace(/^[^\p{L}\p{N}]+|[^\p{L}\p{N}]+$/gu,'');}
async function lookup(){var w=wordAt();if(!w)return;var s=document.getSelection(),r=s.getRangeAt(0).getBoundingClientRect();
 close();pop=document.createElement('div');pop.id='pd-pop';
 pop.style.cssText='position:absolute;z-index:70;max-width:min(420px,90vw);color-scheme:light;background:#fff;color:#111;border:1px solid #ccc;'
  +'border-radius:12px;padding:12px 14px;box-shadow:0 6px 24px rgba(0,0,0,.18);font:15px/1.4 system-ui,sans-serif;text-align:left';
 pop.style.left=Math.max(8,Math.min(r.left+scrollX,document.documentElement.clientWidth-300))+'px';
 pop.style.top=(r.bottom+scrollY+8)+'px';
 var wk='https://en.wiktionary.org/wiki/'+encodeURIComponent(w.toLowerCase());
 pop.innerHTML='<b>'+esc(w)+'</b><div id="pd-body" style="margin-top:6px;color:#555">'+esc(T.looking)+'</div>'
  +'<div style="margin-top:8px"><a href="'+wk+'" target="_blank" rel="noopener" style="color:#2f6fde">'+esc(T.wiki)+'</a></div>';
 pop.addEventListener('pointerdown',function(e){e.stopPropagation();},true);
 pop.addEventListener('pointerup',function(e){e.stopPropagation();},true);
 document.body.appendChild(pop);try{window.frameElement.dispatchEvent(new Event('fc-resize'));}catch(e){}
 var body=pop.querySelector('#pd-body'),out='';
 try{var res=await fetch('https://api.dictionaryapi.dev/api/v2/entries/en/'+encodeURIComponent(w.toLowerCase()));
  if(res.ok){var j=await res.json(),n=0;
   (j[0].phonetic)&&(out+='<div style="color:#888">'+esc(j[0].phonetic)+'</div>');
   j.forEach(function(e){(e.meanings||[]).forEach(function(m){(m.definitions||[]).slice(0,2).forEach(function(d){
    if(n++<4)out+='<div style="margin-top:4px"><i>'+esc(m.partOfSpeech||'')+'</i> '+esc(d.definition)+'</div>';});});});}
 }catch(e){}
 if(pop)body.innerHTML=out||esc(T.none);}
function later(){clearTimeout(timer);timer=setTimeout(lookup,350);}
document.addEventListener('mouseup',later);document.addEventListener('touchend',later);
document.addEventListener('selectionchange',function(){if(document.getSelection().isCollapsed)return;later();});
document.addEventListener('pointerdown',function(e){if(pop&&!pop.contains(e.target))close();});
})();</script>"""


def card_render(text, card, kind, col):
    words = {"looking": t("Looking up…"), "wiki": t("Open in Wiktionary ↗"),
             "none": t("No English definition found. Try Wiktionary.")}
    return text + SCRIPT.replace("__T__", json.dumps(words).replace("<", "\\u003c"))
