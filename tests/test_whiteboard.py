"""Whiteboard on the study screen: settings per person, the study page wiring, and the drawing
logic driven with simulated pointer events (pen, finger, mouse) in node with a fake canvas.
Real-browser pointer tests are in tests/shots_whiteboard.py (needs Chromium)."""
import glob, json, os, re, shutil, subprocess, sys, tempfile
os.environ["DATA_DIR"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fastapi.testclient import TestClient
import app as webapp
from flashcards import whiteboard

ok = lambda m: print("PASS", m)
c = TestClient(webapp.app)
c.post("/login", data={"username": "wbuser", "password": "secret123"})
with webapp.collection("wbuser") as col:
    for f, b in (("chat", "cat"), ("chien", "dog")):
        n = col.new_note(col.models.by_name("Basic")); n["Front"] = f; n["Back"] = b
        col.add_note(n, 1)

# ---------- off by default, page wiring ----------
r = c.get("/study/1")
assert r.status_code == 200
assert 'id="wbtoggle" aria-pressed="false"' in r.text and '<canvas id="wb" style="display:none">' in r.text
assert 'id="wbbar" class="row" style="display:none"' in r.text
assert '<div id="wbwrap"><iframe id="frame"' in r.text
for s in ("function fcWhiteboard(", "function wbTap(", "touch-action:none", '"on": false', "tap:wbTap"):
    assert s in r.text, s
assert whiteboard.CSS in r.text and "#wbwrap{position:relative}" in r.text and "{{" not in r.text.split("<script")[0]
for k in whiteboard.COLORS:
    assert f'data-wbc="{k}"' in r.text
for w in whiteboard.WIDTHS:
    assert f'data-wbw="{w}"' in r.text
for i in ("wberase", "wbundo", "wbclear"):
    assert f'id="{i}"' in r.text
ok("study screen has the whiteboard toggle (off), hidden toolbar and canvas over the card")

# ---------- saving settings ----------
r = c.post("/study/whiteboard", json={"on": True})
assert r.status_code == 200 and r.json()["ok"] and r.json()["on"] is True and r.json()["stamp"]
with webapp.collection("wbuser") as col:
    assert col.get_config(whiteboard.WB_KEY) == {"on": True, "color": "", "width": 4}
r = c.post("/study/whiteboard", json={"color": "red", "width": 8})
assert r.json()["color"] == "red" and r.json()["width"] == 8 and r.json()["on"] is True
r = c.get("/study/1")
assert 'id="wbtoggle" aria-pressed="true"' in r.text and 'id="wbbar" class="row" style=""' in r.text
assert '"on": true, "color": "red", "width": 8' in r.text
ok("turning it on, colour and width are saved in the collection config and used by the page")

for bad in ({"on": "yes"}, {"color": "green"}, {"width": 5}, {"width": True}, {"evil": 1}, {}, [1], "x"):
    r = c.post("/study/whiteboard", json=bad)
    assert r.status_code == 400, bad
r = c.post("/study/whiteboard", content=b"not json", headers={"Content-Type": "application/json"})
assert r.status_code == 400
with webapp.collection("wbuser") as col:
    assert whiteboard.settings(col) == {"on": True, "color": "red", "width": 8}
    col.set_config(whiteboard.WB_KEY, {"on": "x", "color": "<b>", "width": 99})   # damaged config
    assert whiteboard.settings(col) == whiteboard.DEFAULT
    col.set_config(whiteboard.WB_KEY, {"on": True, "color": "red", "width": 8})
ok("bad values are refused (400) and a damaged config falls back to the defaults")

anon = TestClient(webapp.app)
assert anon.post("/study/whiteboard", json={"on": True}).status_code == 401
c2 = TestClient(webapp.app)
c2.post("/login", data={"username": "other", "password": "secret123"})
with webapp.collection("other") as col:
    assert whiteboard.settings(col)["on"] is False
ok("needs login; remembered per person (another user still has it off)")

with webapp.collection("wbuser") as col:
    undo_before = col.undo_status().undo
c.post("/study/whiteboard", json={"on": False})
with webapp.collection("wbuser") as col:
    assert col.undo_status().undo == undo_before and whiteboard.settings(col)["on"] is False
c.post("/study/whiteboard", json={"on": True})
ok("changing the whiteboard isn't an undo step")

# ---------- studying still works with it on ----------
r = c.get("/study/1")
cid = int(re.search(r'name="card_id" value="(\d+)"', r.text).group(1))
c.post("/study/1/answer", data={"card_id": cid, "rating": 3, "ms": 1000})
with webapp.collection("wbuser") as col:
    assert col.get_card(cid).reps == 1
r = c.get("/study/1")
assert re.search(r'name="card_id" value="(\d+)"', r.text).group(1) != str(cid)
assert "let strokes=[]" in r.text   # drawing starts empty on each new card (page)
ok("answering works with the whiteboard on; the next card starts with a clean board")

# ---------- drawing logic with simulated pointer events (node) ----------
node = shutil.which("node") or next(iter(glob.glob(os.path.join(
    os.path.dirname(__import__("playwright").__file__) if __import__("importlib").util.find_spec("playwright")
    else "/nonexistent", "driver", "node*"))), None)

HARNESS = r"""
const calls=[];let scrolled=0,saved=[],taps=[];
function el(){const L={};return{L,style:{},dataset:{},attrs:{},addEventListener(t,f){(L[t]=L[t]||[]).push(f);},
 setAttribute(k,v){this.attrs[k]=v;},fire(t,e){for(const f of L[t]||[])f(e);}};}
const ctx={save(){},restore(){},beginPath(){},arc(){calls.push('arc');},fill(){},moveTo(){},lineTo(){},
 stroke(){calls.push('line');},setTransform(){},clearRect(){calls.push('clear');}};
const canvas=el();canvas.getContext=()=>ctx;canvas.width=0;canvas.height=0;
canvas.getBoundingClientRect=()=>({left:0,top:100});canvas.setPointerCapture=()=>{};
const frame={offsetWidth:400,offsetHeight:600};
const btn=(ds,id)=>Object.assign(el(),{dataset:ds,id});
const colorBtns=['black','white','red','blue'].map(k=>btn({wbc:k},''));
const widthBtns=[2,4,8].map(w=>btn({wbw:String(w)},''));
const erase=btn({},'wberase');
const bar=el();bar.querySelectorAll=s=>s==='[data-wbc]'?colorBtns:widthBtns;bar.querySelector=()=>erase;
const toggle=el();
const store={};
global.sessionStorage={getItem:k=>store[k]??null,setItem:(k,v)=>{store[k]=v;}};
global.window={devicePixelRatio:2,scrollBy:(x,y)=>{scrolled+=y;},addEventListener(){}};
let now=1000;Date.now=()=>now;
%JS%
const wb=fcWhiteboard({canvas,frame,bar,toggle,conf:%CONF%,dark:()=>false,tap:(x,y)=>taps.push([x,y]),save:c=>saved.push(c)});
let pd=0;
const ev=(type,id,x,y,more)=>Object.assign({pointerId:id,pointerType:type,clientX:x,clientY:y+100,button:0,pressure:0.5,
 width:1,height:1,preventDefault(){pd++;}},more||{});
const down=(...a)=>canvas.fire('pointerdown',ev(...a)),move=(...a)=>canvas.fire('pointermove',ev(...a)),
 up=(...a)=>canvas.fire('pointerup',ev(...a)),cancel=(...a)=>canvas.fire('pointercancel',ev(...a));
function out(name){console.log(JSON.stringify({name,strokes:wb.strokes().map(s=>({n:s.p.length,c:s.c,erase:s.erase,w:s.p[0][2]})),
 scrolled,taps,saved,pen:wb.penMode(),on:wb.state.on,size:[canvas.width,canvas.height,canvas.style.width,canvas.style.height],
 pressed:toggle.attrs['aria-pressed'],eraser:erase.attrs['aria-pressed'],
 colors:colorBtns.map(b=>b.attrs['aria-pressed']),widths:widthBtns.map(b=>b.attrs['aria-pressed'])}));}
out('start');
// mouse drag draws a stroke
down('mouse',1,10,10);move('mouse',1,50,10);move('mouse',1,90,10);up('mouse',1,90,10);out('mouse');
// right mouse button does nothing
down('mouse',2,10,10,{button:2});move('mouse',2,50,50);up('mouse',2,50,50);out('right');
// a still mouse click is a tap that goes to the card (no dot drawn)
now+=1000;down('mouse',3,30,40);now+=100;up('mouse',3,30,40);out('click');
// a finger draws while no pen has been used
now+=1000;down('touch',4,100,100);move('touch',4,130,100);move('touch',4,160,100);up('touch',4,160,100);out('finger');
// two fingers scroll instead of drawing (the started finger stroke is dropped)
now+=1000;down('touch',5,100,300);down('touch',6,200,300);move('touch',5,100,280);move('touch',6,200,280);
move('touch',5,100,260);move('touch',6,200,260);up('touch',5,100,260);up('touch',6,200,260);out('twofinger');
// pen with pressure draws; afterwards fingers only scroll (palm-safe)
now+=1000;down('pen',7,10,500,{pressure:1});move('pen',7,20,500,{pressure:1});up('pen',7,20,500);out('pen');
now+=1000;down('touch',8,100,100);move('touch',8,100,80);move('touch',8,100,60);up('touch',8,100,60);out('fingerafterpen');
// a still finger tap after pen use still goes to the card
now+=1000;down('touch',9,55,66);now+=50;up('touch',9,55,66);out('tapafterpen');
// a palm (big contact) while the pen draws: no scroll, no tap, pen stroke kept
now+=1000;down('pen',10,10,10);down('touch',11,300,300,{width:80,height:80});move('touch',11,300,200,{width:80,height:80});
move('pen',10,40,40);up('touch',11,300,200);up('pen',10,40,40);out('palm');
// a pen tap draws a dot
now+=1000;down('pen',12,5,5);now+=50;up('pen',12,5,5);out('pendot');
// cancelled pointer keeps what was drawn, no tap
now+=1000;down('pen',13,1,1);move('pen',13,30,30);cancel('pen',13,30,30);out('cancel');
// toolbar: colour, width, eraser, undo, clear
bar.fire('click',{target:{closest:()=>colorBtns[3]}});bar.fire('click',{target:{closest:()=>widthBtns[2]}});
now+=1000;down('mouse',14,0,0);move('mouse',14,30,30);up('mouse',14,30,30);out('blue8');
bar.fire('click',{target:{closest:()=>erase}});
now+=1000;down('mouse',15,0,0);move('mouse',15,30,30);up('mouse',15,30,30);out('erase');
bar.fire('click',{target:{closest:()=>btn({},'wbundo')}});out('undo');
bar.fire('click',{target:{closest:()=>btn({},'wbclear')}});out('clear');
// toggle off: canvas hidden, events ignored
toggle.fire('click',{});down('mouse',16,0,0);move('mouse',16,9,9);up('mouse',16,9,9);out('off');
console.log(JSON.stringify({name:'misc',display:canvas.style.display,bardisplay:bar.style.display,pd}));
"""

if node:
    d = tempfile.mkdtemp()
    f = os.path.join(d, "wb.js")
    with open(f, "w") as fh:
        fh.write(HARNESS.replace("%JS%", whiteboard.JS)
                 .replace("%CONF%", json.dumps({"on": True, "color": "", "width": 4})))
    p = subprocess.run([node, f], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    R = {}
    for line in p.stdout.splitlines():
        j = json.loads(line); R[j["name"]] = j
    s = R["start"]
    assert s["on"] and s["pressed"] == "true" and s["size"] == [800, 1200, "400px", "600px"]   # devicePixelRatio 2
    assert s["colors"] == ["true", "false", "false", "false"] and s["widths"] == ["false", "true", "false"]
    ok("canvas sized to the card (sharp on retina), default black pen, medium width")
    m = R["mouse"]["strokes"]
    assert len(m) == 1 and m[0]["n"] == 3 and m[0]["c"] == "#111111" and m[0]["w"] == 4 and not m[0]["erase"]
    assert len(R["right"]["strokes"]) == 1
    assert len(R["click"]["strokes"]) == 1 and R["click"]["taps"] == [[30, 40]]
    ok("mouse: drag draws, right button ignored, a still click goes through to the card")
    assert len(R["finger"]["strokes"]) == 2 and R["finger"]["pen"] is False
    t = R["twofinger"]
    assert len(t["strokes"]) == 2 and t["scrolled"] == 40 and len(t["taps"]) == 1
    ok("finger draws until a pen is used; two fingers scroll the page instead of drawing")
    pn = R["pen"]
    assert pn["pen"] is True and len(pn["strokes"]) == 3 and pn["strokes"][2]["w"] == 4 * 1.6   # pressure 1
    fa = R["fingerafterpen"]
    assert len(fa["strokes"]) == 3 and fa["scrolled"] == 40 + 40
    assert R["tapafterpen"]["taps"][-1] == [55, 66]
    pa = R["palm"]
    assert len(pa["strokes"]) == 4 and pa["scrolled"] == fa["scrolled"] and len(pa["taps"]) == 2
    ok("pen: pressure changes width; after a pen, fingers scroll and tap but never draw; a palm is ignored")
    assert len(R["pendot"]["strokes"]) == 5 and R["pendot"]["strokes"][4]["n"] == 1 and len(R["pendot"]["taps"]) == 2
    assert len(R["cancel"]["strokes"]) == 6 and len(R["cancel"]["taps"]) == 2
    ok("a pen tap leaves a dot; a cancelled pointer keeps its stroke and isn't a tap")
    b = R["blue8"]
    assert b["strokes"][-1]["c"] == "#2f6fde" and b["strokes"][-1]["w"] == 8
    assert {"color": "blue"} in b["saved"] and {"width": 8} in b["saved"]
    assert b["colors"] == ["false", "false", "false", "true"] and b["widths"] == ["false", "false", "true"]
    e = R["erase"]
    assert e["strokes"][-1]["erase"] is True and e["strokes"][-1]["w"] == 8 * 4 + 8 and e["eraser"] == "true"
    assert e["colors"] == ["false"] * 4
    assert len(R["undo"]["strokes"]) == len(e["strokes"]) - 1 and R["clear"]["strokes"] == []
    ok("toolbar: colour and width are saved; eraser strokes erase; undo removes the last stroke; clear empties")
    off = R["off"]
    assert off["on"] is False and off["pressed"] == "false" and off["strokes"] == [] and {"on": False} in off["saved"]
    assert R["misc"]["display"] == "none" and R["misc"]["bardisplay"] == "none" and R["misc"]["pd"] > 0
    ok("toggle off: canvas and toolbar hidden (card gets taps directly), drawing ignored, saved")
    p = subprocess.run([node, "--check", f], capture_output=True, text=True)
    assert p.returncode == 0
else:
    print("SKIP pointer simulation (no node found)")
print("ALL WHITEBOARD TESTS PASSED")
