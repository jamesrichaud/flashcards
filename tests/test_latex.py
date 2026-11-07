"""LaTeX on cards ([latex], [$], [$$]): source fallback when LaTeX is off or not installed,
image generation (with fake latex/dvipng/dvisvgm programs on PATH, and the real ones if the
server has them), security limits, Preferences toggle and Check Media's Render LaTeX."""
import html, os, re, stat, sys, tempfile, threading
os.environ["DATA_DIR"] = tempfile.mkdtemp()
os.environ["REMINDERS"] = "0"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import shutil
REAL_LATEX = shutil.which("latex") and shutil.which("dvipng")
ORIG_PATH = os.environ.get("PATH", "")
from fastapi.testclient import TestClient
import app as webapp
from anki import hooks
import anki.latex as engine_latex
from flashcards import latex as L

ok = lambda m: print("PASS", m)
c = TestClient(webapp.app)
c.post("/login", data={"username": "lat", "password": "secret123"})


def flash(r):
    m = re.search(r'<div class="flash( err)?">(.*?)</div>', r.text, re.S)
    return ("!" if m and m.group(1) else "") + html.unescape(m.group(2)) if m else ""


# ---------- fake LaTeX programs ----------
EMPTY = tempfile.mkdtemp()     # PATH with no latex at all
FAKE = tempfile.mkdtemp()
CALLS = os.path.join(FAKE, "calls.log")
PNG = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                    "1f15c4890000000d49444154789c6360f8cf00000301010018dd8db00000000049454e44ae426082")
FAKE_LATEX = f"""#!{sys.executable}
import os, sys, time
tex = open("tmp.tex", encoding="utf8").read()
with open({CALLS!r}, "a") as f:
    f.write("latex " + " ".join(sys.argv[1:]) + " | " + os.getcwd() + " | " + os.environ.get("openin_any", "")
            + " | " + os.environ.get("openout_any", "") + "\\n")
print("This is a fake TeX")
if "SLOWME" in tex:
    time.sleep(5)
if "FAILME" in tex:
    print("! Undefined control sequence.")
    sys.exit(1)
open("tmp.dvi", "w").write(tex)
"""
FAKE_DVIPNG = f"""#!{sys.executable}
import sys
with open({CALLS!r}, "a") as f:
    f.write("dvipng " + " ".join(sys.argv[1:]) + "\\n")
open(sys.argv[sys.argv.index("-o") + 1], "wb").write({PNG!r} + open("tmp.dvi", "rb").read())
"""
FAKE_DVISVGM = f"""#!{sys.executable}
import sys
with open({CALLS!r}, "a") as f:
    f.write("dvisvgm " + " ".join(sys.argv[1:]) + "\\n")
open(sys.argv[sys.argv.index("-o") + 1], "w").write("<svg xmlns='http://www.w3.org/2000/svg'></svg>")
"""
for name, src in [("latex", FAKE_LATEX), ("dvipng", FAKE_DVIPNG), ("dvisvgm", FAKE_DVISVGM)]:
    p = os.path.join(FAKE, name)
    open(p, "w").write(src)
    os.chmod(p, os.stat(p).st_mode | stat.S_IXUSR)


def use_path(which):
    os.environ["PATH"] = which + os.pathsep + "/usr/bin:/bin" if which != EMPTY else EMPTY


def calls():
    return open(CALLS).read().splitlines() if os.path.exists(CALLS) else []


with webapp.collection("lat") as col:
    basic = col.models.by_name("Basic")
    def add(front, back="b", model=basic):
        n = col.new_note(model); n[model["flds"][0]["name"]] = front
        if len(model["flds"]) > 1: n[model["flds"][1]["name"]] = back
        col.add_note(n, 1); return n.id
    nid1 = add("x [$]a<b[/$] y [latex]\\textbf{hi}[/latex] z [$$]\\int f[/$$]")
    media_dir = col.media.dir()

def q(nid, side="question"):
    with webapp.collection("lat") as col:
        card = col.get_note(nid).cards()[0]
        return card.question() if side == "question" else card.answer()

def latex_files():
    return sorted(f for f in os.listdir(media_dir) if f.startswith("latex-"))

# ---------- hook installed in place of the engine's ----------
assert L.on_card_did_render in hooks.card_did_render._hooks
assert engine_latex.on_card_did_render not in hooks.card_did_render._hooks
assert engine_latex._save_latex_image is L.save_latex_image
L.install(); assert hooks.card_did_render._hooks.count(L.on_card_did_render) == 1
ok("our render hook and image maker replace the engine's (once)")

# ---------- off by default (as in Anki): source shown, no image, no LaTeX run ----------
use_path(FAKE)
with webapp.collection("lat") as col:
    assert not L.enabled(col)
t = q(nid1)
assert "<img class=latex" not in t and t.count('class="latex-source"') == 3, t
assert '<code class="latex-source" style="white-space:pre-wrap">&#x24;a&lt;b&#x24;</code>' in t
assert "disabled in the preferences" in t and "Preferences" in t and t.count('class="latex-error"') == 1
assert calls() == [] and latex_files() == []
ok("generation off (Anki's default): cards show the LaTeX source and say how to turn it on")

# ---------- Preferences toggle ----------
r = c.get("/preferences")
assert 'name="render_latex"' in r.text and "Generate LaTeX images (security risk)" in r.text
assert 'name="render_latex" value="1" checked' not in r.text
prefs = {"rollover": "4", "learn_ahead": "20", "timebox": "0", "theme": "system", "show_counts": "1"}
r = c.post("/preferences", data={**prefs, "render_latex": "1"})
assert flash(r) == "Preferences saved."
with webapp.collection("lat") as col:
    assert L.enabled(col)
assert 'name="render_latex" value="1" checked' in c.get("/preferences").text
c.post("/preferences", data=prefs)
with webapp.collection("lat") as col:
    assert not L.enabled(col)
    L.set_enabled(col, True)
ok("Preferences turns the engine's Generate LaTeX images setting on and off")

# ---------- on, but LaTeX not installed ----------
use_path(EMPTY)
assert not L.installed()
assert "LaTeX isn't installed on this server" in c.get("/preferences").text
t = q(nid1)
assert t.count('class="latex-source"') == 3 and "<img class=latex" not in t
assert t.count("LaTeX isn&#x27;t installed on the server (latex and dvipng)") == 1, t
a = q(nid1, "answer")
assert "latex-source" in a
r = c.get("/study/1")
assert r.status_code == 200 and "latex-source" in r.text
assert c.get(f"/edit/{nid1}").status_code == 200
r = c.get("/browse")
assert r.status_code == 200
assert latex_files() == []
ok("on but not installed: source with a clear 'LaTeX isn't installed' note; study, edit and browse still work")

# ---------- installed (fake programs) ----------
use_path(FAKE)
assert L.installed() and L.installed(svg=True)
cwd = os.getcwd()
t = q(nid1)
assert t.count("<img class=latex") == 3 and "latex-source" not in t and "latex-error" not in t, t
files = latex_files()
assert len(files) == 3 and all(f.endswith(".png") for f in files)
for f in files:
    assert open(os.path.join(media_dir, f), "rb").read().startswith(PNG)
assert os.getcwd() == cwd
lc = [l for l in calls() if l.startswith("latex ")]
assert len(lc) == 3
for l in lc:
    args, where, inp, outp = l.split(" | ")
    assert "-no-shell-escape" in args and "-interaction=nonstopmode" in args
    assert inp == "p" and outp == "p"
    assert os.path.basename(where).startswith("fc-latex-") and not os.path.exists(where)
assert any("-bg Transparent -D 200 -T tight tmp.dvi -o tmp.png" in l for l in calls())
n = len(calls())
q(nid1); q(nid1, "answer")
assert len(calls()) == n
ok("installed: images made into the media folder in a private temp folder (no chdir, paranoid file access, "
   "no shell escape) and reused afterwards")

# the image is served and the card page uses it
r = c.get("/media/" + files[0])
assert r.status_code == 200 and r.content.startswith(PNG)
assert "img.latex{filter:invert(100%)}" in webapp.CARD_DARK_CSS and "latex" in webapp.card_doc("x", "dark")
ok("images served from /media; dark mode inverts them like Anki's reviewer")

# ---------- header/footer from the note type; svg note types use dvisvgm ----------
with webapp.collection("lat") as col:
    m = col.models.copy(col.models.by_name("Basic"), add=False)
    m["name"] = "Basic SVG"; m["latexsvg"] = True; m["latexPre"] += "%MYHEADER\n"
    col.models.add_dict(m)
    svg_model = col.models.by_name("Basic SVG")
    nid_svg = add("[$]e=mc^2[/$]", model=svg_model)
t = q(nid_svg)
assert re.search(r'<img class=latex alt="[^"]*" src="latex-[0-9a-f]+\.svg">', t), t
assert any(l.startswith("dvisvgm ") for l in calls())
svgs = [f for f in latex_files() if f.endswith(".svg")]
assert len(svgs) == 1 and open(os.path.join(media_dir, svgs[0])).read().startswith("<svg")
ok("note types set to SVG use dvisvgm")

# ---------- banned commands, LaTeX errors, time limit ----------
with webapp.collection("lat") as col:
    nid_bad = add("[latex]\\input{/etc/passwd}[/latex]")
    nid_fail = add("[$]FAILME[/$]")
    nid_slow = add("[$]SLOWME[/$]")
before = len(calls())
t = q(nid_bad)
assert "For security reasons, &#x27;\\input&#x27; is not allowed on cards" in t and "latex-source" in t, t
assert len(calls()) == before
t = q(nid_fail)
assert "Error executing latex." in t and "Undefined control sequence" in t and "<details>" in t and "latex-source" in t
old_timeout, L.TIMEOUT = L.TIMEOUT, 1
t = q(nid_slow)
L.TIMEOUT = old_timeout
assert "Stopped after 1 seconds." in t and "latex-source" in t
ok("refuses \\input etc. like older Anki, shows LaTeX errors with the log, stops LaTeX that runs too long")

# ---------- several requests at once ----------
with webapp.collection("lat") as col:
    many = [add(f"[$]x_{{{i}}}[/$]") for i in range(6)]
errs = []
def worker(nid):
    try:
        with webapp.collection("lat") as col:
            out = col.get_note(nid).cards()[0].question()
            assert "<img class=latex" in out and "latex-error" not in out, out
    except Exception as e:
        errs.append(e)
ths = [threading.Thread(target=worker, args=(n,)) for n in many]
[t.start() for t in ths]; [t.join() for t in ths]
assert not errs, errs
assert os.getcwd() == cwd
ok("several cards rendered at once from different threads")

# ---------- Check Media: Render LaTeX ----------
with webapp.collection("lat") as col:
    col.remove_notes([nid_bad, nid_fail, nid_slow])
    cloze = col.models.by_name("Cloze")
    nid_cl = add("{{c1::[$]y=x^3[/$]}} and {{c2::two}}", model=cloze)
    want = [l.filename for l in col._backend.extract_latex(
        text=col.get_note(nid_cl).fields[0], svg=False, expand_clozes=True).latex]
assert want and not any(os.path.exists(os.path.join(media_dir, f)) for f in want)
r = c.get("/check-media")
assert 'id="latex"' in r.text and "Render LaTeX" in r.text and 'action="/check-media/latex"' in r.text
r = c.post("/check-media/latex")
assert flash(r) == "All LaTeX rendered.", flash(r)
assert all(os.path.exists(os.path.join(media_dir, f)) for f in want)
assert not any("latex-" in f for f in re.findall(r'value="([^"]+)"', r.text)), "latex images aren't 'unused'"
ok("Check Media's Render LaTeX makes every missing image (cloze text too)")

with webapp.collection("lat") as col:
    nid_fail = add("[$]FAILME again[/$]")
r = c.post("/check-media/latex", follow_redirects=False)
assert r.status_code == 303 and f"latex_error={nid_fail}" in r.headers["location"]
r = c.get(r.headers["location"])
assert 'id="latex-error"' in r.text and f'href="/edit/{nid_fail}?back=/check-media"' in r.text
assert "Undefined control sequence" in r.text
ok("a note that fails is shown with its LaTeX log and links to edit it")

use_path(EMPTY)
r = c.get("/check-media")
assert "LaTeX isn&#x27;t installed on the server" in r.text or "LaTeX isn't installed on the server" in r.text
r = c.post("/check-media/latex")
assert flash(r) == "!LaTeX isn't installed on the server.", flash(r)
with webapp.collection("lat") as col:
    L.set_enabled(col, False)
r = c.post("/check-media/latex")
assert flash(r) == "!LaTeX image generation is disabled in the preferences.", flash(r)
use_path(FAKE)
t = c.get("/check-media").text
assert "disabled in the preferences" in t and 'href="/preferences"' in t
use_path(EMPTY)
c2 = TestClient(webapp.app)
assert c2.post("/check-media/latex", follow_redirects=False).status_code == 303
ok("Render LaTeX explains when LaTeX is off or not installed; needs a login")

# ---------- real LaTeX, when the server has it ----------
os.environ["PATH"] = ORIG_PATH
if REAL_LATEX:
    with webapp.collection("lat") as col:
        L.set_enabled(col, True)
        nid_real = add("[$]\\frac{a}{b}+\\sqrt{2}[/$]")
    t = q(nid_real)
    assert "<img class=latex" in t and "latex-error" not in t, t
    ok("real latex + dvipng make an image")
else:
    print("SKIP real latex + dvipng: not installed here")

print("ALL LATEX TESTS PASSED")
