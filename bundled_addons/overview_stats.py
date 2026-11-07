"""More numbers on the deck overview: total, new, learning, young, mature, suspended, due tomorrow, time today.

Like the desktop add-on "More Overview Stats". All counts come from the engine's own searches
(the same ones Browse uses), so a parent deck includes its subdecks. "Time today" is today's
remaining cards times your average answer time in this deck over the last 30 days.
"""
import html, time
from urllib.parse import quote

from anki.collection import SearchNode
from anki.utils import ids2str

from flashcards.i18n import t, N_

NAME = "More Overview Stats"   # plain literal: flashcards/addons.py reads it without importing
N_("More Overview Stats")      # ...so mark it for translation here
N_('More numbers on the deck overview: total, new, learning, young, mature, suspended, due tomorrow, time today.')
DEFAULT_SECS = 8.0     # per card when the deck has no recent reviews (Anki's own estimate is similar)

# label, search added to the deck's search
COUNTS = [(N_("Total"), ""), (N_("New"), "is:new"), (N_("Learning"), "is:learn"),
          (N_("Young"), "-is:new -is:learn prop:ivl<21"), (N_("Mature"), "prop:ivl>=21"),
          (N_("Suspended"), "is:suspended"), (N_("Due tomorrow"), "prop:due=1")]


def numbers(col, did):
    deck = col.decks.get(did, default=False)
    if not deck:
        return None
    scope = col.build_search_string(SearchNode(deck=deck["name"]))
    counts = []
    for label, extra in COUNTS:
        q = f"{scope} {extra}".strip()
        counts.append((label, len(col.find_cards(q)), q))
    new, lrn, rev = col.sched.counts()
    left = new + lrn + rev
    cids = col.find_cards(scope)
    since = int((time.time() - 30 * 86400) * 1000)
    row = col.db.first(f"select count(), sum(time) from revlog where id > ? and cid in {ids2str(cids)} "
                       f"and time > 0", since) if cids else None
    per = (row[1] / 1000 / row[0]) if row and row[0] else DEFAULT_SECS
    per = min(per, 60.0)    # Anki caps an answer's time at 60 s by default
    return {"counts": counts, "left": left, "per": per, "secs": left * per}


def minutes(secs):
    m = round(secs / 60)
    if secs and m == 0:
        return t("under a minute")
    return t("{h} h {m} min", h=m // 60, m=m % 60) if m >= 60 else t("{m} min", m=m)


def deck_overview(col, did):
    s = numbers(col, did)
    if not s:
        return ""
    e = lambda x: html.escape(x, quote=False)
    tiles = "".join(
        f'<a class="os-tile" href="/browse?q={html.escape(quote(q, safe=''))}"><b>{n}</b><span>{e(t(label))}</span></a>'
        for label, n, q in s["counts"])
    est = t("About {time} left today ({secs} s per card).", time=minutes(s["secs"]), secs=f"{s['per']:.1f}")
    total = s["counts"][0][1]
    mature = s["counts"][4][1]
    share = t("{pct}% of cards are mature.", pct=round(100 * mature / total)) if total else ""
    return f"""<style>.os-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(110px,1fr));gap:8px}}
.os-tile{{display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:64px;padding:8px;
border:1px solid var(--line);border-radius:12px;color:var(--ink);text-decoration:none}}.os-tile:hover{{background:var(--bg)}}
.os-tile b{{font-size:22px;font-variant-numeric:tabular-nums}}.os-tile span{{font-size:14px;color:var(--muted)}}</style>
<div class="box os"><h3 style="margin-top:0">{e(t("More Overview Stats"))}</h3><div class="os-grid">{tiles}</div>
<p class="muted" style="margin-bottom:0">{e(est)} {e(share)}</p></div>"""

