"""Draws Image Occlusion masks on cards.

Anki's Image Occlusion card template ends with `anki.imageOcclusion.setup()`, a function that
lives in desktop Anki's reviewer bundle (not in the `anki` Python package). This is a small port
of it (ts/routes/image-occlusion/review.ts): it reads the shapes the engine renders into the
hidden `.cloze` / `.cloze-inactive` / `.cloze-highlight` divs and paints them on the canvas over
the image, using the colours from the note type's CSS. card_doc() adds it to any card that calls
setup(), so study, previews and the card layout screen all show the masks.
"""

IO_REVIEW_JS = r"""<style>#image-occlusion-container{position:relative;display:inline-block;max-width:100%}
#image-occlusion-container img{display:block;max-width:100%;height:auto;margin:0 auto}
#image-occlusion-canvas{position:absolute;left:0;top:0;width:100%;height:100%;pointer-events:none}</style>
<script>(function(){
function num(v){var n=parseFloat(v);return isFinite(n)?n:0;}
function shapesOf(sel){return Array.prototype.slice.call(document.querySelectorAll(sel)).map(function(el){
 var s={},d=el.dataset;for(var k in d)s[k]=d[k];s.oi=d.occludeinactive==='1'||d.oi==='1';return s;})
 .filter(function(s){return s.shape;});}
function pts(s){return String(s.points||'').trim().split(/\s+/).filter(Boolean).map(function(p){
 var q=p.split(',');return [num(q[0]),num(q[1])];});}
// Old notes (Anki 23.10) stored pixel positions; newer ones store fractions of the image size.
function isAbsolute(list){return list.some(function(s){
 var v=[s.left,s.top,s.width,s.height,s.rx,s.ry].map(num);pts(s).forEach(function(p){v.push(p[0],p[1]);});
 return v.some(function(x){return x>1.5;});});}
function border(str,dflt){var m=/([\d.]+)px\s+(\S+)/.exec(str||'');return m?[+m[1],m[2]]:dflt;}
function paint(ctx,s,k,fill,b){
 var L=num(s.left)*k.x,T=num(s.top)*k.y,a=num(s.angle)*Math.PI/180;
 ctx.save();ctx.fillStyle=fill;ctx.strokeStyle=b[1];ctx.lineWidth=b[0];
 if(a){ctx.translate(L,T);ctx.rotate(a);ctx.translate(-L,-T);}
 ctx.beginPath();
 if(s.shape==='rect')ctx.rect(L,T,num(s.width)*k.x,num(s.height)*k.y);
 else if(s.shape==='ellipse'){var rx=num(s.rx)*k.x,ry=num(s.ry)*k.y;ctx.ellipse(L+rx,T+ry,rx,ry,0,0,2*Math.PI);}
 else if(s.shape==='polygon'){var p=pts(s);if(p.length<3){ctx.restore();return;}
  var mx=Math.min.apply(null,p.map(function(q){return q[0];})),my=Math.min.apply(null,p.map(function(q){return q[1];}));
  var ox=s.left!==undefined?num(s.left)-mx:0,oy=s.top!==undefined?num(s.top)-my:0;
  p.forEach(function(q,i){var x=(q[0]+ox)*k.x,y=(q[1]+oy)*k.y;if(i)ctx.lineTo(x,y);else ctx.moveTo(x,y);});ctx.closePath();}
 else{ctx.restore();return;}
 ctx.fill();if(b[0]>0)ctx.stroke();ctx.restore();}
function label(ctx,s,k,h){var t=String(s.text||'');if(!t){return;}
 var fs=num(s.fs)||0.04,sc=num(s.scale)||1,px=(fs<=1.5?fs*h:fs)*sc;
 ctx.save();ctx.font=px+'px Arial, sans-serif';ctx.textBaseline='top';
 var L=num(s.left)*k.x,T=num(s.top)*k.y,lines=t.split('\n'),w=0;
 lines.forEach(function(l){w=Math.max(w,ctx.measureText(l).width);});
 ctx.fillStyle='rgba(255,255,255,.85)';ctx.fillRect(L-2,T-2,w+4,lines.length*px*1.15+4);
 ctx.fillStyle='#000';lines.forEach(function(l,i){ctx.fillText(l,L,T+i*px*1.15);});ctx.restore();}
function draw(img,cv){
 var w=img.clientWidth,h=img.clientHeight;if(!w||!h)return;
 var dpr=window.devicePixelRatio||1;cv.width=Math.round(w*dpr);cv.height=Math.round(h*dpr);
 var ctx=cv.getContext('2d');ctx.setTransform(dpr,0,0,dpr,0,0);ctx.clearRect(0,0,w,h);
 var cs=getComputedStyle(cv),v=function(n,d){return (cs.getPropertyValue(n)||'').trim()||d;};
 var act=shapesOf('.cloze'),ina=shapesOf('.cloze-inactive'),hi=shapesOf('.cloze-highlight'),all=act.concat(ina,hi);
 var k=isAbsolute(all)?{x:w/img.naturalWidth,y:h/img.naturalHeight}:{x:w,y:h};
 var masks=function(s){return s.shape!=='text';};
 ina.filter(function(s){return s.oi&&masks(s);}).forEach(function(s){
  paint(ctx,s,k,v('--inactive-shape-color','#ffeba2'),border(v('--inactive-shape-border',''),[1,'#212121']));});
 act.filter(masks).forEach(function(s){
  paint(ctx,s,k,v('--active-shape-color','#ff8e8e'),border(v('--active-shape-border',''),[1,'#212121']));});
 hi.filter(masks).forEach(function(s){
  paint(ctx,s,k,v('--highlight-shape-color','#ff8e8e00'),border(v('--highlight-shape-border',''),[1,'#ff8e8e']));});
 all.filter(function(s){return s.shape==='text';}).forEach(function(s){label(ctx,s,k,h);});
 try{window.frameElement&&window.frameElement.dispatchEvent(new Event('fc-resize'));}catch(e){}}
function setup(){
 var box=document.getElementById('image-occlusion-container'),cv=document.getElementById('image-occlusion-canvas');
 if(!box||!cv)return;var img=box.querySelector('img');
 if(!img){var er=document.getElementById('err');if(er)er.textContent='No image to occlude.';return;}
 var go=function(){draw(img,cv);};
 if(img.complete&&img.naturalWidth)go();img.addEventListener('load',go);window.addEventListener('resize',go);
 var t=document.getElementById('toggle');
 if(t)t.addEventListener('click',function(){cv.style.visibility=cv.style.visibility==='hidden'?'':'hidden';});}
window.anki=window.anki||{};window.anki.imageOcclusion={setup:setup};
})();</script>"""
