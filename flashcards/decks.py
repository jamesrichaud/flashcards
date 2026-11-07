"""Deck list and deck overview: Anki's main window deck browser and its overview screen.

Everything goes through the engine: `deck_due_tree` (counts and the collapsed flag of each
deck), `decks.set_collapsed` (the +/- in front of a parent deck, the same call Anki's deck
browser makes), `decks.reparent` (what Anki's drag-and-drop calls; here a "Move to" list),
`decks.update_dict` for the description (Anki's Description dialog saves `desc` the same
way), and `studied_today` for the "Studied N cards in M minutes today" line.
"""
import html, os, re, tempfile
from html.parser import HTMLParser
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse

from anki.collection import ExportAnkiPackageOptions, DeckIdLimit
from anki.decks import DeckCollapseScope

import app as core
from flashcards.i18n import t, tn

router = APIRouter()
DEFAULT_DECK_ID = 1
SHARED_DECKS_URL = "https://ankiweb.net/shared/decks"
DESC_MAX = 100_000

DECK_CSS = """<style>
.dl td{padding-top:6px;padding-bottom:6px}
.dl td.nm{white-space:nowrap}.dl td.nm form{display:inline}
.dl td.nm a.deck{display:inline-block;vertical-align:middle;padding:10px 0;white-space:normal;max-width:calc(100% - 48px)}
.tg{vertical-align:middle;display:inline-flex;align-items:center;justify-content:center;width:44px;height:44px;min-height:44px;padding:0;
border:0;background:none;color:var(--muted);font-size:20px;line-height:1;border-radius:10px;flex:none}
.tg:hover{background:var(--bg)}.tgs{display:inline-block;vertical-align:middle;width:44px;flex:none}
.today{text-align:center;margin:4px 0 16px}
.desc{text-align:left;max-width:640px;margin:0 auto 14px;overflow-wrap:anywhere}
.desc img{max-width:100%;height:auto}
</style>"""


def clean_msg(s):
    """The engine's translations wrap numbers in bidi marks and break lines with \\n."""
    return re.sub(r"\s+", " ", re.sub(r"[⁨⁩]", "", s)).strip()


# ---------- description: shown as HTML, cleaned so a shared deck can't run scripts in the app ----------
DESC_TAGS = {"b", "i", "u", "s", "em", "strong", "br", "p", "div", "span", "ul", "ol", "li", "a", "img",
             "h1", "h2", "h3", "h4", "h5", "h6", "sub", "sup", "hr", "blockquote", "pre", "code", "font",
             "table", "thead", "tbody", "tr", "td", "th", "small", "big", "center"}
VOID_TAGS = {"br", "img", "hr"}
DESC_ATTRS = {"a": {"href", "title"}, "img": {"src", "alt", "title", "width", "height"},
              "font": {"color"}, "td": {"colspan", "rowspan"}, "th": {"colspan", "rowspan"}}
DROP_CONTENT = {"script", "style", "iframe", "object", "embed", "template", "noscript", "svg", "math",
                "textarea", "select", "title", "head"}


def safe_url(url, img=False):
    url = (url or "").strip()
    scheme = urlsplit(url).scheme.lower()
    if scheme in ("http", "https") or (not img and scheme == "mailto"):
        return url
    if scheme or not url or url.startswith(("/", "\\")):
        return None
    if url.startswith("#"):
        return None if img else url
    # A bare file name in a description is a media file, as on Anki's overview screen.
    return "/media/" + quote(url)


class DescCleaner(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.open, self.skip = [], [], 0

    def handle_starttag(self, tag, attrs):
        if tag in DROP_CONTENT:
            if tag not in VOID_TAGS:
                self.skip += 1
            return
        if self.skip or tag not in DESC_TAGS:
            return
        kept = []
        for k, v in attrs:
            if k not in DESC_ATTRS.get(tag, ()):
                continue
            if k in ("href", "src"):
                v = safe_url(v, img=(k == "src"))
                if v is None:
                    continue
            kept.append(f' {k}="{html.escape(v or "", quote=True)}"')
        if tag == "a":
            kept.append(' target="_blank" rel="noopener noreferrer"')
        self.out.append(f"<{tag}{''.join(kept)}>")
        if tag not in VOID_TAGS:
            self.open.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID_TAGS and self.open and self.open[-1] == tag:
            self.open.pop()
            self.out.append(f"</{tag}>")

    def handle_endtag(self, tag):
        if tag in DROP_CONTENT:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip or tag not in self.open:
            return
        while self.open:
            tg = self.open.pop()
            self.out.append(f"</{tg}>")
            if tg == tag:
                break

    def handle_data(self, data):
        if not self.skip:
            self.out.append(html.escape(data, quote=False))

    def result(self):
        self.close()
        return "".join(self.out) + "".join(f"</{tg}>" for tg in reversed(self.open))


def desc_html(deck):
    desc = deck.get("desc") or ""
    if not desc.strip():
        return ""
    if deck.get("md"):
        # Saved by desktop Anki with "Enable markdown" on. Markdown isn't rendered here,
        # so show the text as written.
        return f'<div class="desc" style="white-space:pre-wrap">{html.escape(desc)}</div>'
    p = DescCleaner()
    p.feed(desc)
    return f'<div class="desc">{p.result()}</div>'


# ---------- deck list ----------
@router.get("/", response_class=HTMLResponse)
def decks(request: Request):
    user = core.current_user(request)
    if not user: return core.need_login(request)
    flash = core.take_flash(request)
    with core.collection(user) as col:
        problem = core.sync_mod.auto_sync(user, col)   # only if turned on in Sync; '' or "!problem"
        if problem:
            flash = "!" + f"{flash.lstrip('!')} {problem.lstrip('!')}".strip()
        tree = col.sched.deck_due_tree()
        hide_default = col.card_count() and not col.find_cards(f"did:{DEFAULT_DECK_ID}")
        rows = []

        def walk(node, depth):
            for ch in node.children:
                if ch.deck_id == DEFAULT_DECK_ID and not ch.children and hide_default:
                    continue
                pad = depth * 22
                name = html.escape(ch.name)
                if ch.children:
                    sign, label = (("+", html.escape(t("Expand {name}", name=ch.name))) if ch.collapsed
                                   else ("−", html.escape(t("Collapse {name}", name=ch.name))))
                    tg = (f'<form method="post" action="/deck/{ch.deck_id}/collapse">'
                          f'<input type="hidden" name="collapsed" value="{0 if ch.collapsed else 1}">'
                          f'<button class="tg" title="{label}" aria-label="{label}" '
                          f'aria-expanded="{"false" if ch.collapsed else "true"}">{sign}</button></form>')
                else:
                    tg = '<span class="tgs"></span>'
                cls = ' class="filtered"' if ch.filtered else ""
                rows.append(f"""<tr{cls}><td class="nm" style="padding-left:{pad}px">{tg}<a class="deck" href="/deck/{ch.deck_id}"{' style="color:var(--accent)"' if ch.filtered else ''}>{name}</a></td>
<td class="n new">{ch.new_count or '·'}</td><td class="n lrn">{ch.learn_count or '·'}</td>
<td class="n rev">{ch.review_count or '·'}</td></tr>""")
                if not ch.collapsed:
                    walk(ch, depth + 1)

        walk(tree, 0)
        today = clean_msg(col.studied_today())
    body = f"""{DECK_CSS}<div class="box"><table class="dl"><tr><th>{t("Deck")}</th><th class="n">{t("New")}</th><th class="n">{t("Learn")}</th>
<th class="n">{t("Due")}</th></tr>{''.join(rows) or '<tr><td colspan=4 class="muted">' + t("No decks yet.") + '</td></tr>'}</table></div>
<p class="today muted" id="studied-today">{html.escape(today)}</p>
<div class="box"><form method="post" action="/deck/new" class="row">
<input name="name" placeholder="{html.escape(t("New deck name (use :: for subdecks)"))}" style="flex:1" required>
<button>{t("Create deck")}</button></form>
<div class="row" style="margin-top:10px"><a class="btn" href="/filtered/new">{t("Create filtered deck")}</a>
<a class="btn" href="/import">{t("Import file")}</a>
<a class="btn" href="{SHARED_DECKS_URL}" target="_blank" rel="noopener noreferrer">{t("Get shared decks ↗")}</a>
<a class="btn" href="/sync">{t("Sync")}</a></div></div>"""
    return core.page(t("Decks"), body, user, "decks", flash)


@router.post("/deck/new")
def deck_new(request: Request, name: str = Form(...)):
    user = core.current_user(request)
    if not user: return core.need_login(request)
    with core.collection(user) as col:
        did = col.decks.id(name.strip())
        # The engine creates decks collapsed (as Anki does); open the new deck's parents
        # so the deck just made can be seen in the list.
        for parent in col.decks.parents(did):
            if parent.get("collapsed"):
                col.decks.set_collapsed(parent["id"], False, DeckCollapseScope.REVIEWER)
    return RedirectResponse("/", 303)


@router.post("/deck/{did}/collapse")
def deck_collapse(request: Request, did: int, collapsed: str = Form("")):
    """Anki's +/- in the deck list. Without a value it toggles."""
    user = core.current_user(request)
    if not user: return core.need_login(request)
    with core.collection(user) as col:
        deck = col.decks.get(did, default=False)
        if deck:
            want = (collapsed == "1") if collapsed in ("0", "1") else not deck.get("collapsed", False)
            col.decks.set_collapsed(did, want, DeckCollapseScope.REVIEWER)
    return RedirectResponse("/", 303)


# ---------- deck overview ----------
def move_targets(col, did):
    """Decks this deck may be moved under: not itself, its own subdecks or filtered decks."""
    name = col.decks.name(did)
    out = []
    for d in col.decks.all_names_and_ids(skip_empty_default=False, include_filtered=False):
        if d.id == did or d.name.startswith(name + "::"):
            continue
        out.append((d.id, d.name))
    return sorted(out, key=lambda x: x[1].lower())


@router.get("/deck/{did}", response_class=HTMLResponse)
def deck_overview(request: Request, did: int):
    user = core.current_user(request)
    if not user: return core.need_login(request)
    request.session.pop("timebox", None)   # like Anki, the overview starts a new timebox
    with core.collection(user) as col:
        deck = col.decks.get(did, default=False)
        if not deck: return RedirectResponse("/", 303)
        core.select_deck(col, did)
        new, lrn, rev = col.sched.counts()
        total = len(col.find_cards(f'"deck:{deck["name"]}"'))
        conf = col.decks.config_dict_for_deck_id(did) if not deck.get("dyn") else None
        parent = deck["name"].rpartition("::")[0]
        targets = move_targets(col, did)
        from flashcards import addons   # late import: addons imports app
        addon_html = addons.html_hook("deck_overview", col, did)
        offline_box = core.offline_mod.overview_box(col, did)
    opts = ""
    if conf:
        preset = t("Preset “{name}”: {new} new/day,\n{rev} reviews/day", name=html.escape(conf["name"]),
                   new=conf["new"]["perDay"], rev=conf["rev"]["perDay"])
        opts = f"""<div class="box row"><div style="flex:1;min-width:200px"><h3 style="margin:0">{t("Deck options")}</h3>
<span class="muted" style="font-size:14px">{preset}</span></div><a class="btn" href="/deck/{did}/custom">{t("Custom study")}</a>
<a class="btn" href="/deck/{did}/options">{t("Options")}</a></div>"""
    else:
        opts = f"""<div class="box row"><div style="flex:1;min-width:200px"><h3 style="margin:0">{t("Filtered deck")}</h3>
<span class="muted" style="font-size:14px">{t("Cards are borrowed from their home decks until the deck is emptied.")}</span></div>
<a class="btn" href="/deck/{did}/filtered">{t("Options")}</a>
<form method="post" action="/deck/{did}/rebuild"><button>{t("Rebuild")}</button></form>
<form method="post" action="/deck/{did}/empty"><button>{t("Empty")}</button></form></div>"""
    desc_btn = (f'<a class="btn" href="/deck/{did}/description">{t("Description")}</a>' if conf else "")
    choices = [f'<option value="0"{" selected" if not parent else ""}>{t("(top level)")}</option>']
    # English has always said "1 cards in deck"; both forms kept the same so English output is unchanged.
    in_deck = tn("{n} cards in deck", "{n} cards in deck", total)
    if new + lrn + rev:
        study = f'<a class="btn primary" style="min-width:200px" href="/study/{did}">{t("Study now")}</a>'
    else:
        study = "<p>" + t("Congratulations! You have finished this deck for now.") + "</p>"
        if conf:
            link = f'<a href="/deck/{did}/custom">{t("Custom study")}</a>'
            study += ('<p class="muted">' + t("To study outside the regular schedule, use {link}.", link=link)
                      + "</p>")
    for tid, tname in targets:
        sel = " selected" if tname == parent else ""
        choices.append(f'<option value="{tid}"{sel}>{html.escape(tname)}</option>')
    body = f"""{DECK_CSS}<div class="box" style="text-align:center"><h2 style="margin-top:0">{html.escape(deck['name'])}</h2>
{desc_html(deck) if conf else ''}
<div class="row" style="justify-content:center;gap:28px;font-size:20px">
<span class="new">{t("New {n}", n=f"<b>{new}</b>")}</span><span class="lrn">{t("Learning {n}", n=f"<b>{lrn}</b>")}</span>
<span class="rev">{t("To review {n}", n=f"<b>{rev}</b>")}</span></div>
<p class="muted">{in_deck}</p>
{study}
</div>{addon_html}{opts}{core.offline_mod.SWITCH_CSS}{offline_box}
<div class="box row"><a class="btn" href="/browse?q=%22deck:{html.escape(deck['name'])}%22">{t("Browse cards")}</a>
<a class="btn" href="/export?did={did}">{t("Export…")}</a>
<a class="btn" href="/stats?did={did}">{t("Stats")}</a>{desc_btn}</div>
<div class="box"><form method="post" action="/deck/{did}/rename" class="row">
<input name="name" value="{html.escape(deck['name'])}" style="flex:1;min-width:200px" aria-label="{html.escape(t("Deck name"))}"><button>{t("Rename")}</button></form>
<form method="post" action="/deck/{did}/move" class="row" style="margin-top:10px">
<label for="move-to" style="margin:0">{t("Move under")}</label>
<select id="move-to" name="parent" style="flex:1;width:auto;min-width:200px">{''.join(choices)}</select>
<button>{t("Move")}</button></form>
<form method="post" action="/deck/{did}/delete" style="margin-top:10px"
 onsubmit="return confirm({html.escape(core.js(t("Delete this deck and all its cards?")))})">
<button class="danger">{t("Delete deck")}</button></form></div>"""
    return core.page(deck["name"], body, user, "decks", core.take_flash(request))


@router.post("/deck/{did}/rename")
def deck_rename(request: Request, did: int, name: str = Form("")):
    user = core.current_user(request)
    if not user: return core.need_login(request)
    if not name.strip():
        core.set_flash(request, "!" + t("Please type a deck name."))
        return RedirectResponse(f"/deck/{did}", 303)
    with core.collection(user) as col:
        try:
            col.decks.rename(did, name.strip())
        except Exception as e:
            core.set_flash(request, f"!{e}")
    return RedirectResponse(f"/deck/{did}", 303)


@router.post("/deck/{did}/move")
def deck_move(request: Request, did: int, parent: int = Form(0)):
    """Anki's drag-and-drop in the deck list, as a form: the engine's reparent call."""
    user = core.current_user(request)
    if not user: return core.need_login(request)
    with core.collection(user) as col:
        if not col.decks.get(did, default=False):
            return RedirectResponse("/", 303)
        if parent and not col.decks.get(parent, default=False):
            core.set_flash(request, "!" + t("That deck no longer exists."))
            return RedirectResponse(f"/deck/{did}", 303)
        old = col.decks.name(did)
        try:
            moved = col.decks.reparent([did], parent).count
        except Exception as e:
            core.set_flash(request, f"!{e}")
            return RedirectResponse(f"/deck/{did}", 303)
        new = col.decks.name(did)
        if parent:
            # Show the new home expanded, so the moved deck can be seen in the list.
            col.decks.set_collapsed(parent, False, DeckCollapseScope.REVIEWER)
    core.set_flash(request, t("Moved “{old}” to “{new}”.", old=old, new=new) if moved and new != old
                   else "!" + t("The deck can't be moved there."))
    return RedirectResponse(f"/deck/{did}", 303)


@router.get("/deck/{did}/description", response_class=HTMLResponse)
def deck_description_page(request: Request, did: int):
    user = core.current_user(request)
    if not user: return core.need_login(request)
    with core.collection(user) as col:
        deck = col.decks.get(did, default=False)
        if not deck or deck.get("dyn"):
            return RedirectResponse(f"/deck/{did}" if deck else "/", 303)
    md = ('<p class="muted" style="font-size:14px">' + t("This description was written in desktop Anki with markdown "
          "on; it is shown as plain text here. Saving here turns markdown off.") + "</p>" if deck.get("md") else "")
    allowed = t("Shown on the deck's overview screen. Basic HTML is allowed\n(e.g. {tags}); scripts and styles are not shown.",
                tags="&lt;b&gt;, &lt;i&gt;, &lt;a href&gt;, &lt;img src&gt;")
    title = t("Description: {name}", name=html.escape(deck["name"]))
    body = f"""{DECK_CSS}<div class="box"><h2 style="margin-top:0">{title}</h2>
<p class="muted" style="font-size:14px">{allowed}</p>{md}
<form method="post"><label for="desc">{t("Description")}</label>
<textarea id="desc" name="desc" style="min-height:220px" maxlength="{DESC_MAX}">{html.escape(deck.get('desc') or '')}</textarea>
<div class="row" style="margin-top:12px"><button class="primary">{t("Save")}</button>
<a class="btn" href="/deck/{did}">{t("Cancel")}</a></div></form></div>
<div class="box"><h3 style="margin-top:0">{t("Preview")}</h3>{desc_html(deck) or '<p class="muted">' + t("No description.") + '</p>'}</div>"""
    return core.page(t("Description"), body, user, "decks", core.take_flash(request))


@router.post("/deck/{did}/description")
def deck_description_save(request: Request, did: int, desc: str = Form("")):
    user = core.current_user(request)
    if not user: return core.need_login(request)
    with core.collection(user) as col:
        deck = col.decks.get(did, default=False)
        if not deck or deck.get("dyn"):
            return RedirectResponse("/", 303)
        deck["desc"] = desc.replace("\r\n", "\n")[:DESC_MAX]
        deck["md"] = False
        col.decks.update_dict(deck)
    core.set_flash(request, t("Description saved."))
    return RedirectResponse(f"/deck/{did}", 303)


@router.post("/deck/{did}/delete")
def deck_delete(request: Request, did: int):
    user = core.current_user(request)
    if not user: return core.need_login(request)
    with core.collection(user) as col:
        col.decks.remove([did])
    core.set_flash(request, t("Deck deleted."))
    return RedirectResponse("/", 303)


@router.get("/deck/{did}/export")
def deck_export(request: Request, did: int):
    user = core.current_user(request)
    if not user: return core.need_login(request)
    with core.collection(user) as col:
        name = col.decks.name(did)
        out = os.path.join(tempfile.mkdtemp(), "deck.apkg")
        col.export_anki_package(out_path=out, options=ExportAnkiPackageOptions(
            with_scheduling=True, with_deck_configs=True, with_media=True, legacy=False),
            limit=DeckIdLimit(did))
    safe = re.sub(r"[^\w.-]+", "_", name) or "deck"
    return FileResponse(out, filename=f"{safe}.apkg", media_type="application/octet-stream")
