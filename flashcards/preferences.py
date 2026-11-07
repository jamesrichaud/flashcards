"""Preferences (Anki's Tools > Preferences), the parts that make sense in a browser.

Scheduling and review settings are the engine's own preferences (`col.get_preferences()` /
`col.set_preferences()`), stored in the collection like desktop Anki, so they follow the user
to every device and to Anki itself after a sync:
  - Next day starts at (hours past midnight)
  - Learn ahead limit (minutes)
  - Timebox time limit (minutes, 0 = off)
  - Show remaining card count
  - Show next review time above answer buttons
The theme (follow system / light / dark) is this app's own setting, kept in the collection's
config under `flashcardsTheme`. Labels come from the engine's translations.
"""
import html, re

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

import app as core

router = APIRouter()


def clean(s):
    return re.sub(r"[⁨⁩]", "", s or "")


def int_field(form, name, lo, hi):
    """Whole number in [lo, hi] from the form, or raise ValueError with a readable message."""
    raw = str(form.get(name, "")).strip()
    try:
        v = int(raw)
    except ValueError:
        raise ValueError(f"Please enter a whole number for {LABELS[name]}.")
    if not lo <= v <= hi:
        raise ValueError(f"{LABELS[name]} must be between {lo} and {hi}.")
    return v


LABELS = {"rollover": "Next day starts at", "learn_ahead": "Learn ahead limit", "timebox": "Timebox time limit"}


def body_html(col, flash_err=""):
    tr = col.tr
    p = col.get_preferences()
    s, r = p.scheduling, p.reviewing
    theme = core.theme_of(col)
    t = lambda f: html.escape(clean(f()))
    mins = t(tr.preferences_mins)
    themes = [("system", tr.preferences_theme_follow_system), ("light", tr.preferences_theme_light),
              ("dark", tr.preferences_theme_dark)]
    theme_opts = "".join(
        f'<label class="opt"><input type="radio" name="theme" value="{k}"{" checked" if k == theme else ""}>'
        f'{t(f)}</label>' for k, f in themes)
    def check(name, on, label, hint=""):
        h = f'<br><span class="muted hint">{hint}</span>' if hint else ""
        return (f'<label class="opt"><input type="checkbox" name="{name}" value="1"{" checked" if on else ""}>'
                f'<span>{label}{h}</span></label>')
    def number(name, value, lo, hi, unit, hint):
        return (f'<label for="{name}">{html.escape(LABELS[name])}</label>'
                f'<div class="row num"><input id="{name}" name="{name}" type="number" inputmode="numeric" '
                f'min="{lo}" max="{hi}" step="1" value="{value}" required><span>{unit}</span></div>'
                f'<div class="muted hint">{hint}</div>')
    return f"""<style>.opt{{display:flex;gap:12px;align-items:center;color:var(--ink);font-size:17px;margin:4px 0;
min-height:44px;cursor:pointer}}.opt input{{width:22px;height:22px;flex:none;margin:0}}
.num input{{width:110px;flex:none}}.hint{{font-size:14px}}.box h3{{margin-top:0}}</style>
<p><a href="/settings" class="btn">← Settings</a></p>
<form method="post" action="/preferences">
<div class="box"><h3>{t(tr.preferences_scheduling)}</h3>
{number("rollover", s.rollover, 0, 23, t(tr.preferences_hours_past_midnight),
        "When a new day begins for your cards and daily limits. 4 means 4 AM.")}
{number("learn_ahead", s.learn_ahead_secs // 60, 0, 9999, mins,
        "When nothing else is due, learning cards due within this many minutes are shown early.")}
{number("timebox", r.time_limit_secs // 60, 0, 9999, mins,
        "While studying, say how many cards you have done each time this many minutes pass. 0 turns it off.")}
</div>
<div class="box"><h3>{t(tr.preferences_review)}</h3>
{check("show_counts", r.show_remaining_due_counts, t(tr.preferences_show_remaining_card_count))}
{check("show_times", r.show_intervals_on_buttons, t(tr.preferences_show_next_review_time_above_answer))}
</div>
<div class="box"><h3>{t(tr.preferences_appearance)}</h3>
<label>{t(tr.preferences_theme)}</label>{theme_opts}
<label>{t(tr.preferences_language)}</label><div>English</div>
</div>
<p><button class="primary">Save</button></p>
</form>"""


@router.get("/preferences", response_class=HTMLResponse)
def preferences_page(request: Request):
    user = core.current_user(request)
    if not user: return core.need_login(request)
    with core.collection(user) as col:
        body = body_html(col)
        title = clean(col.tr.preferences_preferences())
    return core.page(title, body, user, "settings", core.take_flash(request))


@router.post("/preferences")
async def preferences_save(request: Request):
    user = core.current_user(request)
    if not user: return core.need_login(request)
    form = await request.form()
    try:
        rollover = int_field(form, "rollover", 0, 23)
        learn_ahead = int_field(form, "learn_ahead", 0, 9999)
        timebox = int_field(form, "timebox", 0, 9999)
    except ValueError as e:
        core.set_flash(request, "!" + str(e))
        return RedirectResponse("/preferences", 303)
    theme = str(form.get("theme", ""))
    with core.collection(user) as col:
        p = col.get_preferences()
        p.scheduling.rollover = rollover
        p.scheduling.learn_ahead_secs = learn_ahead * 60
        p.reviewing.time_limit_secs = timebox * 60
        p.reviewing.show_remaining_due_counts = form.get("show_counts") == "1"
        p.reviewing.show_intervals_on_buttons = form.get("show_times") == "1"
        col.set_preferences(p)
        if theme in core.THEMES and theme != core.theme_of(col):
            col.set_config(core.THEME_KEY, theme)
    core.set_flash(request, "Preferences saved.")
    return RedirectResponse("/preferences", 303)
