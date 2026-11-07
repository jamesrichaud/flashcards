"""Interface languages: t()/tn(), language files complete, picker, Accept-Language, engine text,
and every page rendering in every language (the regression crawler, once per language)."""
import json, os, re, string, subprocess, sys, tempfile
os.environ["DATA_DIR"] = tempfile.mkdtemp()
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from fastapi.testclient import TestClient
import app as webapp
from flashcards import i18n
from flashcards.i18n import extract

ok = lambda m: print("PASS", m)
CODES = list(i18n.LANGUAGES)
assert CODES == ["en", "es", "fr", "de", "pt", "it", "ga", "pl"]

# ---------- the helpers ----------
assert i18n.from_accept_language("fr-FR,fr;q=0.9,en;q=0.8") == "fr"
assert i18n.from_accept_language("en-GB;q=0.5, de;q=0.9") == "de"
assert i18n.from_accept_language("pt-BR") == "pt"
assert i18n.from_accept_language("ja, zh;q=0.8") == ""
assert i18n.from_accept_language("ga;q=0, pl") == "pl"
assert i18n.from_accept_language("") == ""
ok("Accept-Language picks the best shipped language")

assert [i18n.PLURAL["pl"](n) for n in (1, 2, 4, 5, 12, 22, 25, 112)] == [0, 1, 1, 2, 2, 1, 2, 2]
assert [i18n.PLURAL["ga"](n) for n in (1, 2, 3, 7, 11)] == [0, 1, 2, 3, 4]
assert i18n.PLURAL["fr"](0) == 0 and i18n.PLURAL["en"](0) == 1
with i18n.use_lang("en"):
    assert i18n.tn("{n} card", "{n} cards", 1) == "1 card" and i18n.tn("{n} card", "{n} cards", 5) == "5 cards"
    assert i18n.t("Hello {name}", name="Ann") == "Hello Ann"
    assert i18n.t("Not a key {x}") == "Not a key {x}"   # missing value: text left as is, no crash
ok("plural rules (Polish, Irish, French) and placeholders")

# ---------- the language files ----------
en, problems = extract.english()
assert not problems, "\n".join(problems)
with open(os.path.join(ROOT, "flashcards", "i18n", "en.json"), encoding="utf-8") as f:
    saved_en = json.load(f)
assert saved_en == en, "en.json is out of date: run python3 flashcards/i18n/extract.py"
assert len(en) > 800, f"only {len(en)} interface strings found"

bad = []
for code in CODES[1:]:
    bad += extract.problems_for(code, en)
assert not bad, "\n".join(bad[:40]) + f"\n({len(bad)} problems)"
ok(f"{len(en)} strings: every key in every language file, plural forms and placeholders match")

# translations really differ from English for the main words
for code in CODES[1:]:
    with i18n.use_lang(code):
        assert i18n.t("Decks") != "Decks" and i18n.t("Settings") != "Settings", code
ok("the main navigation is translated in every language")

# ---------- picking a language ----------
c = TestClient(webapp.app)
r = c.get("/login", headers={"Accept-Language": "es-ES,es;q=0.9"})
assert '<html lang="es">' in r.text
with i18n.use_lang("es"):
    assert i18n.t("Username") in r.text and i18n.t("Username") != "Username"
r = c.get("/login")
assert "<html>" in r.text and "Username" in r.text
ok("logged out: language from the browser's Accept-Language, English otherwise")

c.post("/login", data={"username": "lang", "password": "secret123"})
r = c.get("/preferences", headers={"Accept-Language": "it"})
assert '<select id="lang" name="lang">' in r.text
for code, (name, _) in i18n.LANGUAGES.items():
    assert f'<option value="{code}"' in r.text and name in r.text
assert '<option value="" selected>' in r.text and "Italiano" in r.text
ok("Preferences has a language picker: Automatic plus the eight languages")

prefs = {"rollover": "4", "learn_ahead": "20", "timebox": "0", "theme": "system", "show_counts": "1", "show_times": "1"}
r = c.post("/preferences", data=dict(prefs, lang="de"), headers={"Accept-Language": "fr"})
assert '<html lang="de">' in r.text and "Einstellungen" in r.text   # engine's own word for Preferences
assert webapp.user_lang("lang") == "de"
assert re.search(r'<option value="de"[^>]* selected>', r.text)
with i18n.use_lang("de"):
    assert i18n.t("Preferences saved.") in r.text and i18n.t("Decks") in r.text
ok("choosing German: saved per person, beats the browser's language, engine labels in German")

with webapp.collection("lang") as col:
    did = col.decks.id("Deutsch")
    n = col.new_note(col.models.by_name("Basic")); n["Front"] = "Hund"; n["Back"] = "dog"
    col.add_note(n, did)
    msg = col.studied_today()
assert "Karten" in msg or "gelernt" in msg, msg
r = c.get(f"/study/{did}")
cid = re.search(r'name="card_id" value="(\d+)"', r.text).group(1)
r = c.get(f"/study/{did}?cid={cid}&shown=1")
assert r.status_code == 200 and '<html lang="de">' in r.text
assert re.search(r"\b\d+\s?(Min|Tg)", r.text), "answer button intervals should be in German"
ok("the engine itself speaks German after the change (studied today, answer intervals)")

# another person isn't affected; password change keeps the language
c2 = TestClient(webapp.app)
c.post("/settings/adduser", data={"username": "other", "pw": "secret123"})
c2.post("/login", data={"username": "other", "password": "secret123"})
assert "<html>" in c2.get("/").text
c.post("/settings/password", data={"pw": "newsecret1"})
assert webapp.user_lang("lang") == "de"
ok("language is per person and survives a password change")

r = c.post("/preferences", data=dict(prefs, lang=""), headers={"Accept-Language": "pl"})
assert webapp.user_lang("lang") == "" and '<html lang="pl">' in r.text
r = c.post("/preferences", data=dict(prefs, lang="xx"))
assert webapp.user_lang("lang") == "" and "<html>" in r.text
ok("Automatic follows the browser again; unknown codes are ignored")

# reminders and other background work can run in a person's language
with i18n.use_lang("fr"):
    assert i18n.current() == "fr" and i18n.t("Decks") != "Decks"
assert i18n.current() == "en"
ok("use_lang() for work outside a request")

# ---------- every page in every language ----------
for code in CODES[1:]:
    r = subprocess.run([sys.executable, os.path.join(ROOT, "tests", "test_regression.py")], cwd=ROOT,
                       env=dict(os.environ, FC_TEST_LANG=code, DATA_DIR=tempfile.mkdtemp()),
                       capture_output=True, text=True)
    assert r.returncode == 0, f"{code}:\n" + (r.stdout + r.stderr)[-3000:]
    assert "ALL REGRESSION TESTS PASSED" in r.stdout
ok("the regression crawler passes in " + ", ".join(CODES[1:]) + " (pages load, no errors, scripts parse)")
print("ALL I18N TESTS PASSED")
