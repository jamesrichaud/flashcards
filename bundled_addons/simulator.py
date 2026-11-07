"""See how many reviews a deck will give you each day for the coming weeks or months.

Like the desktop add-on "Anki Simulator". Uses the engine's own FSRS simulator (the one behind
the Simulate button in Anki's deck options) with the deck's preset: new cards/day, maximum
reviews/day, desired retention, steps and FSRS parameters. If the engine can't simulate, a
simple projection is used instead and the page says so. Nothing in the collection is changed.
"""
import html
from urllib.parse import urlencode

from fastapi import APIRouter, Request

from anki import scheduler_pb2
from anki.collection import SearchNode
from anki.utils import ids2str

from flashcards import addons, stats
from flashcards.i18n import t, N_

NAME = "Anki Simulator"   # plain literal: flashcards/addons.py reads it without importing
N_("Anki Simulator")      # ...so mark it for translation here
N_('See how many reviews a deck will give you each day for the coming weeks or months.')
MENU = [(N_("Simulate a deck"), "/")]
DAY_CHOICES = (30, 90, 180, 365)
REVIEW_SECS, NEW_SECS = 8.0, 20.0       # per card, for the simple projection
STEPS = (1, 3, 8, 20, 50, 120, 300, 700)   # days between reviews of a new card, simple projection


def preset(col, did):
    conf = col.decks.config_dict_for_deck_id(did)
    params = list(conf.get("fsrsParams6") or conf.get("fsrsParams5") or conf.get("fsrsWeights") or [])
    return {"new": int(conf["new"]["perDay"]), "rev": int(conf["rev"]["perDay"]),
            "retention": float(conf.get("desiredRetention", 0.9)), "max_ivl": int(conf["rev"].get("maxIvl", 36500)),
            "params": params, "lsteps": len(conf["new"].get("delays", [])), "rsteps": len(conf["lapse"].get("delays", [])),
            "historical": float(conf.get("historicalRetention", 0.9)),
            "easy_days": list(conf.get("easyDaysPercentages") or [1.0] * 7),
            "leech": int(conf["lapse"].get("leechFails", 0)) if conf["lapse"].get("leechAction") == 0 else 0}


def engine(col, scope, extra, days, new, rev, retention, p):
    req = scheduler_pb2.SimulateFsrsReviewRequest(
        params=p["params"], desired_retention=retention, deck_size=extra,   # extra new cards on top of the deck's own
        days_to_simulate=days,
        new_limit=new, review_limit=rev, max_interval=p["max_ivl"], search=scope,
        new_cards_ignore_review_limit=bool(col.get_config("newCardsIgnoreReviewLimit", False)),
        easy_days_percentages=p["easy_days"], historical_retention=p["historical"],
        learning_step_count=p["lsteps"], relearning_step_count=p["rsteps"])
    if p["leech"]:
        req.suspend_after_lapse_count = p["leech"]
    r = col._backend.simulate_fsrs_review(req)
    return [(int(r.daily_review_count[i]), int(r.daily_new_count[i]), float(r.daily_time_cost[i]))
            for i in range(min(days, len(r.daily_review_count)))]


def projection(col, scope, days, new, rev, extra=0):
    """Rough estimate without FSRS: due cards as scheduled, each review pushes the card 2.5x further,
    new cards come back after 1, 3, 8, 20... days; reviews over the daily limit wait a day."""
    today = col.sched.today
    want = [0] * (days + 1)
    cids = col.find_cards(f"{scope} is:review -is:suspended -is:buried")
    for due, ivl in col.db.all(f"select due, ivl from cards where id in {ids2str(cids)}"):
        d, step = max(0, due - today), max(1, ivl)
        while d < days:
            want[d] += 1
            step = max(1, round(step * 2.5))
            d += step
    left = len(col.find_cards(scope + " is:new -is:suspended -is:buried")) + extra
    out, carry = [], 0
    for day in range(days):
        n = min(new, left)
        left -= n
        for s in STEPS:
            if day + s < days:
                want[day + s] += n
        todo = want[day] + carry
        done = min(todo, rev) if rev else 0
        carry = todo - done
        out.append((done, n, done * REVIEW_SECS + n * NEW_SECS))
    return out


def simulate(col, did, days, new, rev, retention, extra=0):
    deck = col.decks.get(did, default=False)
    scope = col.build_search_string(SearchNode(deck=deck["name"]))
    p = preset(col, did)
    try:
        return engine(col, scope, extra, days, new, rev, retention, p), True
    except Exception:
        return projection(col, scope, days, new, rev, extra), False


def fmt_min(secs):
    return t("{m} min", m=round(secs / 60))


def result_html(rows, used_engine):
    if not rows:
        return ""
    labels = [str(i + 1) for i in range(len(rows))]
    tips = [t("Day {day}: {rev} reviews, {new} new, about {time}", day=i + 1, rev=r, new=n, time=fmt_min(s))
            for i, (r, n, s) in enumerate(rows)]
    chart = stats.bar_chart([[r, n] for r, n, _ in rows], [stats.C["bar"], stats.C["new"]], tips=tips,
                            xticks=stats.tick_list(len(rows), labels), no_data=t("No cards to review in this time."),
                            label=t("Reviews per day"))
    total_rev = sum(r for r, _, _ in rows)
    total_secs = sum(s for _, _, s in rows)
    busiest = max(range(len(rows)), key=lambda i: rows[i][0])
    summary = stats.summary([(t("Average reviews per day"), f"{total_rev / len(rows):.0f}"),
                             (t("Busiest day"), t("Day {day}: {rev} reviews", day=busiest + 1, rev=rows[busiest][0])),
                             (t("New cards introduced"), str(sum(n for _, n, _ in rows))),
                             (t("Average time per day"), fmt_min(total_secs / len(rows))),
                             (t("Total time"), fmt_min(total_secs))])
    how = (t("Simulated with the engine's FSRS simulator and this deck's preset.") if used_engine else
           t("The engine's simulator couldn't run, so this is a simple projection: treat it as a rough guide."))
    e = lambda x: html.escape(x, quote=False)
    legend = (f'<div class="legend"><span><i style="background:{stats.C["bar"]}"></i>{e(t("Reviews"))}</span>'
              f'<span><i style="background:{stats.C["new"]}"></i>{e(t("New"))}</span></div>')
    return stats.graph(t("Reviews per day"), how, legend + chart + summary)


router = APIRouter()


@router.get("/")
def page(request: Request, did: int = 0, days: int = 90, new: int = -1, rev: int = -1, retention: float = 0,
         extra: int = 0, run: int = 0):
    with addons.collection(request) as col:
        decks = [d for d in sorted(col.decks.all_names_and_ids(include_filtered=False), key=lambda d: d.name)]
        if not did or not any(d.id == did for d in decks):
            did = col.decks.get_current_id() if any(d.id == col.decks.get_current_id() for d in decks) else decks[0].id
        p = preset(col, did)
        days = days if days in DAY_CHOICES else 90
        new = p["new"] if new < 0 else min(new, 9999)
        rev = p["rev"] if rev < 0 else min(rev, 99999)
        extra = max(0, min(extra, 100000))
        retention = p["retention"] if not 0.7 <= retention <= 0.99 else retention
        result = ""
        if run:
            rows, used_engine = simulate(col, did, days, new, rev, retention, extra)
            result = result_html(rows, used_engine)
    e = lambda x: html.escape(x, quote=False)
    deck_opts = "".join(f'<option value="{d.id}"{" selected" if d.id == did else ""}>{html.escape(d.name)}</option>'
                        for d in decks)
    day_opts = "".join(f'<option value="{d}"{" selected" if d == days else ""}>{e(t("{n} days", n=d))}</option>'
                       for d in DAY_CHOICES)
    reset = "/addon/simulator/?" + urlencode({"did": did, "days": days, "run": 1})
    body = f"""{stats.STYLE}<div class="box"><h2 style="margin-top:0">{e(t("Anki Simulator"))}</h2>
<p class="muted">{e(t("Starts from the deck's preset. Change the numbers to see what would happen; nothing is saved."))}
{e(t("Extra new cards: cards you plan to add, on top of the deck's own new cards."))}</p>
<form method="get" action="/addon/simulator/"><input type="hidden" name="run" value="1">
<div class="row"><div style="flex:2;min-width:200px"><label for="sim-did">{e(t("Deck"))}</label>
<select id="sim-did" name="did" onchange="location='/addon/simulator/?did='+this.value">{deck_opts}</select></div>
<div style="flex:1;min-width:140px"><label for="sim-days">{e(t("Days to simulate"))}</label><select id="sim-days" name="days">{day_opts}</select></div></div>
<div class="row"><div style="flex:1;min-width:140px"><label for="sim-new">{e(t("New cards/day"))}</label>
<input id="sim-new" name="new" type="number" min="0" max="9999" value="{new}"></div>
<div style="flex:1;min-width:140px"><label for="sim-rev">{e(t("Maximum reviews/day"))}</label>
<input id="sim-rev" name="rev" type="number" min="0" max="99999" value="{rev}"></div>
<div style="flex:1;min-width:140px"><label for="sim-ret">{e(t("Desired retention"))}</label>
<input id="sim-ret" name="retention" type="number" min="0.7" max="0.99" step="0.01" value="{retention:.2f}"></div>
<div style="flex:1;min-width:140px"><label for="sim-extra">{e(t("Extra new cards"))}</label>
<input id="sim-extra" name="extra" type="number" min="0" max="100000" value="{extra}"></div></div>
<p class="row"><button class="primary">{e(t("Simulate"))}</button><a class="btn" href="{html.escape(reset)}">{e(t("Use the preset"))}</a>
<a class="btn" href="/addons">{e(t("Back to add-ons"))}</a></p></form></div>{result}
{stats.SCRIPT.replace("__BROWSE__", '""')}"""
    return addons.page(request, t("Anki Simulator"), body)
