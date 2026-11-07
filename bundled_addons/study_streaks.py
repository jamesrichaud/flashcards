"""Daily streaks, levels and achievements, worked out from your real review history.

A light gamification add-on (in the spirit of desktop add-ons like Pokemanki and Review Heatmap's
streaks). Shows your streak under each answer and a progress page with level and badges.
"""
import html
import time

from fastapi import APIRouter, Request

from flashcards import addons
from flashcards.i18n import t, tn, N_

NAME = "Study Streaks & Achievements"   # plain literal: flashcards/addons.py reads it without importing
N_("Study Streaks & Achievements")      # ...so mark it for translation here
N_('Daily streaks, levels and achievements, worked out from your real review history.')  # the first docstring line, shown on the Add-ons page
MENU = [(N_("My progress"), "/")]
XP = {1: 1, 2: 2, 3: 3, 4: 3}     # points per review by answer button


def stats(col):
    cutoff = col.sched.day_cutoff
    rows = col.db.all("select id, ease from revlog where ease between 1 and 4")
    days = set()
    xp = 0
    for rid, ease in rows:
        days.add(int((cutoff - rid / 1000) // 86400))   # 0 = today, 1 = yesterday, ...
        xp += XP.get(ease, 0)
    start = 0 if 0 in days else 1
    streak = 0
    while (start + streak) in days:
        streak += 1
    longest, run, prev = 0, 0, None
    for d in sorted(days):
        run = run + 1 if prev is not None and d == prev + 1 else 1
        longest = max(longest, run)
        prev = d
    level = 1
    while xp >= 50 * level * (level + 1) // 2:   # level n needs 50*(1+2+...+n) points
        level += 1
    floor, ceil = 50 * (level - 1) * level // 2, 50 * level * (level + 1) // 2
    return {"streak": streak, "today": 0 in days, "longest": longest, "days": len(days), "reviews": len(rows),
            "xp": xp, "level": level, "progress": (xp - floor) / (ceil - floor), "next": ceil - xp}


BADGES = [(N_("First steps"), N_("Answer your first card"), lambda s: s["reviews"] >= 1),
          (N_("Warming up"), N_("100 reviews"), lambda s: s["reviews"] >= 100),
          (N_("Committed"), N_("1,000 reviews"), lambda s: s["reviews"] >= 1000),
          (N_("Machine"), N_("10,000 reviews"), lambda s: s["reviews"] >= 10000),
          (N_("On a roll"), N_("3-day streak"), lambda s: s["longest"] >= 3),
          (N_("One week"), N_("7-day streak"), lambda s: s["longest"] >= 7),
          (N_("Habit formed"), N_("30-day streak"), lambda s: s["longest"] >= 30),
          (N_("Unstoppable"), N_("100-day streak"), lambda s: s["longest"] >= 100),
          (N_("Level 5"), N_("Reach level 5"), lambda s: s["level"] >= 5),
          (N_("Level 10"), N_("Reach level 10"), lambda s: s["level"] >= 10)]


def card_render(text, card, kind, col):
    if kind != "answer":
        return text
    try:
        s = stats(col)
    except Exception:
        return text
    if not s["streak"]:
        return text
    line = tn("{n}-day streak · level {level}", "{n}-day streak · level {level}", s["streak"], level=s["level"])
    return text + (f'<p style="text-align:center;opacity:.6;font-size:14px;margin-top:24px">'
                   f'🔥 {html.escape(line, quote=False)}</p>')


router = APIRouter()


@router.get("/")
def page(request: Request):
    with addons.collection(request) as col:
        s = stats(col)
    pct = int(s["progress"] * 100)
    e = lambda x: html.escape(x, quote=False)
    badges = "".join(
        f'<div class="box" style="flex:1;min-width:150px;margin:0;{"" if ok(s) else "opacity:.4"}">'
        f'<b>{"🏅" if ok(s) else "🔒"} {html.escape(t(n))}</b><br><span class="muted">{html.escape(t(d))}</span></div>'
        for n, d, ok in BADGES)
    hint = "" if s["today"] or not s["streak"] else (
        '<p class="lrn">' + e(t("You haven't studied yet today. Do a few cards to keep your streak.")) + '</p>')
    summary = tn("day streak · longest {longest} · {n} day studied",
                 "day streak · longest {longest} · {n} days studied", s["days"], longest=s["longest"])
    points = t("{xp} points · {next} to level {level} · {reviews} reviews in total.",
               xp=s["xp"], next=s["next"], level=s["level"] + 1, reviews=s["reviews"])
    body = f"""<div class="box" style="text-align:center"><div style="font-size:48px">🔥 {s['streak']}</div>
<div class="muted">{e(summary)}</div>{hint}</div>
<div class="box"><h3 style="margin-top:0">{e(t("Level {n}", n=s['level']))}</h3>
<div style="background:var(--bg);border-radius:8px;height:14px;overflow:hidden">
<div style="width:{pct}%;height:100%;background:var(--accent)"></div></div>
<p class="muted">{e(points)}
{e(t("Again 1 point, Hard 2, Good or Easy 3."))}</p></div>
<h3>{e(t("Achievements"))}</h3><div class="row" style="align-items:stretch">{badges}</div>"""
    return addons.page(request, t("My progress"), body)
