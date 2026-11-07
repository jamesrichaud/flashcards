"""Statistics page: every Anki graph from the engine's `graphs` call, scope (deck / collection /
search), time range, calendar years, toggles, browse links and the FSRS vs SM-2 graphs."""
import datetime, html, os, re, sys, tempfile, time
import xml.etree.ElementTree as ET
os.environ["DATA_DIR"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fastapi.testclient import TestClient
import app as webapp
from anki.scheduler.v3 import CardAnswer

ok = lambda m: print("PASS", m)
c = TestClient(webapp.app)
c.post("/login", data={"username": "stat", "password": "secret123"})


def text(r):
    """Visible text of a page, roughly."""
    t = re.sub(r"<(script|style)\b.*?</\1>", " ", r.text, flags=re.S)
    t = re.sub(r"<title>.*?</title>", " ", t, flags=re.S)
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t)))


def section(r, title):
    m = re.search(r'<section class="box graph[^"]*"><h3>' + re.escape(title) + r"</h3>(.*?)</section>", r.text, re.S)
    assert m, title
    return m.group(1)


def flash(r):
    m = re.search(r'<div class="flash( err)?">(.*?)</div>', r.text, re.S)
    return ("!" if m and m.group(1) else "") + html.unescape(m.group(2)) if m else ""


# ---------- data ----------
with webapp.collection("stat") as col:
    basic = col.models.by_name("Basic")
    langs = col.decks.id("Languages")
    french = col.decks.id("Languages::French")
    maths = col.decks.id("Maths")
    nids = {}
    for did, n_notes, tag in [(french, 6, "verbs"), (maths, 4, "algebra")]:
        for i in range(n_notes):
            n = col.new_note(basic); n["Front"] = f"{tag} {i}"; n["Back"] = "x"; n.tags = [tag]
            col.add_note(n, did); nids.setdefault(did, []).append(n.id)
    fr_cards = col.find_cards(f"did:{french}")
    ma_cards = col.find_cards(f"did:{maths}")
    # Study three French cards today, through the real scheduler.
    col.decks.select(french)
    for rating in (CardAnswer.EASY, CardAnswer.EASY, CardAnswer.AGAIN):
        q = col.sched.get_queued_cards()
        card = col.get_card(q.cards[0].card.id); card.start_timer()
        col.sched.answer_card(col.sched.build_answer(card=card, states=q.cards[0].states, rating=rating))
    # Older history written straight into the review log (as an import from desktop Anki would):
    # 10 days ago (2 reviews), 200 days ago (1), and 400 days ago (1, last calendar year or before).
    cutoff = col.sched.day_cutoff
    old = [(10, ma_cards[0], 3, 1), (10, ma_cards[1], 1, 1), (200, ma_cards[2], 3, 1), (400, ma_cards[3], 4, 1)]
    for j, (days, cid, ease, typ) in enumerate(old):
        rid = (cutoff - 86400 * days - 7200) * 1000 + j
        col.db.execute("insert into revlog (id,cid,usn,ease,ivl,lastIvl,factor,time,type) values (?,?,?,?,?,?,?,?,?)",
                       rid, cid, -1, ease, 5, 2, 2500, 8000, typ)
    col.sched.suspend_cards([ma_cards[0]])
    col.sched.bury_cards([ma_cards[1]])
    # One review card due tomorrow in Maths.
    col.sched.set_due_date([ma_cards[2]], "1")
    today = datetime.date.fromtimestamp(cutoff - 86400)

# ---------- login, links ----------
c2 = TestClient(webapp.app)
assert c2.get("/stats", follow_redirects=False).status_code == 303; ok("stats needs a login")
assert f'href="/stats?did={french}"' in c.get(f"/deck/{french}").text; ok("deck overview links to its stats")

# ---------- whole collection ----------
r = c.get("/stats", params={"did": 0})
assert r.status_code == 200
t = text(r)
for title in ["Today", "Future Due", "Calendar", "Reviews", "Card Counts", "Review Intervals", "Card Difficulty",
              "Card Stability", "Card Retrievability", "Hourly Breakdown", "Answer Buttons", "Added", "Retention"]:
    assert f"<h3>{title}</h3>" in r.text, title
assert "<h3>Card Ease</h3>" not in r.text
ok("FSRS collection shows all of Anki's graphs (difficulty/stability/retrievability, no ease)")
for svg in re.findall(r"<svg\b.*?</svg>", r.text, re.S):
    ET.fromstring(svg)
ok("every chart is well-formed SVG")

today_s = section(r, "Today")
assert "Studied 3 cards" in text(r) and "Again count: 1 (33.33%)" in html.unescape(today_s)
assert "Learn: 3, Review: 0, Relearn: 0, Filtered: 0" in html.unescape(today_s)
ok("today: studied, again count and type counts from the engine")

counts = html.unescape(section(r, "Card Counts"))
# Separate suspended/buried is on by default (Anki's preference): 10 cards, 1 suspended, 1 buried.
sep = re.search(r'<div class="vc">(.*?)</div></div>', counts, re.S).group(1)
assert re.search(r"Suspended</th><td>1<", sep) and re.search(r"Buried</th><td>1<", sep)
assert re.search(r"Total cards</th><td>10<", sep)
with webapp.collection("stat") as col:
    n_new = len(col.find_cards("is:new -is:suspended -is:buried"))
assert re.search(rf"New</th><td>{n_new}<", sep)
assert 'class="tc" id="' in counts and "checked>" in counts
ok("card counts: new/learning/young/suspended/buried and total, separate toggle on by default")

assert "Total cards" in t and "Studied" in t; ok("old stats wording kept (Studied, Total cards)")

fd = html.unescape(section(r, "Future Due"))
assert re.search(r"Due tomorrow</th><td>1 review<", fd); ok("future due: due tomorrow from the engine")

# ---------- range ----------
def days_studied(r):
    s = html.unescape(section(r, "Reviews"))
    return re.search(r"Days studied</th><td>(\d+) of (\d+)", s).groups()

assert days_studied(c.get("/stats", params={"did": 0, "range": "1m"})) == ("2", "31")
assert days_studied(c.get("/stats", params={"did": 0, "range": "3m"})) == ("2", "90")
assert days_studied(c.get("/stats", params={"did": 0, "range": "1y"})) == ("3", "365")
r_all = c.get("/stats", params={"did": 0, "range": "all"})
assert days_studied(r_all) == ("4", "401")
assert 'class="btn on" href="/stats?did=0&amp;range=all"' in r_all.text
assert days_studied(c.get("/stats", params={"did": 0, "range": "nonsense"})) == ("2", "31")
ok("time range 1 month / 3 months / 1 year / all changes the reviews span")

# Hours and buttons use the engine's per-range buckets.
b1 = html.unescape(section(c.get("/stats", params={"did": 0, "range": "1m"}), "Answer Buttons"))
ball = html.unescape(section(r_all, "Answer Buttons"))
assert "Learning</th><td>Correct: 2/3" in b1
assert "Young</th><td>" in ball or "Mature</th><td>" in ball or "Learning</th><td>Correct: 4/6" in ball
ok("answer buttons follow the range")
assert "Hourly Breakdown" in r_all.text and "NO DATA" not in section(r_all, "Hourly Breakdown")
ok("hourly breakdown has data")

# ---------- scope ----------
rd = c.get("/stats", params={"did": maths})
cd = html.unescape(section(rd, "Card Counts"))
assert re.search(r"Total cards</th><td>4<", cd)
assert "Studied 3 cards" not in text(rd) and "No cards have been studied today." in text(rd)
assert f'<option value="{maths}" selected>' in rd.text
ok("deck scope: only that deck's cards and reviews")
rp = c.get("/stats", params={"did": langs})
assert re.search(r"Total cards</th><td>6<", html.unescape(section(rp, "Card Counts")))
assert "Studied 3 cards" in text(rp); ok("deck scope includes subdecks")
with webapp.collection("stat") as col:
    col.decks.select(maths)
rc = c.get("/stats")
assert f'<option value="{maths}" selected>' in rc.text and re.search(
    r"Total cards</th><td>4<", html.unescape(section(rc, "Card Counts")))
ok("without a choice, the current deck is shown (as in Anki)")
rs = c.get("/stats", params={"q": "tag:verbs", "did": 0})
assert re.search(r"Total cards</th><td>6<", html.unescape(section(rs, "Card Counts")))
assert 'value="tag:verbs"' in rs.text and "Search: tag:verbs" in text(rs); ok("search scope")
rb = c.get("/stats", params={"q": "prop:nonsense=(", "did": 0})
assert rb.status_code == 200 and flash(rb).startswith("!")
assert re.search(r"Total cards</th><td>10<", html.unescape(section(rb, "Card Counts")))
ok("a bad search shows the engine's error and falls back to the collection")
rx = c.get("/stats", params={"did": 999999})
assert re.search(r"Total cards</th><td>10<", html.unescape(section(rx, "Card Counts"))); ok("unknown deck -> collection")

# ---------- calendar ----------
cal = section(r, "Calendar")
assert f"<b>{today.year}</b>" in cal
studied_this_year = sum(1 for d in (0, 10, 200, 400) if (today - datetime.timedelta(days=d)).year == today.year)
assert f"Days studied: {studied_this_year}" in html.unescape(cal)
assert "Previous year" in cal and "Next year" not in cal
prev = today.year - 1
rprev = c.get("/stats", params={"did": 0, "year": prev})
calp = html.unescape(section(rprev, "Calendar"))
assert f"<b>{prev}</b>" in calp and "Next year" in calp
studied_prev = sum(1 for d in (0, 10, 200, 400) if (today - datetime.timedelta(days=d)).year == prev)
assert f"Days studied: {studied_prev}" in calp
first_year = (today - datetime.timedelta(days=400)).year
assert "Previous year" not in section(c.get("/stats", params={"did": 0, "year": first_year}), "Calendar")
assert f"<b>{today.year}</b>" in section(c.get("/stats", params={"did": 0, "year": 1990}), "Calendar")
ok("calendar: year view, days studied, previous/next year")

# ---------- retention ----------
ret = html.unescape(section(r_all, "Retention"))
assert "Today" in ret and "All time" in ret and "N/A" in ret
ok("true retention table")

# ---------- browse links are valid searches ----------
queries = set(html.unescape(q) for q in re.findall(r'data-q="([^"]*)"', r_all.text + rd.text + rs.text))
assert queries
with webapp.collection("stat") as col:
    for q in queries:
        col.find_cards(q)
    due1 = col.find_cards(f"prop:due=1")
assert any("prop:due=1" == q for q in queries) and len(due1) == 1
assert any(re.match(r'^\("?deck:Maths"?\) ', q) for q in queries), sorted(queries)[:5]
ok(f"all {len(queries)} browse links are valid engine searches")
with webapp.collection("stat") as col:
    assert len(col.find_cards("rated:1")) == 3                       # today's bar
    assert sorted(col.find_cards("prop:rated=-10")) == sorted(ma_cards[:2])
    g = col._backend.graphs(search="", days=0)
    # Each difficulty/retrievability bucket's link finds as many cards as the bucket counts.
    for hist, prop in [(dict(g.difficulty.eases), "d"), (dict(g.retrievability.retrievability), "r")]:
        for a in range(0, 100, 5):
            top = a == 95                        # the top bucket includes 100%, as on the page
            n = sum(v for k, v in hist.items() if a <= k and (top or k < a + 5))
            q = f"-is:new -is:suspended prop:{prop}>={a / 100:g}" + ("" if top else f" prop:{prop}<{(a + 5) / 100:g}")
            found = len(col.find_cards(q))
            assert found >= n, (prop, a, n, found)
ok("browse links match the bars (rated days, difficulty and retrievability buckets)")

# ---------- SM-2 ----------
c.post("/settings/fsrs")
with webapp.collection("stat") as col:
    assert col.get_config("fsrs") is False
r2 = c.get("/stats", params={"did": 0, "range": "all"})
assert "<h3>Card Ease</h3>" in r2.text and "<h3>Card Difficulty</h3>" not in r2.text
assert "Average ease" in r2.text
c.post("/settings/fsrs")
ok("SM-2 collection shows Card Ease instead of the FSRS graphs")

# ---------- empty collection ----------
c3 = TestClient(webapp.app)
webapp.create_user("empty", "secret123")
c3.post("/login", data={"username": "empty", "password": "secret123"})
re_ = c3.get("/stats")
assert re_.status_code == 200 and "No cards have been studied today." in text(re_) and "NO DATA" in re_.text
for rng in ["1m", "3m", "1y", "all"]:
    assert c3.get("/stats", params={"range": rng, "did": 0}).status_code == 200
ok("empty collection renders with NO DATA")
print("ALL STATS TESTS PASSED")
