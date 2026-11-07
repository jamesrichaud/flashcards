"""Round 3 regression: layout fixes found while checking the offline pages by hand.

1. Offline study page: the "Offline · N answers waiting to sync" banner and the header were both
   sticky at the top, so after scrolling the banner covered the header's Decks/Add links. They now
   stick together as one block.
2. Normal pages: the red "You're offline" bar is fixed at the bottom and covered the study screen's
   Show answer / answer buttons (which still work offline: the service worker hands the answer to the
   offline page). The bar's height is now kept in --fc-offbar and the sticky answer bar sits above it.
"""
import json, os, re, shutil, subprocess, sys, tempfile
os.environ["DATA_DIR"] = tempfile.mkdtemp()
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from fastapi.testclient import TestClient
import app as webapp
from flashcards import touch

ok = lambda m: print("PASS", m)
c = TestClient(webapp.app)

# ---------- 1. offline study page: header and banner stick together ----------
r = c.get("/offline/study")
assert r.status_code == 200
top = re.search(r'<div id="fco-top"><header>.*?</header>\s*<div id="fco-banner"[^>]*></div></div>', r.text, re.S)
assert top, "header and banner share one sticky block"
css = re.search(r"<style>(.*?)</style>", r.text, re.S).group(1)
assert "#fco-top{position:sticky;top:0;z-index:6}#fco-top header{position:static}" in css
banner_rule = re.search(r"#fco-banner\{[^}]*\}", css.split("#fco-top header{position:static}")[1]).group(0)
assert "sticky" not in banner_rule and "background:#8a5a00;color:#fff" in banner_rule
ok("offline page: banner sits under the header instead of covering it")

# ---------- 2. normal pages: the answer bar stays above the red offline bar ----------
c.post("/login", data={"username": "lay", "password": "secret123"})
page = c.get("/").text
assert 'id="fc-offline"' in page and "--fc-offbar" in page
assert "body main .bar{bottom:var(--fc-offbar,0px)}" in touch.CSS
# more specific than STUDY_CSS's .bar{...bottom:0}, which comes later in the page
assert ".bar{position:sticky;bottom:0" in webapp.STUDY_CSS

# ---------- 3. "N answers were skipped" message uses the app's red box (defined on every page) ----------
client = c.get("/offline/client.js").text
assert "box.className = 'flash' + (n.warn ? ' err' : '')" in client
assert ".err{background:#fbe9e9;color:#8a1f1f}" in webapp.CSS
ok("sync warnings show in the app's red message box on every page")

script = re.search(r'<div id="fc-offline".*?<script>(.*?)</script>', page, re.S).group(1)
node = r"""
const script = %s;
function run(online, height) {
  const props = {}, listeners = {};
  const b = {hidden: true, get offsetHeight() { return this.hidden ? 0 : height; }};
  const ctx = {
    document: {getElementById: () => b, documentElement: {style: {setProperty: (k, v) => props[k] = v}}},
    window: {addEventListener: (n, f) => listeners[n] = f, isSecureContext: false},
    navigator: {onLine: online},
  };
  new Function('document', 'window', 'navigator', script)(ctx.document, ctx.window, ctx.navigator);
  const first = props['--fc-offbar'], hidden = b.hidden;
  ctx.navigator.onLine = true; listeners.online();
  return {first, hidden, after: props['--fc-offbar'], afterHidden: b.hidden, resize: !!listeners.resize};
}
console.log(JSON.stringify([run(false, 58), run(true, 58)]));
""" % json.dumps(script)
NODE = None
try:   # Playwright ships node (as the other node-based tests use)
    from playwright._impl._driver import compute_driver_executable
    NODE = compute_driver_executable()[0]
except Exception:
    NODE = shutil.which("node")
if not (NODE and os.path.exists(NODE)):
    print("SKIP offline bar script in node (no node found)")
    sys.exit(0)
out = subprocess.run([NODE, "-e", node], capture_output=True, text=True)
assert out.returncode == 0, out.stderr
off, on = json.loads(out.stdout)
assert off == {"first": "58px", "hidden": False, "after": "0px", "afterHidden": True, "resize": True}, off
assert on["first"] == "0px" and on["hidden"] is True, on
ok("normal pages: offline bar height kept in --fc-offbar (0 when online), answer bar lifted above it")
