"""Statistics: Anki's Statistics window as a page.

All numbers come from the engine's own `graphs` call (the one Anki's statistics screen
uses), with Anki's graph preferences for the defaults. Each graph is drawn here as plain
SVG on the server, so the page needs no chart library and works without JavaScript.
Switches inside a graph (Time, Backlog, Separate suspended/buried, interval ranges) are
CSS-only radio buttons/checkboxes. A small script shows the details of a tapped bar
(touch has no hover) with a "Browse" link where Anki's graphs link to the browser.
Labels and messages come from the engine's translations.
"""
import datetime, html, math, re, time

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse

from anki.collection import SearchNode

import app as core

router = APIRouter()

# Global time range, as in Anki's graph radio buttons: key -> (label, days, field in per-range data)
RANGES = {"1m": ("1 month", 31, "one_month"), "3m": ("3 months", 90, "three_months"),
          "1y": ("1 year", 365, "one_year"), "all": ("All", 0, "all_time")}
# Colours close to Anki's graphs.
C = {"new": "#6baed6", "learn": "#fd8d3c", "relearn": "#e6550d", "young": "#74c476", "mature": "#31a354",
     "suspended": "#e8c547", "buried": "#9e9e9e", "filtered": "#9e7ad6", "bar": "#5b8def", "line": "#8e8e93",
     "again": "#d64545", "hard": "#c98a16", "good": "#2e9b4f", "easy": "#2f6fde"}
W, H = 800, 230                 # SVG viewBox
ML, MR, MT, MB = 46, 46, 12, 30  # margins


def clean(s):
    """Drop the bidi isolation marks the engine's translations put around values."""
    return re.sub(r"[⁨⁩]", "", s)


def esc(s):
    return html.escape(str(s), quote=True)


def pct(a, b, digits=0):
    if not b:
        return "0"
    v = 100 * a / b
    return f"{v:.{digits}f}" if digits else str(round(v))


def nice_max(v):
    """A round number ≥ v for the y axis, and its tick step."""
    if v <= 0:
        return 1, 1
    raw = v / 4
    mag = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 2.5, 5, 10):
        step = m * mag
        if step >= raw:
            break
    if step < 1:
        step = 1
    top = math.ceil(v / step) * step
    return top, step


def fmt_num(v):
    if isinstance(v, float) and not v.is_integer():
        return f"{v:.1f}"
    v = int(v)
    if v >= 10000:
        return f"{v / 1000:.0f}k"
    return str(v)


def fmt_secs(secs):
    """Short duration for review time: 45s, 12m, 3.4h."""
    if secs < 60:
        return f"{round(secs)}s"
    if secs < 3600:
        return f"{secs / 60:.0f}m" if secs >= 600 else f"{secs / 60:.1f}m"
    return f"{secs / 3600:.1f}h"


def fmt_days(days):
    """Interval/stability in Anki's short units (d, mo, y)."""
    if days < 1:
        return f"{days * 24:.0f}h" if days > 0 else "0d"
    if days < 30:
        return f"{days:.0f}d" if days >= 10 else f"{days:.1f}".rstrip("0").rstrip(".") + "d"
    if days < 365:
        return f"{days / 30:.1f}".rstrip("0").rstrip(".") + "mo"
    return f"{days / 365:.1f}".rstrip("0").rstrip(".") + "y"


# ---------- SVG drawing ----------
def bar_chart(bins, colors, *, tips, xticks, line=None, line_max=None, line_fmt=None,
              queries=None, yfmt=fmt_num, no_data="", label=""):
    """Stacked bar chart. bins: per-bar lists of values (one per colour). xticks: (index, text).
    line: optional per-bar values drawn on a right-hand axis from 0 to line_max."""
    n = len(bins)
    totals = [sum(b) for b in bins]
    if not n or not any(totals):
        return f'<div class="nodata">{esc(no_data)}</div>'
    top, step = nice_max(max(totals))
    pw, ph = W - ML - MR, H - MT - MB
    bw = pw / n
    gap = 1 if bw > 4 else 0
    y = lambda v: MT + ph - ph * v / top
    out = [f'<svg viewBox="0 0 {W} {H}" class="chart" role="img" aria-label="{esc(label)}">']
    t = 0
    while t <= top + 1e-9:
        yy = y(t)
        out.append(f'<line class="grid" x1="{ML}" x2="{W - MR}" y1="{yy:.1f}" y2="{yy:.1f}"/>'
                   f'<text class="ax" x="{ML - 6}" y="{yy + 4:.1f}" text-anchor="end">{esc(yfmt(t))}</text>')
        t += step
    for i, vals in enumerate(bins):
        x = ML + i * bw
        base = 0
        for v, col in zip(vals, colors):
            if v <= 0:
                continue
            y1, y0 = y(base + v), y(base)
            out.append(f'<rect x="{x + gap / 2:.2f}" y="{y1:.2f}" width="{max(bw - gap, 0.6):.2f}" '
                       f'height="{max(y0 - y1, 0.5):.2f}" fill="{col}"/>')
            base += v
    if line and line_max:
        pts = " ".join(f"{ML + (i + 0.5) * bw:.1f},{MT + ph - ph * min(v, line_max) / line_max:.1f}"
                       for i, v in enumerate(line))
        out.append(f'<polyline class="rline" points="{pts}"/>')
        for frac in (0, 0.5, 1):
            yy = MT + ph - ph * frac
            out.append(f'<text class="ax" x="{W - MR + 6}" y="{yy + 4:.1f}">{esc(line_fmt(line_max * frac))}</text>')
    for i, text in xticks:
        x = ML + (i + 0.5) * bw
        out.append(f'<text class="ax" x="{x:.1f}" y="{H - MB + 18}" text-anchor="middle">{esc(text)}</text>')
    out.append(f'<line class="axis" x1="{ML}" x2="{W - MR}" y1="{MT + ph}" y2="{MT + ph}"/>')
    for i in range(n):
        q = f' data-q="{esc(queries[i])}"' if queries and queries[i] else ""
        out.append(f'<rect class="hit" x="{ML + i * bw:.2f}" y="{MT}" width="{bw:.2f}" height="{ph}" '
                   f'data-tip="{esc(tips[i])}"{q}><title>{esc(tips[i])}</title></rect>')
    out.append("</svg>")
    return "".join(out)


def tick_list(n, labels, want=8):
    """Pick about `want` evenly spaced tick positions from per-bar labels."""
    if n <= 0:
        return []
    every = max(1, math.ceil(n / want))
    return [(i, labels[i]) for i in range(0, n, every)]


def donut(parts, total, label=""):
    """Pie of (name, value, colour)."""
    if not total:
        return ""
    cx, cy, r, w = 110, 110, 100, 46
    out = [f'<svg viewBox="0 0 220 220" class="pie" role="img" aria-label="{esc(label)}">']
    a0 = -math.pi / 2
    for name, v, col in parts:
        if v <= 0:
            continue
        frac = v / total
        tip = f"{name}: {v} ({pct(v, total, 1)}%)"
        if frac >= 0.9999:
            out.append(f'<circle cx="{cx}" cy="{cy}" r="{r - w / 2}" fill="none" stroke="{col}" '
                       f'stroke-width="{w}" class="hit" data-tip="{esc(tip)}"><title>{esc(tip)}</title></circle>')
            break
        a1 = a0 + 2 * math.pi * frac
        big = 1 if frac > 0.5 else 0
        ro, ri = r, r - w
        p = (f"M{cx + ro * math.cos(a0):.2f},{cy + ro * math.sin(a0):.2f} "
             f"A{ro},{ro} 0 {big} 1 {cx + ro * math.cos(a1):.2f},{cy + ro * math.sin(a1):.2f} "
             f"L{cx + ri * math.cos(a1):.2f},{cy + ri * math.sin(a1):.2f} "
             f"A{ri},{ri} 0 {big} 0 {cx + ri * math.cos(a0):.2f},{cy + ri * math.sin(a0):.2f}Z")
        out.append(f'<path d="{p}" fill="{col}" class="hit" data-tip="{esc(tip)}"><title>{esc(tip)}</title></path>')
        a0 = a1
    out.append("</svg>")
    return "".join(out)


# ---------- page pieces ----------
_uid = [0]


def uid():
    _uid[0] += 1
    return f"g{_uid[0]}"


def graph(title, subtitle, body):
    return (f'<section class="box graph"><h3>{esc(title)}</h3>'
            f'<p class="muted sub">{esc(subtitle)}</p>{body}<div class="tip" aria-live="polite"></div></section>')


def tabs(views, checked=0):
    """CSS-only segmented control: views = [(label, html)]."""
    g = uid()
    inputs = "".join(f'<input type="radio" class="tg t{i + 1}" name="{g}" id="{g}-{i + 1}"'
                     f'{" checked" if i == checked else ""}>' for i in range(len(views)))
    labels = "".join(f'<label for="{g}-{i + 1}" class="l{i + 1}">{esc(lab)}</label>' for i, (lab, _) in enumerate(views))
    panes = "".join(f'<div class="v{i + 1}">{body}</div>' for i, (_, body) in enumerate(views))
    return f'{inputs}<div class="seg">{labels}</div><div class="views">{panes}</div>'


def toggle(label, off_html, on_html, checked):
    """CSS-only checkbox switching between two views."""
    g = uid()
    return (f'<input type="checkbox" class="tc" id="{g}"{" checked" if checked else ""}>'
            f'<label class="chk" for="{g}">{esc(label)}</label>'
            f'<div class="views"><div class="v0">{off_html}</div><div class="vc">{on_html}</div></div>')


def summary(rows):
    rows = [(k, v) for k, v in rows if v is not None]
    if not rows:
        return ""
    return '<table class="sum">' + "".join(f"<tr><th>{esc(k)}</th><td>{esc(v)}</td></tr>" for k, v in rows) + "</table>"


def and_q(scope_q, extra):
    return f"({scope_q}) {extra}" if scope_q else extra


def day_bins(span):
    """Days per bar for a span of days."""
    if span <= 92:
        return 1
    if span <= 366:
        return 7
    return max(7, math.ceil(span / 60 / 7) * 7)


# ---------- the graphs ----------
def today_html(tr, g, col):
    t = g.today
    if not t.answer_count:
        lines = [tr.statistics_today_no_cards()]
    else:
        lines = [clean(col._backend.studied_today_message(cards=t.answer_count, seconds=t.answer_millis / 1000))
                 .replace("\n", " ")]
        again = t.answer_count - t.correct_count
        lines.append(f"{tr.statistics_today_again_count()} {again} ({pct(again, t.answer_count, 2)}%)")
        lines.append(clean(tr.statistics_today_type_counts(
            learn_count=t.learn_count, review_count=t.review_count, relearn_count=t.relearn_count,
            filtered_count=t.early_review_count)))
        if t.mature_count:
            lines.append(clean(tr.statistics_today_correct_mature(
                correct=t.mature_correct, total=t.mature_count, percent=pct(t.mature_correct, t.mature_count, 2))))
        else:
            lines.append(tr.statistics_today_no_mature_cards())
    body = "".join(f"<p>{esc(l)}</p>" for l in lines)
    return f'<section class="box graph today"><h3>{esc(tr.statistics_today_title())}</h3>{body}</section>'


def future_due_html(tr, g, span, scope_q, show_backlog):
    fd = dict(g.future_due.future_due)
    keys = [k for k in fd if k >= 0]
    end = span if span else (max(keys) + 1 if keys else 1)
    end = max(end, 1)
    step = day_bins(end)
    nb = math.ceil(end / step)

    def chart(with_backlog):
        bins, tips, labels, qs = [], [], [], []
        if with_backlog:
            overdue = sum(v for k, v in fd.items() if k < 0)
            if overdue:
                bins.append([overdue]); labels.append("<0")
                tips.append(f"Overdue\n{clean(tr.statistics_cards_due(cards=overdue))}")
                qs.append(and_q(scope_q, "prop:due<0"))
        for b in range(nb):
            lo, hi = b * step, min(end, (b + 1) * step) - 1
            v = sum(fd.get(d, 0) for d in range(lo, hi + 1))
            bins.append([v]); labels.append(str(lo))
            when = clean(tr.statistics_in_days_single(days=lo) if lo == hi
                         else tr.statistics_in_days_range(days_start=lo, days_end=hi))
            tips.append(f"{when}\n{clean(tr.statistics_cards_due(cards=v))}")
            qs.append(and_q(scope_q, f"prop:due={lo}" if lo == hi else f"prop:due>={lo} prop:due<={hi}"))
        run, tot = [], 0
        for b in bins:
            tot += b[0]; run.append(tot)
        svg = bar_chart(bins, [C["bar"]], tips=[f"{tp}\n{tr.statistics_running_total()}: {r}" for tp, r in zip(tips, run)],
                        xticks=tick_list(len(bins), labels), line=run, line_max=max(tot, 1), line_fmt=fmt_num,
                        queries=qs, no_data=tr.statistics_no_data(), label=tr.statistics_future_due_title())
        days = end + (1 if with_backlog and any(k < 0 for k in fd) else 0)
        rows = [(tr.statistics_total(), clean(tr.statistics_reviews(reviews=tot))),
                (tr.statistics_average(), clean(tr.statistics_reviews_per_day(count=round(tot / max(days, 1))))),
                (tr.statistics_due_tomorrow(), clean(tr.statistics_reviews(reviews=fd.get(1, 0))))]
        if g.future_due.daily_load:
            rows.append((tr.statistics_daily_load(), clean(tr.statistics_reviews_per_day(count=g.future_due.daily_load))))
        return svg + summary(rows)

    body = chart(False)
    if g.future_due.have_backlog:
        body = toggle(tr.statistics_backlog_checkbox(), chart(False), chart(True), show_backlog)
    return graph(tr.statistics_future_due_title(), tr.statistics_future_due_subtitle(), body)


def calendar_html(tr, g, today, year, first_weekday, min_year, base_url):
    counts = {}
    for k, v in g.reviews.count.items():
        counts[k] = v.learn + v.relearn + v.young + v.mature + v.filtered
    start = datetime.date(year, 1, 1)
    end = datetime.date(year, 12, 31)
    # Anki's GraphPreferences weekday: 0 = Sunday. Python: Monday = 0.
    py_first = (first_weekday - 1) % 7
    grid_start = start - datetime.timedelta(days=(start.weekday() - py_first) % 7)
    vals = [counts.get((start + datetime.timedelta(days=i) - today).days, 0)
            for i in range((end - start).days + 1)]
    mx = max(vals) if vals else 0
    shades = ["#c6e48b", "#7bc96f", "#49af5d", "#2e8840", "#196127"]
    cell, pad, left, topm = 13.5, 2, 30, 18
    weeks = ((end - grid_start).days // 7) + 1
    width = left + weeks * cell + 4
    height = topm + 7 * cell + 4
    out = [f'<svg viewBox="0 0 {width:.0f} {height:.0f}" class="chart cal" role="img" '
           f'aria-label="{esc(tr.statistics_calendar_title())} {year}">']
    days_lbl = ["S", "M", "T", "W", "T", "F", "S"]
    for r in range(7):
        if r % 2 == 1:
            out.append(f'<text class="ax" x="{left - 6}" y="{topm + r * cell + cell - 3:.1f}" text-anchor="end">'
                       f'{days_lbl[(first_weekday + r) % 7]}</text>')
    seen_month = set()
    d = grid_start
    studied = 0
    while d <= end:
        i = (d - grid_start).days
        wk, row = i // 7, i % 7
        x, y = left + wk * cell, topm + row * cell
        if d >= start:
            if d.month not in seen_month and d.day <= 7:
                seen_month.add(d.month)
                out.append(f'<text class="ax" x="{x:.1f}" y="{topm - 5}">{d.strftime("%b")}</text>')
            off = (d - today).days
            v = counts.get(off, 0)
            if v:
                studied += 1
                level = min(4, int(4 * v / mx)) if mx else 0
                fill = shades[level]
                cls = ""
            else:
                fill = ""
                cls = ' class="empty"' if off <= 0 else ' class="future"'
            tip = f'{d.strftime("%a")} {d.day} {d.strftime("%b %Y")}\n{clean(tr.statistics_reviews(reviews=v))}'
            fill_attr = f' fill="{fill}"' if fill else ""
            out.append(f'<rect x="{x + 1:.1f}" y="{y + 1:.1f}" width="{cell - pad:.1f}" height="{cell - pad:.1f}" rx="2"'
                       f'{fill_attr}{cls}/>')
            out.append(f'<rect class="hit" x="{x:.1f}" y="{y:.1f}" width="{cell:.1f}" height="{cell:.1f}" '
                       f'data-tip="{esc(tip)}"><title>{esc(tip)}</title></rect>')
        d += datetime.timedelta(days=1)
    out.append("</svg>")
    nav = '<div class="row calnav">'
    if year > min_year:
        nav += f'<a class="btn" href="{esc(base_url(year=year - 1))}" aria-label="Previous year">◀</a>'
    nav += f'<b>{year}</b>'
    if year < today.year:
        nav += f'<a class="btn" href="{esc(base_url(year=year + 1))}" aria-label="Next year">▶</a>'
    nav += f'<span class="muted">{esc(tr.statistics_days_studied())}: {studied}</span></div>'
    return graph(tr.statistics_calendar_title(), "", nav + "".join(out))


REV_KINDS = [("learn", "statistics_counts_learning_cards"), ("relearn", "statistics_counts_relearning_cards"),
             ("young", "statistics_counts_young_cards"), ("mature", "statistics_counts_mature_cards"),
             ("filtered", "statistics_counts_filtered_cards")]


def reviews_html(tr, g, span, scope_q):
    counts, times = dict(g.reviews.count), dict(g.reviews.time)
    keys = [k for k in counts if k <= 0]
    first = -(span - 1) if span else (min(keys) if keys else 0)
    total_days = -first + 1
    step = day_bins(total_days)
    nb = math.ceil(total_days / step)

    def chart(data, is_time):
        bins, tips, labels, qs = [], [], [], []
        for b in range(nb):
            hi = -(nb - 1 - b) * step          # newest day of this bar (0 = today)
            lo = max(first, hi - step + 1)
            vals = [0] * 5
            for d in range(lo, hi + 1):
                e = data.get(d)
                if e:
                    for j, (k, _) in enumerate(REV_KINDS):
                        vals[j] += getattr(e, k)
            if is_time:
                vals = [v / 1000 / 60 for v in vals]
            bins.append(vals)
            labels.append(str(hi))
            when = clean(tr.statistics_days_ago_single(days=-hi) if lo == hi
                         else tr.statistics_days_ago_range(days_start=-hi, days_end=-lo))
            lines = [when]
            for (k, key), v in zip(REV_KINDS, vals):
                if v:
                    lines.append(f"{getattr(tr, key)()}: " + (fmt_secs(v * 60) if is_time else str(int(v))))
            tot = sum(vals)
            lines.append(f"{tr.statistics_counts_total_cards()}: " + (fmt_secs(tot * 60) if is_time else str(int(tot))))
            tips.append("\n".join(lines))
            qs.append(and_q(scope_q, f"rated:{-lo + 1}" if lo == hi == 0 else f"prop:rated>={lo} prop:rated<={hi}")
                      if not is_time else "")
        run, tot = [], 0
        for b in bins:
            tot += sum(b); run.append(tot)
        yfmt = (lambda v: f"{v:.0f}m" if v >= 1 or v == 0 else f"{v * 60:.0f}s") if is_time else fmt_num
        lfmt = (lambda v: fmt_secs(v * 60)) if is_time else fmt_num
        svg = bar_chart(bins, [C[k] for k, _ in REV_KINDS], tips=tips, xticks=tick_list(nb, labels),
                        line=run, line_max=max(tot, 1e-9), line_fmt=lfmt, queries=None if is_time else qs,
                        yfmt=yfmt, no_data=tr.statistics_no_data(), label=tr.statistics_reviews_title())
        days_studied = sum(1 for d in range(first, 1) if d in data and
                           sum(getattr(data[d], k) for k, _ in REV_KINDS))
        if is_time:
            mins = tot
            n_reviews = sum(sum(getattr(counts[d], k) for k, _ in REV_KINDS) for d in counts if d >= first)
            rows = [(tr.statistics_days_studied(), clean(tr.statistics_amount_of_total_with_percentage(
                        amount=days_studied, total=total_days, percent=pct(days_studied, total_days)))),
                    (tr.statistics_total(), fmt_secs(mins * 60)),
                    (tr.statistics_average_for_days_studied(),
                     clean(tr.statistics_minutes_per_day(count=round(mins / days_studied) if days_studied else 0))),
                    (tr.statistics_average_over_period(),
                     clean(tr.statistics_minutes_per_day(count=round(mins / total_days)))),
                    ]
            if n_reviews and mins:
                secs = mins * 60 / n_reviews
                rows.append((tr.statistics_average_answer_time_label(), clean(tr.statistics_average_answer_time(
                    average_seconds=round(secs, 1), cards_per_minute=round(60 / secs, 1) if secs else 0))))
        else:
            rows = [(tr.statistics_days_studied(), clean(tr.statistics_amount_of_total_with_percentage(
                        amount=days_studied, total=total_days, percent=pct(days_studied, total_days)))),
                    (tr.statistics_total(), clean(tr.statistics_reviews(reviews=int(tot)))),
                    (tr.statistics_average_for_days_studied(),
                     clean(tr.statistics_reviews_per_day(count=round(tot / days_studied) if days_studied else 0))),
                    (tr.statistics_average_over_period(),
                     clean(tr.statistics_reviews_per_day(count=round(tot / total_days))))]
        return svg + summary(rows)

    legend = "".join(f'<span><i style="background:{C[k]}"></i>{esc(getattr(tr, key)())}</span>' for k, key in REV_KINDS)
    body = toggle(tr.statistics_reviews_time_checkbox(), chart(counts, False), chart(times, True), False)
    return graph(tr.statistics_reviews_title(), tr.statistics_reviews_count_subtitle(),
                 f'<div class="legend">{legend}</div>{body}')


def card_counts_html(tr, g, separate):
    def part(c, with_inactive):
        parts = [(tr.statistics_counts_new_cards(), c.newCards, C["new"]),
                 (tr.statistics_counts_learning_cards(), c.learn, C["learn"]),
                 (tr.statistics_counts_relearning_cards(), c.relearn, C["relearn"]),
                 (tr.statistics_counts_young_cards(), c.young, C["young"]),
                 (tr.statistics_counts_mature_cards(), c.mature, C["mature"])]
        if with_inactive:
            parts += [(tr.statistics_counts_suspended_cards(), c.suspended, C["suspended"]),
                      (tr.statistics_counts_buried_cards(), c.buried, C["buried"])]
        total = sum(v for _, v, _ in parts)
        rows = "".join(f'<tr><th><i style="background:{col}"></i>{esc(n)}</th><td>{v}</td>'
                       f'<td class="muted">{pct(v, total, 2)}%</td></tr>' for n, v, col in parts)
        rows += f'<tr class="tot"><th>Total cards</th><td>{total}</td><td></td></tr>'
        if not total:
            return f'<div class="nodata">{esc(tr.statistics_no_data())}</div>'
        return (f'<div class="row pierow">{donut(parts, total, tr.statistics_counts_title())}'
                f'<table class="counts">{rows}</table></div>')
    body = toggle(tr.statistics_counts_separate_suspended_buried_cards(),
                  part(g.card_counts.including_inactive, False), part(g.card_counts.excluding_inactive, True), separate)
    return graph(tr.statistics_counts_title(), "", body)


def percentile(hist, p):
    """Smallest key at which the cumulative share reaches p (0..1)."""
    total = sum(hist.values())
    run = 0
    for k in sorted(hist):
        run += hist[k]
        if run >= total * p:
            return k
    return 0


def days_hist_html(tr, hist, *, title, subtitle, tip_single, tip_range, avg_label, median_label,
                   scope_q, prop):
    """Intervals and stability: days histogram with Anki's 1 month / 50% / 95% / all choices."""
    hist = {k: v for k, v in hist.items() if v}
    if not hist:
        return graph(title, subtitle, f'<div class="nodata">{esc(tr.statistics_no_data())}</div>')
    total = sum(hist.values())
    avg = sum(k * v for k, v in hist.items()) / total
    med = percentile(hist, 0.5)
    mx = max(hist)

    def chart(cap):
        cap = max(1, min(cap, mx))
        want = 70
        step = 1 if cap <= want else math.ceil(cap / want)
        nb = cap // step + 1
        bins, tips, labels, qs = [], [], [], []
        run, acc = [], 0
        for b in range(nb):
            lo, hi = b * step, b * step + step - 1
            v = sum(hist.get(d, 0) for d in range(lo, hi + 1))
            acc += v
            bins.append([v]); labels.append(fmt_days(lo) if lo else "0")
            run.append(100 * acc / total)
            t = clean(tip_single(cards=v, day=lo) if lo == hi else tip_range(cards=v, days_start=lo, days_end=hi))
            tips.append(f"{t}\n{tr.statistics_running_total()}: {run[-1]:.1f}%")
            qs.append(and_q(scope_q, f"prop:{prop}={lo}" if lo == hi else f"prop:{prop}>={lo} prop:{prop}<={hi}"))
        return bar_chart(bins, [C["bar"]], tips=tips, xticks=tick_list(nb, labels), line=run, line_max=100,
                         line_fmt=lambda v: f"{v:.0f}%", queries=qs if prop else None,
                         no_data=tr.statistics_no_data(), label=title)

    views = [("1 month", chart(31)), ("50%", chart(percentile(hist, 0.5))),
             ("95%", chart(percentile(hist, 0.95))), (tr.statistics_range_all_time(), chart(mx))]
    rows = [(avg_label, fmt_days(avg)), (median_label, fmt_days(med))]
    return graph(title, subtitle, tabs(views, 2) + summary(rows))


def pct_hist_html(tr, hist, *, title, subtitle, tip, avg_label, average, extra_rows=(), lo=0, hi=100, step=5,
                  scope_q="", prop="", scale=100, median_label=None):
    """Ease, difficulty and retrievability: percentage histogram."""
    hist = {k: v for k, v in hist.items() if v}
    if not hist:
        return graph(title, subtitle, f'<div class="nodata">{esc(tr.statistics_no_data())}</div>')
    lo = min(lo, min(hist) // step * step)
    hi = max(hi, max(hist))
    nb = max(1, math.ceil((hi - lo) / step))
    bins, tips, labels, qs = [], [], [], []
    for b in range(nb):
        a = lo + b * step
        last = b == nb - 1
        v = sum(n for k, n in hist.items() if a <= k and (last or k < a + step))
        bins.append([v]); labels.append(f"{a}%")
        tips.append(clean(tip(cards=v, percent=f"{a}%-{a + step}%")))
        if prop:
            # The last bucket also holds the very top value (e.g. retrievability 100%).
            cond = f"prop:{prop}>={a / scale:g}"
            if not last:
                cond += f" prop:{prop}<{(a + step) / scale:g}"
            qs.append(and_q(scope_q, cond))
    svg = bar_chart(bins, [C["bar"]], tips=tips, xticks=tick_list(nb, labels, 10), queries=qs or None,
                    no_data=tr.statistics_no_data(), label=title)
    rows = [(avg_label, f"{average:.0f}%")]
    if median_label:
        rows.append((median_label, f"{percentile(hist, 0.5)}%"))
    rows += list(extra_rows)
    return graph(title, subtitle, svg + summary(rows))


def hours_html(tr, g, field):
    data = list(getattr(g.hours, field))
    if len(data) != 24 or not any(h.total for h in data):
        return graph(tr.statistics_hours_title(), tr.statistics_hours_subtitle(),
                     f'<div class="nodata">{esc(tr.statistics_no_data())}</div>')
    bins = [[h.total] for h in data]
    rate = [100 * h.correct / h.total if h.total else 0 for h in data]
    tips = [f"{clean(tr.statistics_hours_range(hour_start=i, hour_end=i + 1))}\n"
            f"{clean(tr.statistics_hours_reviews(reviews=h.total))}\n"
            f"{clean(tr.statistics_hours_correct_reviews(percent=pct(h.correct, h.total), reviews=h.correct))}"
            for i, h in enumerate(data)]
    svg = bar_chart(bins, [C["bar"]], tips=tips, xticks=[(i, str(i)) for i in range(0, 24, 3)],
                    line=rate, line_max=100, line_fmt=lambda v: f"{v:.0f}%", label=tr.statistics_hours_title())
    return graph(tr.statistics_hours_title(), tr.statistics_hours_subtitle(),
                 svg + f'<p class="muted small">Bars: reviews · Line: % correct {esc(tr.statistics_hours_correct_info())}</p>')


def buttons_html(tr, g, field):
    b = getattr(g.buttons, field)
    groups = [(tr.statistics_counts_learning_cards(), list(b.learning)),
              (tr.statistics_counts_young_cards(), list(b.young)),
              (tr.statistics_counts_mature_cards(), list(b.mature))]
    if not any(sum(v) for _, v in groups):
        return graph(tr.statistics_answer_buttons_title(), tr.statistics_answer_buttons_subtitle(),
                     f'<div class="nodata">{esc(tr.statistics_no_data())}</div>')
    names = ["Again", "Hard", "Good", "Easy"]
    cols = [C["again"], C["hard"], C["good"], C["easy"]]
    bins, tips, xt, colors = [], [], [], []
    # One bar per button per group, with an empty slot between groups.
    for gi, (gname, vals) in enumerate(groups):
        vals = (vals + [0, 0, 0, 0])[:4]
        tot = sum(vals)
        for i in range(4):
            bin_ = [0, 0, 0, 0]
            bin_[i] = vals[i]
            bins.append(bin_)
            tips.append(f"{gname} · {names[i]}\n{tr.statistics_answer_buttons_button_pressed()}: {vals[i]}"
                        f" ({pct(vals[i], tot)}%)")
        xt.append((len(bins) - 3, gname))
        if gi < 2:
            bins.append([0, 0, 0, 0]); tips.append("")
    svg = bar_chart(bins, cols, tips=tips, xticks=[(i - 0.5, t) for i, t in xt], label=tr.statistics_answer_buttons_title())
    rows = []
    for gname, vals in groups:
        vals = (vals + [0, 0, 0, 0])[:4]
        tot = sum(vals)
        if tot:
            rows.append((gname, f"Correct: {tot - vals[0]}/{tot} ({pct(tot - vals[0], tot, 2)}%)"))
    legend = "".join(f'<span><i style="background:{c}"></i>{n}</span>' for n, c in zip(names, cols))
    return graph(tr.statistics_answer_buttons_title(), tr.statistics_answer_buttons_subtitle(),
                 f'<div class="legend">{legend}</div>{svg}{summary(rows)}')


def added_html(tr, g, span, scope_q):
    added = {k: v for k, v in g.added.added.items() if k <= 0}
    first = -(span - 1) if span else (min(added) if added else 0)
    total_days = -first + 1
    step = day_bins(total_days)
    nb = math.ceil(total_days / step)
    bins, tips, labels, qs, run = [], [], [], [], []
    acc = 0
    for b in range(nb):
        hi = -(nb - 1 - b) * step
        lo = max(first, hi - step + 1)
        v = sum(added.get(d, 0) for d in range(lo, hi + 1))
        acc += v
        bins.append([v]); labels.append(str(hi)); run.append(acc)
        when = clean(tr.statistics_days_ago_single(days=-hi) if lo == hi
                     else tr.statistics_days_ago_range(days_start=-hi, days_end=-lo))
        tips.append(f"{when}\n{clean(tr.statistics_cards(cards=v))}\n{tr.statistics_running_total()}: {acc}")
        # added:N covers the last N days, so a bar ending today can link exactly.
        qs.append(and_q(scope_q, f"added:{-lo + 1}") if hi == 0 else "")
    svg = bar_chart(bins, [C["new"]], tips=tips, xticks=tick_list(nb, labels), line=run, line_max=max(acc, 1),
                    line_fmt=fmt_num, queries=qs, no_data=tr.statistics_no_data(), label=tr.statistics_added_title())
    rows = [(tr.statistics_total(), clean(tr.statistics_cards(cards=acc))),
            (tr.statistics_average(), clean(tr.statistics_cards_per_day(count=round(acc / total_days))))] if acc else []
    return graph(tr.statistics_added_title(), tr.statistics_added_subtitle(), svg + summary(rows))


def true_retention_html(tr, g):
    tr_ = g.true_retention
    periods = [("today", tr.statistics_true_retention_today()), ("yesterday", tr.statistics_true_retention_yesterday()),
               ("week", tr.statistics_true_retention_week()), ("month", tr.statistics_true_retention_month()),
               ("year", tr.statistics_true_retention_year()), ("all_time", tr.statistics_true_retention_all_time())]
    na = tr.statistics_true_retention_not_applicable()

    def cell(p, f):
        n = p + f
        if not n:
            return f'<td class="muted">{esc(na)}</td>'
        return f'<td><b>{pct(p, n, 1)}%</b><br><small class="muted">{p}/{n}</small></td>'

    rows = []
    for key, label in periods:
        r = getattr(tr_, key)
        rows.append(f"<tr><th>{esc(label)}</th>{cell(r.young_passed, r.young_failed)}"
                    f"{cell(r.mature_passed, r.mature_failed)}"
                    f"{cell(r.young_passed + r.mature_passed, r.young_failed + r.mature_failed)}</tr>")
    head = (f"<tr><th>{esc(tr.statistics_true_retention_range())}</th>"
            f"<th>{esc(tr.statistics_true_retention_young())}</th><th>{esc(tr.statistics_true_retention_mature())}</th>"
            f"<th>{esc(tr.statistics_true_retention_total())}</th></tr>")
    table = f'<div class="scrollx"><table class="ret">{head}{"".join(rows)}</table></div>'
    note = f'<p class="muted small">{esc(tr.statistics_true_retention_tooltip())}</p>'
    return graph(tr.statistics_true_retention_title(), tr.statistics_true_retention_subtitle(), table + note)


# ---------- page ----------
STYLE = """<style>
.graph h3{margin:0 0 2px}.graph .sub{margin:0 0 10px;font-size:14px}.graph p{margin:6px 0}
.chart{width:100%;height:auto;display:block;touch-action:manipulation}
.chart .grid{stroke:var(--line);stroke-width:1}.chart .axis{stroke:var(--muted);stroke-width:1}
.chart .ax{fill:var(--muted);font-size:12px;font-family:inherit}
.chart .rline{fill:none;stroke:var(--muted);stroke-width:1.6;stroke-dasharray:4 3}
.hit{fill:transparent;cursor:pointer}.chart rect.hit:hover,.chart rect.hit.on{fill:rgba(127,127,127,.18)}
.pie .hit{cursor:pointer}.pie .hit:hover{opacity:.8}
.cal .empty{fill:var(--line)}.cal .future{fill:none;stroke:var(--line)}
.cal .ax{font-size:10px}.calnav{gap:12px;margin-bottom:6px}.calnav .btn{padding:8px 14px}
.nodata{padding:40px 0;text-align:center;color:var(--muted);letter-spacing:.05em}
.tip{white-space:pre-line;min-height:1.4em;font-size:15px;margin-top:6px;color:var(--ink)}
.tip a{margin-left:8px}
.sum{width:auto;margin-top:8px}.sum th,.sum td{padding:6px 12px 6px 0;border:0;font-size:15px}
.sum th{color:var(--muted);font-weight:400}
.legend{display:flex;gap:14px;flex-wrap:wrap;font-size:14px;color:var(--muted);margin-bottom:6px}
.legend i,.counts i{display:inline-block;width:12px;height:12px;border-radius:3px;margin-right:6px;vertical-align:-1px}
.pierow{gap:24px;align-items:center;flex-wrap:wrap}.pie{width:200px;height:200px;flex:none}
.counts{width:auto;flex:1;min-width:240px}.counts th{color:var(--ink);font-weight:400;font-size:16px}
.counts td,.counts th{padding:8px}.counts td{text-align:right;font-variant-numeric:tabular-nums}
.counts tr.tot th,.counts tr.tot td{font-weight:600}
.ret td,.ret th{text-align:center;padding:8px 6px}.ret th:first-child{text-align:left}
.scrollx{overflow-x:auto}.small{font-size:13px}
.graph input.tg,.graph input.tc{position:absolute;opacity:0;width:1px;height:1px;pointer-events:none}
.seg{display:inline-flex;border:1px solid var(--line);border-radius:10px;overflow:hidden;margin:2px 0 8px;flex-wrap:wrap}
.seg label{margin:0;padding:10px 14px;min-height:44px;color:var(--ink);font-size:15px;cursor:pointer;
border-right:1px solid var(--line);display:flex;align-items:center}.seg label:last-child{border-right:0}
.t1:checked~.seg .l1,.t2:checked~.seg .l2,.t3:checked~.seg .l3,.t4:checked~.seg .l4{background:var(--accent);color:#fff}
.views>div{display:none}
.t1:checked~.views>.v1,.t2:checked~.views>.v2,.t3:checked~.views>.v3,.t4:checked~.views>.v4{display:block}
.tc:not(:checked)~.views>.v0,.tc:checked~.views>.vc{display:block}
label.chk{display:inline-flex;align-items:center;gap:8px;margin:0 0 8px;min-height:44px;color:var(--ink);
font-size:15px;cursor:pointer}
label.chk:before{content:"";width:20px;height:20px;border:1.5px solid var(--muted);border-radius:5px;
box-sizing:border-box}
.tc:checked+label.chk:before{background:var(--accent);border-color:var(--accent);
box-shadow:inset 0 0 0 3px var(--card)}
.tg:focus-visible~.seg,.tc:focus-visible+label.chk{outline:2px solid var(--accent);outline-offset:2px}
.scope{gap:10px}.scope select{width:auto;flex:1;min-width:180px}.scope input{flex:2;min-width:180px}
.ranges{gap:6px;margin-top:10px}.ranges a.on{background:var(--accent);border-color:var(--accent);color:#fff}
.today p{font-size:17px}
@media print{header,.scope,.ranges,.seg,label.chk,.calnav .btn,.tip{display:none!important}.box{break-inside:avoid}}
</style>"""

SCRIPT = """<script>
(function(){
  function show(el){
    var g=el.closest('.graph'); if(!g) return;
    var tip=g.querySelector('.tip'); if(!tip) return;
    g.querySelectorAll('.hit.on').forEach(function(e){e.classList.remove('on')});
    el.classList.add('on');
    tip.textContent=el.getAttribute('data-tip')||'';
    var q=el.getAttribute('data-q');
    if(q){var a=document.createElement('a');a.href='/browse?q='+encodeURIComponent(q);a.textContent='Browse';tip.appendChild(a);}
  }
  document.addEventListener('click',function(e){var h=e.target.closest&&e.target.closest('.hit');if(h)show(h);});
  document.addEventListener('mouseover',function(e){
    var h=e.target.closest&&e.target.closest('.hit');if(h&&matchMedia('(hover:hover)').matches)show(h);});
})();
</script>"""


def scope_search(col, did, q):
    """The search string for the chosen scope: a typed search, one deck (with subdecks), or everything."""
    if q.strip():
        return q.strip()
    if did:
        deck = col.decks.get(did, default=False)
        if deck:
            return col.build_search_string(SearchNode(deck=deck["name"]))
    return ""


@router.get("/stats", response_class=HTMLResponse)
def stats(request: Request, did: str = "", q: str = "", span: str = Query("1m", alias="range"), year: int = 0):
    user = core.current_user(request)
    if not user:
        return core.need_login(request)
    span = span if span in RANGES else "1m"
    span_label, span_days, field = RANGES[span]
    flash = core.take_flash(request)
    with core.collection(user) as col:
        tr = col.tr
        if did == "":
            deck_id = col.decks.get_current_id()     # Anki opens on the current deck
        else:
            try:
                deck_id = int(did)
            except ValueError:
                deck_id = 0
        if deck_id and not col.decks.get(deck_id, default=False):
            deck_id = 0
        search = scope_search(col, deck_id, q)
        prefs = col._backend.get_graph_preferences()
        cutoff = col.sched.day_cutoff
        today = datetime.date.fromtimestamp(cutoff - 86400)
        first_rev = col.db.scalar("select min(id) from revlog") or 0
        min_year = datetime.date.fromtimestamp(first_rev / 1000).year if first_rev else today.year
        cal_year = year if min_year <= year <= today.year else today.year
        cal_days = (today - datetime.date(cal_year, 1, 1)).days + 1
        fetch_days = 0 if span_days == 0 else max(span_days, cal_days)
        try:
            g = col._backend.graphs(search=search, days=fetch_days)
        except Exception as e:
            flash = "!" + (str(e) or "That search did not work.")
            q, search = "", ""
            g = col._backend.graphs(search="", days=fetch_days)
        decks = sorted(col.decks.all_names_and_ids(), key=lambda d: d.name.lower())

    def url(**kw):
        params = {"did": str(deck_id), "range": span}
        if q.strip():
            params["q"] = q.strip()
        if cal_year != today.year:
            params["year"] = str(cal_year)
        params.update({k: str(v) for k, v in kw.items()})
        if params.get("year") == str(today.year):
            params.pop("year")
        return "/stats?" + "&".join(f"{k}={core.quote(v)}" for k, v in params.items())

    opts = [f'<option value="0"{" selected" if not deck_id and not q.strip() else ""}>'
            f'Whole {esc(tr.statistics_range_collection())}</option>']
    opts += [f'<option value="{d.id}"{" selected" if d.id == deck_id and not q.strip() else ""}>{esc(d.name)}</option>'
             for d in decks]
    ranges = "".join(f'<a class="btn{" on" if k == span else ""}" href="{esc(url(range=k))}">{esc(lab)}</a>'
                     for k, (lab, _, _) in RANGES.items())
    controls = f"""<div class="box"><h2 style="margin:0 0 10px">{esc(tr.statistics_title())}</h2>
<form method="get" action="/stats" class="row scope"><input type="hidden" name="range" value="{esc(span)}">
<select name="did" aria-label="Deck" onchange="this.form.q.value='';this.form.submit()">{''.join(opts)}</select>
<input name="q" value="{esc(q.strip())}" placeholder="{esc(tr.statistics_range_search())}: e.g. tag:verbs" aria-label="Search">
<button>Show</button></form>
<div class="row ranges">{ranges}<button type="button" onclick="window.print()" style="margin-left:auto">Print / PDF</button></div>
</div>"""

    scope_q = search
    parts = [today_html(tr, g, col),
             future_due_html(tr, g, span_days, scope_q, prefs.future_due_show_backlog),
             calendar_html(tr, g, today, cal_year, prefs.calendar_first_day_of_week, min_year, url),
             reviews_html(tr, g, span_days, scope_q),
             card_counts_html(tr, g, prefs.card_counts_separate_inactive),
             days_hist_html(tr, dict(g.intervals.intervals), title=tr.statistics_intervals_title(),
                            subtitle=tr.statistics_intervals_subtitle(), tip_single=tr.statistics_intervals_day_single,
                            tip_range=tr.statistics_intervals_day_range, avg_label=tr.statistics_average_interval(),
                            median_label=tr.statistics_median_interval(), scope_q=scope_q, prop="ivl")]
    if g.fsrs:
        parts.append(pct_hist_html(
            tr, dict(g.difficulty.eases), title=tr.statistics_card_difficulty_title(),
            subtitle=tr.statistics_card_difficulty_subtitle2(), tip=tr.statistics_card_difficulty_tooltip,
            avg_label=tr.statistics_average_difficulty(), average=g.difficulty.average,
            median_label=tr.statistics_median_difficulty(), scope_q=scope_q, prop="d"))
        parts.append(days_hist_html(
            tr, dict(g.stability.intervals), title=tr.statistics_card_stability_title(),
            subtitle=tr.statistics_card_stability_subtitle(), tip_single=tr.statistics_stability_day_single,
            tip_range=tr.statistics_stability_day_range, avg_label=tr.statistics_average_stability(),
            median_label=tr.statistics_median_stability(), scope_q=scope_q, prop="s"))
        r = g.retrievability
        parts.append(pct_hist_html(
            tr, dict(r.retrievability), title=tr.statistics_card_retrievability_title(),
            subtitle=tr.statistics_retrievability_subtitle(), tip=tr.statistics_retrievability_tooltip,
            avg_label=tr.statistics_average_retrievability(), average=r.average,
            extra_rows=[(tr.statistics_estimated_total_knowledge(),
                         f"{clean(tr.statistics_cards(cards=round(r.sum_by_card)))} · "
                         f"{clean(tr.statistics_notes(notes=round(r.sum_by_note)))}")],
            scope_q=scope_q, prop="r"))
    else:
        parts.append(pct_hist_html(
            tr, dict(g.eases.eases), title=tr.statistics_card_ease_title(), subtitle=tr.statistics_card_ease_subtitle(),
            tip=tr.statistics_card_ease_tooltip, avg_label=tr.statistics_average_ease(), average=g.eases.average,
            median_label=tr.statistics_median_ease(), lo=130, hi=300, step=10, scope_q=scope_q, prop="ease"))
    parts += [hours_html(tr, g, field), buttons_html(tr, g, field), added_html(tr, g, span_days, scope_q),
              true_retention_html(tr, g)]
    scope_text = (f"Search: {q.strip()}" if q.strip() else
                  next((f"Deck: {d.name}" for d in decks if d.id == deck_id), "Whole collection"))
    body = (STYLE + controls + f'<p class="muted" style="margin:-6px 4px 12px">{esc(scope_text)} · {esc(span_label)}</p>'
            + "".join(parts) + SCRIPT)
    return core.page(tr.statistics_title(), body, user, "stats", flash)
