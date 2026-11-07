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
config under `flashcardsTheme`; so is "tap the card to answer" (`flashcardsTapZones`, see
flashcards/touch.py). "Generate LaTeX images" is the engine's own setting (`Config.Bool.RENDER_LATEX`,
see flashcards/latex.py). Labels come from the engine's translations.
"""
import html, re

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

import app as core
from flashcards import i18n
from flashcards.i18n import t, N_

router = APIRouter()


def clean(s):
    return re.sub(r"[⁨⁩]", "", s or "")


def int_field(form, name, lo, hi):
    """Whole number in [lo, hi] from the form, or raise ValueError with a readable message."""
    raw = str(form.get(name, "")).strip()
    try:
        v = int(raw)
    except ValueError:
        raise ValueError(t("Please enter a whole number for {label}.", label=t(LABELS[name])))
    if not lo <= v <= hi:
        raise ValueError(t("{label} must be between {lo} and {hi}.", label=t(LABELS[name]), lo=lo, hi=hi))
    return v


LABELS = {"rollover": N_("Next day starts at"), "learn_ahead": N_("Learn ahead limit"),
          "timebox": N_("Timebox time limit")}


def lang_select(user, scope):
    """Language picker: Automatic (the browser's language) or one of the shipped languages."""
    saved = core.user_lang(user) if user else ""
    auto = i18n.LANGUAGES[i18n.request_default(scope or {})][0]
    opts = [("", i18n.t("Automatic ({language})", language=auto))]
    opts += [(code, name) for code, (name, _) in i18n.LANGUAGES.items()]
    options = ""
    for code, name in opts:
        attrs = (f' lang="{code}"' if code else "") + (" selected" if code == saved else "")
        options += f'<option value="{code}"{attrs}>{html.escape(name)}</option>'
    hint = html.escape(i18n.t("Automatic uses the language your browser asks for."))
    return f'<select id="lang" name="lang">{options}</select><div class="muted hint">{hint}</div>'


def body_html(col, flash_err="", user=None, request_scope=None):
    tr = col.tr
    p = col.get_preferences()
    s, r = p.scheduling, p.reviewing
    theme = core.theme_of(col)
    eng = lambda f: html.escape(clean(f()))   # the engine's own words (col.tr)
    e = lambda s: html.escape(s, False)       # our translated text in an HTML text node
    mins = eng(tr.preferences_mins)
    themes = [("system", tr.preferences_theme_follow_system), ("light", tr.preferences_theme_light),
              ("dark", tr.preferences_theme_dark)]
    theme_opts = "".join(
        f'<label class="opt"><input type="radio" name="theme" value="{k}"{" checked" if k == theme else ""}>'
        f'{eng(f)}</label>' for k, f in themes)
    latex_hint = e(t("Turns [latex], [$] and [$$] on cards into pictures, made on the server. LaTeX can run commands "
                     "and read files, so only turn this on if you trust the cards you study, as in Anki."))
    if not core.latex_mod.installed():
        missing = "<b>" + e(t("LaTeX isn't installed on this server")) + "</b>"
        latex_hint += " " + e(t("{missing}, so cards show the LaTeX source instead.")).replace("{missing}", missing)
    def check(name, on, label, hint=""):
        h = f'<br><span class="muted hint">{hint}</span>' if hint else ""
        return (f'<label class="opt"><input type="checkbox" name="{name}" value="1"{" checked" if on else ""}>'
                f'<span>{label}{h}</span></label>')
    def number(name, value, lo, hi, unit, hint):
        return (f'<label for="{name}">{html.escape(t(LABELS[name]))}</label>'
                f'<div class="row num"><input id="{name}" name="{name}" type="number" inputmode="numeric" '
                f'min="{lo}" max="{hi}" step="1" value="{value}" required><span>{unit}</span></div>'
                f'<div class="muted hint">{hint}</div>')
    return f"""<style>.opt{{display:flex;gap:12px;align-items:center;color:var(--ink);font-size:17px;margin:4px 0;
min-height:44px;cursor:pointer}}.opt input{{width:22px;height:22px;flex:none;margin:0}}
.num input{{width:110px;flex:none}}.hint{{font-size:14px}}.box h3{{margin-top:0}}</style>
<p><a href="/settings" class="btn">{e(t("← Settings"))}</a></p>
<form method="post" action="/preferences">
<div class="box"><h3>{eng(tr.preferences_scheduling)}</h3>
{number("rollover", s.rollover, 0, 23, eng(tr.preferences_hours_past_midnight),
        e(t("When a new day begins for your cards and daily limits. 4 means 4 AM.")))}
{number("learn_ahead", s.learn_ahead_secs // 60, 0, 9999, mins,
        e(t("When nothing else is due, learning cards due within this many minutes are shown early.")))}
{number("timebox", r.time_limit_secs // 60, 0, 9999, mins,
        e(t("While studying, say how many cards you have done each time this many minutes pass. 0 turns it off.")))}
</div>
<div class="box"><h3>{eng(tr.preferences_review)}</h3>
{check("show_counts", r.show_remaining_due_counts, eng(tr.preferences_show_remaining_card_count))}
{check("show_times", r.show_intervals_on_buttons, eng(tr.preferences_show_next_review_time_above_answer))}
{check("tap_zones", core.touch_mod.tap_zones(col), e(t("Tap the card to answer")),
       e(t("Tap the card to show the answer, then tap its left half for Again or its right half for Good.")))}
</div>
<div class="box"><h3>{eng(tr.preferences_editing)}</h3>
{check("render_latex", core.latex_mod.enabled(col), eng(tr.preferences_generate_latex_images_automatically),
       latex_hint)}
</div>
<div class="box"><h3>{eng(tr.preferences_appearance)}</h3>
<label>{eng(tr.preferences_theme)}</label>{theme_opts}
<label for="lang">{eng(tr.preferences_language)}</label>{lang_select(user, request_scope)}
</div>
<p><button class="primary">{e(t("Save"))}</button></p>
</form>"""


@router.get("/preferences", response_class=HTMLResponse)
def preferences_page(request: Request):
    user = core.current_user(request)
    if not user: return core.need_login(request)
    with core.collection(user) as col:
        body = body_html(col, user=user, request_scope=request.scope)
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
    lang = str(form.get("lang", core.user_lang(user)))
    lang_changed = "lang" in form and (lang if lang in i18n.LANGUAGES else "") != core.user_lang(user)
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
        latex_on = form.get("render_latex") == "1"
        if latex_on != core.latex_mod.enabled(col):
            core.latex_mod.set_enabled(col, latex_on)
        tap = form.get("tap_zones") == "1"
        if tap != core.touch_mod.tap_zones(col):
            col.set_config(core.touch_mod.TAP_KEY, tap)
    if lang_changed:
        core.set_user_lang(user, lang)
        core.reopen_collection(user)   # the engine's own messages switch language too
        i18n.forget_request_lang()
    core.set_flash(request, i18n.t("Preferences saved."))
    return RedirectResponse("/preferences", 303)
