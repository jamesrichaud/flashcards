"""LaTeX on cards: [latex]..[/latex], [$]..[/$] and [$$]..[/$$], as in desktop Anki.

The engine does the work: `col._backend.extract_latex` turns the tags into
`<img class=latex src="latex-<hash>.png">` and `anki.latex.render_latex_returning_errors`
(run by the engine's `card_did_render` hook) makes any image that isn't in the media folder
yet, if the "Generate LaTeX images" preference (`Config.Bool.RENDER_LATEX`) is on.

Two changes for a web server:
- `anki.latex._save_latex_image` is replaced with `save_latex_image` below. The engine's
  version `os.chdir`s into a shared temp folder, which is unsafe when several requests run at
  once; ours runs latex + dvipng (or dvisvgm for SVG note types) in a private temp folder with
  `cwd=`, a time limit, no shell escape, and kpathsea's paranoid file access (`openin_any=p`,
  `openout_any=p`) so a card can't read files elsewhere on the server. As older Anki did, a
  few TeX commands are refused in the card's LaTeX (they can still go in the note type's
  LaTeX header). Every engine caller (card rendering, `col.media.render_all_latex()` for
  Check Media, `files_in_str`) uses it, because they look the function up at call time.
- The render hook is swapped for `on_card_did_render`: same engine call, but an image that
  can't be made (LaTeX not installed, generation turned off, or a LaTeX error) is shown as
  its LaTeX source with one short note, instead of a broken image.
"""
import html, os, re, shutil, subprocess, tempfile

import anki.latex as engine_latex
from anki import hooks
from anki.config import Config

from flashcards.i18n import t, N_

TIMEOUT = 20   # seconds per command (latex, dvipng)
LOG_MAX = 4000
# Older Anki refused these in a card's LaTeX for security; the message is the engine's own.
BANNED = ("\\write18", "\\readline", "\\input", "\\include", "\\catcode", "\\openout",
          "\\write", "\\loop", "\\def", "\\shipout")
NOT_INSTALLED = N_("LaTeX isn't installed on the server")   # translate with t(NOT_INSTALLED)
IMG_PAT = re.compile(r'<img class=latex alt="([^"]*)" src="([^"]+)">')


def clean(s):
    return re.sub(r"[⁨⁩]", "", s or "")


def commands(svg):
    return engine_latex.svgCommands if svg else engine_latex.pngCommands


def installed(svg=False):
    """True if the programs needed to make LaTeX images are on the server."""
    return all(shutil.which(cmd[0]) for cmd in commands(svg))


def enabled(col):
    return col.get_config_bool(Config.Bool.RENDER_LATEX)


def set_enabled(col, on):
    col.set_config_bool(Config.Bool.RENDER_LATEX, bool(on))


def not_installed_msg(svg=False):
    names = [cmd[0] for cmd in commands(svg)]
    progs = t("{first} and {last}", first=", ".join(names[:-1]), last=names[-1]) if len(names) > 1 else names[0]
    return t("LaTeX isn't installed on the server ({programs}), so it is shown as source.", programs=progs)


def save_latex_image(col, extracted, header, footer, svg):
    """Drop-in for anki.latex._save_latex_image: make one image and add it to the media
    folder. Returns None, or an error message (HTML)."""
    lower = extracted.latex_body.lower()
    for bad in BANNED:
        if bad in lower:
            return html.escape(clean(col.tr.media_for_security_reasons_is_not(val=bad)))
    if not installed(svg):
        return html.escape(not_installed_msg(svg))
    ext = "svg" if svg else "png"
    env = dict(os.environ, openin_any="p", openout_any="p", shell_escape="f")
    with tempfile.TemporaryDirectory(prefix="fc-latex-") as tmp:
        with open(os.path.join(tmp, "tmp.tex"), "w", encoding="utf8") as f:
            f.write(f"{header}\n{extracted.latex_body}\n{footer}")
        log = []
        for cmd in commands(svg):
            argv = list(cmd)
            if argv[0] == "latex":
                argv.insert(1, "-no-shell-escape")
            try:
                p = subprocess.run(argv, cwd=tmp, env=env, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=TIMEOUT)
                log.append(p.stdout.decode("utf8", "replace"))
                failed = p.returncode != 0
            except subprocess.TimeoutExpired:
                log.append(t("Stopped after {n} seconds.", n=TIMEOUT))
                failed = True
            except OSError as e:
                log.append(str(e))
                failed = True
            if failed:
                text = "".join(log)[-LOG_MAX:]
                return (f"{html.escape(clean(col.tr.media_error_executing(val=argv[0])))}"
                        f"<details><summary>{html.escape(t('Log'), quote=False)}</summary><pre style=\"white-space:pre-wrap;text-align:left;"
                        f"font-size:12px\">{html.escape(text)}</pre></details>")
        out = os.path.join(tmp, f"tmp.{ext}")
        if not os.path.exists(out):
            return html.escape(clean(col.tr.media_error_executing(val=commands(svg)[-1][0])))
        with open(out, "rb") as f:
            data = f.read()
    col.media.write_data(extracted.filename, data)
    return None


NOTE_STYLE = ("display:block;margin:12px auto 0;max-width:36em;padding:8px 12px;border-radius:8px;"
              "font:14px/1.4 -apple-system,system-ui,sans-serif;background:rgba(128,128,128,.15);text-align:left")


def render(text, model, col):
    """Card HTML with LaTeX tags turned into images; any image that couldn't be made is
    shown as its source, with one note saying why."""
    if "[" not in text:   # every LaTeX tag starts with [
        return text
    out, errors = engine_latex.render_latex_returning_errors(text, model, col)
    if not errors:
        return out
    have = col.media.have

    def source(m):
        if have(html.unescape(m.group(2))):
            return m.group(0)
        return f'<code class="latex-source" style="white-space:pre-wrap">{m.group(1)}</code>'
    out = IMG_PAT.sub(source, out)
    if not enabled(col):
        note = (html.escape(clean(col.tr.preferences_latex_generation_disabled())) +
                ' ' + html.escape(t("Turn on “Generate LaTeX images” in Preferences."), quote=False))
    else:
        note = "<br>".join(dict.fromkeys(errors))   # same message once
    return out + f'<div class="latex-error" style="{NOTE_STYLE}">{note}</div>'


def on_card_did_render(output, ctx):
    output.question_text = render(output.question_text, ctx.note_type(), ctx.col())
    output.answer_text = render(output.answer_text, ctx.note_type(), ctx.col())


def install():
    """Use our image maker and render hook instead of the engine's (once)."""
    engine_latex._save_latex_image = save_latex_image
    lst = hooks.card_did_render._hooks
    if on_card_did_render in lst:
        return
    if engine_latex.on_card_did_render in lst:   # same place in the list as the engine's
        lst[lst.index(engine_latex.on_card_did_render)] = on_card_did_render
    else:
        lst.append(on_card_did_render)


install()
