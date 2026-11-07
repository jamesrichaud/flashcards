"""Note types: Anki's "Manage Note Types", Fields, Cards (template editor) and Change Note Type.

Everything goes through the engine: stock note types come from `get_stock_notetype_legacy`
(via `anki.stdmodels.get_stock_notetypes`), field and card type changes use the
`col.models` helpers followed by `update_dict` (the engine checks the templates), the live
preview renders the unsaved template with `TemplateRenderContext.from_card_layout` (what
Anki's card layout screen uses), and changing the note type of notes uses
`change_notetype_info` / `change_notetype_of_notes`.
"""
import html, re
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse

from anki.cards import Card
from anki.consts import MODEL_CLOZE
from anki.stdmodels import get_stock_notetypes
from anki.template import TemplateRenderContext

import app as core
from flashcards.i18n import t, tn

router = APIRouter()

CSS = """<style>
.nt-row{display:flex;gap:10px;flex-wrap:wrap;align-items:center;justify-content:space-between;padding:12px 0;
border-bottom:1px solid var(--line)}.nt-row:last-child{border-bottom:0}
.nt-row form{margin:0}.nt-name{font-weight:500;flex:1;min-width:180px}
details.opts>summary{cursor:pointer;list-style:none;display:inline-block}
details.opts>summary::-webkit-details-marker{display:none}
.code{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:14px;min-height:170px;
white-space:pre;tab-size:4}
.tgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:16px}
.pv{width:100%;min-height:160px;border:1px solid var(--line);border-radius:12px;background:var(--card)}
.chk{display:flex;gap:8px;align-items:center;margin:0;padding:8px 0;color:var(--ink);font-size:15px;min-height:44px}
.chk input{width:22px;height:22px;margin:0}
.ctype{display:flex;gap:6px;flex-wrap:wrap}.ctype .btn{min-width:44px}
</style>"""


def clean_err(e):
    """Engine messages carry HTML and bidi isolation marks; flashes are plain text."""
    s = re.sub(r"[⁨⁩]", "", str(e))
    s = re.sub(r"<br\s*/?>", " ", s)
    s = re.sub(r"<a [^>]*>.*?</a>", "", s, flags=re.S)
    return html.unescape(re.sub(r"<[^>]+>", "", s)).strip() or t("That did not work.")


def get_nt(col, ntid):
    nt = col.models.get(ntid)
    if not nt:
        raise LookupError
    return nt


def is_cloze(nt):
    return nt["type"] == MODEL_CLOZE


def notes_n(n):
    return tn("{n} note", "{n} notes", n)


def confirm_attr(text):
    """onsubmit="..." that asks before submitting (text is plain; quoted for JS and the attribute)."""
    return f'onsubmit="return confirm({html.escape(core.js(text))})"'


def guard(request):
    """The logged-in user, or None."""
    return core.current_user(request)


# ---------- list, add, rename, delete ----------
def stock_types(col):
    return [nt_fn(col) for _, nt_fn in get_stock_notetypes(col)[:6]]


def list_html(col):
    rows = []
    for m in sorted(col.models.all_use_counts(), key=lambda m: m.name.lower()):
        nm = html.escape(m.name)
        rows.append(f"""<div class="nt-row"><span class="nt-name">{nm}
<span class="muted" style="font-weight:400;font-size:14px"> · {html.escape(notes_n(m.use_count))}</span></span>
<span class="row"><a class="btn" href="/notetypes/{m.id}/fields">{html.escape(t("Fields"))}</a>
<a class="btn" href="/notetypes/{m.id}/cards">{html.escape(t("Cards"))}</a>
<a class="btn" href="/browse?q={quote(f'note:{m.name}')}">{html.escape(t("Browse"))}</a>
<details class="opts"><summary class="btn">{html.escape(t("More"))} ▾</summary>
<div class="row" style="margin-top:8px"><form method="post" action="/notetypes/{m.id}/rename" class="row">
<input name="name" value="{nm}" aria-label="{html.escape(t("New name"))}" style="width:220px" required><button>{html.escape(t("Rename"))}</button></form>
<form method="post" action="/notetypes/{m.id}/delete"
{confirm_attr(tn("Delete {name} and its {n} note? This cannot be undone.",
                 "Delete {name} and its {n} notes? This cannot be undone.", m.use_count, name=m.name))}>
<button class="danger">{html.escape(t("Delete"))}</button></form></div></details></span></div>""")
    opts = "".join(f'<option value="stock:{i}">{html.escape(t("Add: {name}", name=nt["name"]))}</option>'
                   for i, nt in enumerate(stock_types(col)))
    opts += "".join(f'<option value="clone:{m.id}">{html.escape(t("Clone: {name}", name=m.name))}</option>'
                    for m in sorted(col.models.all_names_and_ids(), key=lambda m: m.name.lower()))
    return f"""{CSS}<div class="box"><h2 style="margin-top:0">{html.escape(t("Note types"))}</h2>{''.join(rows)}</div>
<div class="box"><h3 style="margin-top:0">{html.escape(t("Add note type"))}</h3><form method="post" action="/notetypes/add">
<label for="base">{html.escape(t("Start from"))}</label><select id="base" name="base">{opts}</select>
<label for="ntname">{html.escape(t("Name (leave blank to use the type's name)"), quote=False)}</label><input id="ntname" name="name">
<p style="margin-bottom:0"><button class="primary">{html.escape(t("Add"))}</button></p></form></div>"""


@router.get("/notetypes", response_class=HTMLResponse)
def notetypes(request: Request):
    user = guard(request)
    if not user: return core.need_login(request)
    with core.collection(user) as col:
        body = list_html(col)
    return core.page(t("Note types"), body, user, "settings", core.take_flash(request))


@router.post("/notetypes/add")
async def notetype_add(request: Request):
    user = guard(request)
    if not user: return core.need_login(request)
    form = await request.form()
    base, name = str(form.get("base", "")), str(form.get("name", "")).strip()
    with core.collection(user) as col:
        try:
            kind, _, arg = base.partition(":")
            if kind == "stock":
                stock = stock_types(col)
                nt = stock[int(arg)]
            elif kind == "clone":
                nt = col.models.copy(get_nt(col, int(arg)), add=False)
            else:
                raise ValueError(t("Choose a note type to start from."))
            if name:
                nt["name"] = name
            col.models.ensure_name_unique(nt)
            new_id = col.models.add_dict(nt).id
            core.set_flash(request, t("Added note type “{name}”.", name=col.models.get(new_id)['name']))
        except (ValueError, IndexError, LookupError) as e:
            core.set_flash(request, "!" + (str(e) if isinstance(e, ValueError) and str(e) else t("Unknown note type.")))
            return RedirectResponse("/notetypes", 303)
        except Exception as e:
            core.set_flash(request, "!" + clean_err(e))
            return RedirectResponse("/notetypes", 303)
    return RedirectResponse(f"/notetypes/{new_id}/fields", 303)


@router.post("/notetypes/{ntid}/rename")
async def notetype_rename(request: Request, ntid: int):
    user = guard(request)
    if not user: return core.need_login(request)
    form = await request.form()
    name = str(form.get("name", "")).strip()
    with core.collection(user) as col:
        try:
            nt = get_nt(col, ntid)
            if not name:
                raise ValueError(t("Enter a name."))
            if name != nt["name"]:
                nt["name"] = name
                col.models.ensure_name_unique(nt)
                col.models.update_dict(nt)
                core.set_flash(request, t("Renamed to “{name}”.", name=nt['name']))
        except LookupError:
            pass
        except Exception as e:
            core.set_flash(request, "!" + clean_err(e))
    return RedirectResponse("/notetypes", 303)


@router.post("/notetypes/{ntid}/delete")
def notetype_delete(request: Request, ntid: int):
    user = guard(request)
    if not user: return core.need_login(request)
    with core.collection(user) as col:
        try:
            nt = get_nt(col, ntid)
            if len(col.models.all_names_and_ids()) < 2:
                raise ValueError(t("You can't delete the last note type."))
            col.models.remove(ntid)
            core.set_flash(request, t("Deleted “{name}”.", name=nt['name']))
        except LookupError:
            pass
        except Exception as e:
            core.set_flash(request, "!" + clean_err(e))
    return RedirectResponse("/notetypes", 303)


# ---------- fields ----------
def clean_field_name(nt, name, skip=None):
    """Anki's field-name rules (aqt/fields.py): no : { } ", no leading # / ^, unique."""
    name = re.sub(r'[:{}"]', "", name.replace("\n", " ")).strip()
    name = name.lstrip("#/^").strip()
    if not name:
        raise ValueError(t("Enter a field name."))
    for f in nt["flds"]:
        if f is not skip and f["name"].lower() == name.lower():
            raise ValueError(t("That field name is already used."))
    return name


def fields_html(col, nt):
    ntid = nt["id"]
    n = len(nt["flds"])
    sort_idx = nt["sortf"]
    rows = []
    for i, f in enumerate(nt["flds"]):
        nm = html.escape(f["name"])
        pos = "".join(f'<option value="{j}"{" selected" if j == i else ""}>{j + 1}</option>' for j in range(n))
        def hidden(op):
            return f'<input type="hidden" name="op" value="{op}"><input type="hidden" name="idx" value="{i}">'
        chk = lambda key: " checked" if f.get(key) else ""
        rows.append(f"""<div class="nt-row"><span class="nt-name">{i + 1}. {nm}
{' <span class="muted" style="font-size:14px;font-weight:400">· ' + html.escape(t("sort field")) + '</span>' if i == sort_idx else ''}</span>
<span class="row"><form method="post" action="/notetypes/{ntid}/fields" class="row">{hidden("move")}
<select name="to" aria-label="{html.escape(t("Position of {name}", name=f["name"]))}" style="width:auto" onchange="this.form.submit()">{pos}</select></form>
<details class="opts"><summary class="btn">{html.escape(t("Options"))} ▾</summary>
<form method="post" action="/notetypes/{ntid}/fields" class="row" style="margin-top:8px">{hidden("rename")}
<input name="name" value="{nm}" aria-label="{html.escape(t("New name for {name}", name=f["name"]))}" style="width:200px" required><button>{html.escape(t("Rename"))}</button></form>
<form method="post" action="/notetypes/{ntid}/fields" style="margin-top:8px">{hidden("options")}
<div class="row"><div><label>{html.escape(t("Editing font"))}</label><input name="font" value="{html.escape(f.get('font', 'Arial'))}" style="width:180px"></div>
<div><label>{html.escape(t("Size"))}</label><input type="number" name="size" min="5" max="300" value="{int(f.get('size', 20) or 20)}" style="width:90px"></div></div>
<label>{html.escape(t("Description (shown in the empty field when adding)"))}</label><input name="description" value="{html.escape(f.get('description', ''))}">
<label class="chk"><input type="checkbox" name="sort" value="1"{" checked" if i == sort_idx else ""}>{html.escape(t("Sort by this field in the browser"))}</label>
<label class="chk"><input type="checkbox" name="rtl" value="1"{chk("rtl")}>{html.escape(t("Use right-to-left direction"))}</label>
<label class="chk"><input type="checkbox" name="sticky" value="1"{chk("sticky")}>{html.escape(t("Remember last input when adding"))}</label>
<label class="chk"><input type="checkbox" name="plainText" value="1"{chk("plainText")}>{html.escape(t("Use HTML editor by default"))}</label>
<label class="chk"><input type="checkbox" name="collapsed" value="1"{chk("collapsed")}>{html.escape(t("Collapse by default"))}</label>
<label class="chk"><input type="checkbox" name="excludeFromSearch" value="1"{chk("excludeFromSearch")}>{html.escape(t("Exclude from unqualified searches"))}</label>
<button class="primary">{html.escape(t("Save options"))}</button></form>
<form method="post" action="/notetypes/{ntid}/fields" style="margin-top:8px"
{confirm_attr(tn("Delete field {name} from {n} note?", "Delete field {name} from {n} notes?",
                 col.models.use_count(nt), name=f["name"]))}>
{hidden("delete")}<button class="danger">{html.escape(t("Delete field"))}</button></form></details></span></div>""")
    return f"""{CSS}<div class="box"><div class="row" style="justify-content:space-between">
<h2 style="margin:0">{html.escape(t("Fields for {name}", name=nt['name']))}</h2>
<span class="row"><a class="btn" href="/notetypes/{ntid}/cards">{html.escape(t("Cards"))}</a><a class="btn" href="/notetypes">{html.escape(t("All note types"))}</a></span></div>
{''.join(rows)}
<form method="post" action="/notetypes/{ntid}/fields" class="row" style="margin-top:12px">
<input type="hidden" name="op" value="add"><input name="name" placeholder="{html.escape(t("New field name"))}" aria-label="{html.escape(t("New field name"))}"
style="flex:1;min-width:200px" required><button class="primary">{html.escape(t("Add field"))}</button></form>
<p class="muted" style="font-size:14px;margin-bottom:0">{html.escape(t("Renaming a field also updates the card templates that use it."))}</p></div>"""


@router.get("/notetypes/{ntid}/fields", response_class=HTMLResponse)
def fields_page(request: Request, ntid: int):
    user = guard(request)
    if not user: return core.need_login(request)
    with core.collection(user) as col:
        try:
            body = fields_html(col, get_nt(col, ntid))
        except LookupError:
            return RedirectResponse("/notetypes", 303)
    return core.page(t("Fields"), body, user, "settings", core.take_flash(request))


def field_op(col, nt, form):
    op = str(form.get("op", ""))
    if op == "add":
        name = clean_field_name(nt, str(form.get("name", "")))
        col.models.add_field(nt, col.models.new_field(name))
        return t("Added field “{name}”.", name=name)
    try:
        f = nt["flds"][int(form.get("idx", -1))]
    except (ValueError, IndexError):
        raise ValueError(t("Unknown field."))
    if op == "rename":
        name = clean_field_name(nt, str(form.get("name", "")), skip=f)
        old = f["name"]
        col.models.rename_field(nt, f, name)
        return t("Renamed “{old}” to “{name}”.", old=old, name=name) if old != name else ""
    if op == "move":
        to = int(form.get("to", 0))
        if not 0 <= to < len(nt["flds"]):
            raise ValueError(t("Unknown position."))
        col.models.reposition_field(nt, f, to)
        return ""
    if op == "delete":
        if len(nt["flds"]) < 2:
            raise ValueError(t("A note type needs at least one field."))
        col.models.remove_field(nt, f)
        return t("Deleted field “{name}”.", name=f['name'])
    if op == "options":
        f["font"] = str(form.get("font", "")).strip() or "Arial"
        try:
            f["size"] = max(5, min(300, int(form.get("size", 20))))
        except ValueError:
            raise ValueError(t("Size must be a whole number."))
        f["description"] = str(form.get("description", ""))
        for key in ("rtl", "sticky", "plainText", "collapsed", "excludeFromSearch"):
            f[key] = bool(form.get(key))
        if form.get("sort"):
            col.models.set_sort_index(nt, nt["flds"].index(f))
        return t("Saved options for “{name}”.", name=f['name'])
    raise ValueError(t("Unknown action."))


@router.post("/notetypes/{ntid}/fields")
async def fields_save(request: Request, ntid: int):
    user = guard(request)
    if not user: return core.need_login(request)
    form = await request.form()
    with core.collection(user) as col:
        try:
            nt = get_nt(col, ntid)
            msg = field_op(col, nt, form)
            col.models.update_dict(nt)
        except LookupError:
            return RedirectResponse("/notetypes", 303)
        except Exception as e:
            msg = "!" + clean_err(e)
    if msg:
        core.set_flash(request, msg)
    return RedirectResponse(f"/notetypes/{ntid}/fields", 303)


# ---------- card templates ----------
def preview_note(col, nt, nid=0):
    """The note shown in the preview: the given one, else the first note of this type, else a
    blank note (whose empty fields are shown as "(Field)", as in Anki)."""
    if nid:
        try:
            note = col.get_note(nid)
            if note.mid == nt["id"]:
                return note, False
        except Exception:
            pass
    nids = col.models.nids(nt["id"])
    if nids:
        return col.get_note(nids[0]), False
    return col.new_note(nt), True


def cloze_numbers(note):
    nums = sorted({int(n) for f in note.fields for n in re.findall(r"\{\{c(\d+)::", f)})
    return nums or [1]


def preview_docs(col, nt, ord_, qfmt, afmt, css, nid=0, cloze=1):
    note, blank = preview_note(col, nt, nid)
    card = Card(col)
    card.did = 1
    if is_cloze(nt):
        card.ord, tmpl = max(0, cloze - 1), dict(nt["tmpls"][0])
    else:
        card.ord, tmpl = ord_, dict(nt["tmpls"][ord_])
    tmpl["qfmt"], tmpl["afmt"] = qfmt, afmt
    tmpl["ord"] = card.ord     # the engine picks the cloze number from the template's ord
    shown = dict(nt, css=css)
    out = TemplateRenderContext.from_card_layout(note, card, shown, tmpl, fill_empty=blank).render()
    play = '<span class="replay-button">' + core.PLAY_SVG + "</span>"
    def side(text, front):
        text = re.sub(r"\[anki:play:[qa]:\d+\]", play, text)
        box = ('<center><input type="text" id="typeans" placeholder="'
               + html.escape(t("(typing the answer)")) + '" disabled></center>')
        text = core.TYPE_PAT.sub(box if front else "", text)
        return core.card_doc(f"<style>{css}</style>{text}", core.theme_of(col))
    return side(out.question_text, True), side(out.answer_text, False)


def int_arg(v, default=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def cards_html(col, nt, ord_, nid=0, cloze=1, edits=None):
    ntid = nt["id"]
    cz = is_cloze(nt)
    tmpls = nt["tmpls"]
    ord_ = max(0, min(ord_, len(tmpls) - 1))
    tm = tmpls[ord_]
    qfmt, afmt, css = (edits or (tm["qfmt"], tm["afmt"], nt["css"]))
    q, a = preview_docs(col, nt, ord_, qfmt, afmt, css, nid, cloze)
    note, blank = preview_note(col, nt, nid)
    q_nid = f"&nid={note.id}" if nid and not blank else ""
    types = "".join(f'<a class="btn{" primary" if i == ord_ else ""}" href="/notetypes/{ntid}/cards?ord={i}{q_nid}">'
                    f'{i + 1}: {html.escape(x["name"])}</a>' for i, x in enumerate(tmpls))
    cloze_sel = ""
    if cz:
        nums = cloze_numbers(note)
        if cloze not in nums: nums = sorted(set(nums) | {cloze})
        opts = "".join(f'<option value="{n}"{" selected" if n == cloze else ""}>{html.escape(t("Cloze {n}", n=n))}</option>'
                       for n in nums)
        cloze_sel = (f'<label for="cloze">{html.escape(t("Preview card"))}</label><select id="cloze" name="cloze" style="width:auto">'
                     f'{opts}</select>')
    use = col.models.template_use_count(ntid, ord_) if not cz else 0
    pos = "".join(f'<option value="{j}"{" selected" if j == ord_ else ""}>{j + 1}</option>' for j in range(len(tmpls)))
    hid = f'<input type="hidden" name="ord" value="{ord_}">'
    if cz:
        type_ops = ('<p class="muted" style="font-size:14px">'
                    + html.escape(t("Cloze note types have one card type; each cloze number makes a card.")) + '</p>')
    else:
        type_ops = f"""<div class="row">
<form method="post" action="/notetypes/{ntid}/cards/op" class="row">{hid}<input type="hidden" name="op" value="rename">
<input name="name" value="{html.escape(tm['name'])}" aria-label="{html.escape(t("Card type name"))}" style="width:200px" required><button>{html.escape(t("Rename"))}</button></form>
<form method="post" action="/notetypes/{ntid}/cards/op" class="row">{hid}<input type="hidden" name="op" value="move">
<label for="to" style="margin:0">{html.escape(t("Position"))}</label><select id="to" name="to" style="width:auto" onchange="this.form.submit()">{pos}</select></form>
<form method="post" action="/notetypes/{ntid}/cards/op">{hid}<input type="hidden" name="op" value="add"><button>{html.escape(t("Add card type"))}</button></form>
<form method="post" action="/notetypes/{ntid}/cards/op" {confirm_attr(tn("Delete this card type and its {n} card?", "Delete this card type and its {n} cards?", use))}>
{hid}<input type="hidden" name="op" value="delete"><button class="danger">{html.escape(t("Delete card type"))}</button></form></div>"""
    if blank:
        who = html.escape(t("using a blank note"))
    else:
        who = html.escape(t("using the note “{text}”",
                            text=" ".join(core.strip_html(note.fields[nt["sortf"]]).split())[:40]))
    fields_list = html.escape(', '.join('{{' + f['name'] + '}}' for f in nt['flds']))
    specials = "{{FrontSide}}, {{Tags}}, {{Deck}}, {{Card}}, {{type:Field}}" + (", {{cloze:Field}}" if cz else "")
    # The lists go in after escaping (they hold braces and already-escaped names).
    fields_hint = (html.escape(t("Fields: {fields}.", fields="\x00"), quote=False).replace("\x00", fields_list) + "\n"
                   + html.escape(t("Also {specials}.", specials="\x00"), quote=False).replace("\x00", specials))
    return f"""{CSS}<style>main{{max-width:1240px}}</style>
<div class="box"><div class="row" style="justify-content:space-between"><h2 style="margin:0">{html.escape(t("Cards for {name}", name=nt['name']))}</h2>
<span class="row"><a class="btn" href="/notetypes/{ntid}/fields">{html.escape(t("Fields"))}</a><a class="btn" href="/notetypes">{html.escape(t("All note types"))}</a></span></div>
<div class="ctype" style="margin-top:12px">{types}</div><div style="margin-top:12px">{type_ops}</div></div>
<form id="tform" method="post" action="/notetypes/{ntid}/cards">{hid}<input type="hidden" name="nid" value="{note.id if not blank else 0}">
<div class="tgrid"><div class="box" style="margin:0">
<label for="qfmt">{html.escape(t("Front template"))}</label><textarea id="qfmt" name="qfmt" class="code" spellcheck="false" autocapitalize="off">{html.escape(qfmt)}</textarea>
<label for="afmt">{html.escape(t("Back template"))}</label><textarea id="afmt" name="afmt" class="code" spellcheck="false" autocapitalize="off">{html.escape(afmt)}</textarea>
<label for="css">{html.escape(t("Styling (shared by all card types)"))}</label><textarea id="css" name="css" class="code" spellcheck="false" autocapitalize="off">{html.escape(css)}</textarea>
<p class="row" style="margin-bottom:0"><button class="primary">{html.escape(t("Save"))}</button><a class="btn" href="/notetypes/{ntid}/cards?ord={ord_}{q_nid}">{html.escape(t("Discard changes"))}</a></p>
<p class="muted" style="font-size:14px;margin-bottom:0">{fields_hint}</p></div>
<div class="box" style="margin:0"><div class="row" style="justify-content:space-between"><b>{html.escape(t("Preview"))}</b>
<span class="muted" style="font-size:14px">{who}</span></div>{cloze_sel}
<label>{html.escape(t("Front"))}</label><iframe id="pq" class="pv" title="{html.escape(t("Front preview"))}" srcdoc="{html.escape(q)}"></iframe>
<label>{html.escape(t("Back"))}</label><iframe id="pa" class="pv" title="{html.escape(t("Back preview"))}" srcdoc="{html.escape(a)}"></iframe></div></div></form>
<script>
(function(){{const f=document.getElementById('tform');let tm=null,dirty=false;
function fit(fr){{try{{const d=fr.contentDocument;fr.style.height=Math.max(160,d.documentElement.scrollHeight+4)+'px';}}catch(e){{}}}}
['pq','pa'].forEach(i=>{{const fr=document.getElementById(i);fr.addEventListener('load',()=>fit(fr));fr.addEventListener('fc-resize',()=>fit(fr));fit(fr);}});
function upd(){{clearTimeout(tm);tm=setTimeout(async()=>{{try{{const r=await fetch('/notetypes/{ntid}/preview',
{{method:'POST',body:new FormData(f)}});if(!r.ok)return;const j=await r.json();
document.getElementById('pq').srcdoc=j.q;document.getElementById('pa').srcdoc=j.a;}}catch(e){{}}}},300);}}
f.addEventListener('input',e=>{{if(e.target.tagName==='TEXTAREA')dirty=true;upd();}});
f.addEventListener('change',e=>{{if(e.target.name==='cloze')upd();}});
f.addEventListener('submit',()=>{{dirty=false;}});
f.querySelectorAll('textarea').forEach(t=>t.addEventListener('keydown',e=>{{if(e.key==='Tab'&&!e.shiftKey){{e.preventDefault();
const s=t.selectionStart;t.setRangeText('    ',s,t.selectionEnd,'end');upd();dirty=true;}}
if((e.ctrlKey||e.metaKey)&&e.key==='s'){{e.preventDefault();dirty=false;f.submit();}}}}));
window.addEventListener('beforeunload',e=>{{if(dirty){{e.preventDefault();e.returnValue='';}}}});}})();
</script>"""


@router.get("/notetypes/{ntid}/cards", response_class=HTMLResponse)
def cards_page(request: Request, ntid: int, ord: int = 0, nid: int = 0, cloze: int = 1):
    user = guard(request)
    if not user: return core.need_login(request)
    with core.collection(user) as col:
        try:
            body = cards_html(col, get_nt(col, ntid), ord, nid, max(1, cloze))
        except LookupError:
            return RedirectResponse("/notetypes", 303)
    return core.page(t("Cards"), body, user, "settings", core.take_flash(request))


@router.post("/notetypes/{ntid}/preview")
async def cards_preview(request: Request, ntid: int):
    user = guard(request)
    if not user: return JSONResponse({"error": "login"}, 401)
    form = await request.form()
    with core.collection(user) as col:
        try:
            nt = get_nt(col, ntid)
        except LookupError:
            return JSONResponse({"error": "unknown note type"}, 404)
        ord_ = max(0, min(int_arg(form.get("ord")), len(nt["tmpls"]) - 1))
        q, a = preview_docs(col, nt, ord_, str(form.get("qfmt", "")), str(form.get("afmt", "")),
                            str(form.get("css", "")), int_arg(form.get("nid")), max(1, int_arg(form.get("cloze"), 1)))
    return JSONResponse({"q": q, "a": a})


@router.post("/notetypes/{ntid}/cards")
async def cards_save(request: Request, ntid: int):
    user = guard(request)
    if not user: return core.need_login(request)
    form = await request.form()
    ord_, nid = int_arg(form.get("ord")), int_arg(form.get("nid"))
    cloze = max(1, int_arg(form.get("cloze"), 1))
    edits = tuple(str(form.get(k, "")).replace("\r\n", "\n") for k in ("qfmt", "afmt", "css"))
    with core.collection(user) as col:
        try:
            nt = get_nt(col, ntid)
        except LookupError:
            return RedirectResponse("/notetypes", 303)
        ord_ = max(0, min(ord_, len(nt["tmpls"]) - 1))
        tm = nt["tmpls"][ord_]
        tm["qfmt"], tm["afmt"], nt["css"] = edits
        try:
            col.models.update_dict(nt)
        except Exception as e:
            # Keep what was typed so nothing is lost, as Anki keeps its dialog open.
            body = cards_html(col, get_nt(col, ntid), ord_, nid, cloze, edits=edits)
            return core.page(t("Cards"), body, user, "settings", "!" + clean_err(e))
    core.set_flash(request, t("Saved."))
    return RedirectResponse(f"/notetypes/{ntid}/cards?ord={ord_}" + (f"&nid={nid}" if nid else ""), 303)


def card_type_op(col, nt, form):
    if is_cloze(nt):
        raise ValueError(t("Cloze note types can't have more card types."))
    tmpls = nt["tmpls"]
    ord_ = int_arg(form.get("ord"))
    if not 0 <= ord_ < len(tmpls):
        raise ValueError(t("Unknown card type."))
    tm = tmpls[ord_]
    op = str(form.get("op", ""))
    if op == "add":
        # Like Anki: the new card type starts as a copy of the current one.
        names = {x["name"] for x in tmpls}
        n = len(tmpls) + 1
        while f"Card {n}" in names: n += 1
        new = col.models.new_template(f"Card {n}")
        # Anki validates when its dialog closes, so the user edits the copy first. Here each
        # action saves straight away and the engine refuses two identical fronts, so the copy
        # gets a comment naming it; edit the front to make it a real new card type.
        new["qfmt"] = tm["qfmt"].rstrip() + f"\n<!-- {new['name']}: edit this front -->"
        new["afmt"] = tm["afmt"]
        col.models.add_template(nt, new)
        return len(tmpls) - 1, t("Added card type “{name}”.", name=new['name'])
    if op == "rename":
        name = str(form.get("name", "")).strip()
        if not name: raise ValueError(t("Enter a name."))
        if any(x is not tm and x["name"] == name for x in tmpls):
            raise ValueError(t("That name is already used."))
        tm["name"] = name
        return ord_, ""
    if op == "move":
        to = int_arg(form.get("to"), -1)
        if not 0 <= to < len(tmpls): raise ValueError(t("Unknown position."))
        col.models.reposition_template(nt, tm, to)
        return to, ""
    if op == "delete":
        if len(tmpls) < 2:
            raise ValueError(t("At least one card type is required."))
        col.models.remove_template(nt, tm)
        return 0, t("Deleted card type “{name}”.", name=tm['name'])
    raise ValueError(t("Unknown action."))


@router.post("/notetypes/{ntid}/cards/op")
async def cards_op(request: Request, ntid: int):
    user = guard(request)
    if not user: return core.need_login(request)
    form = await request.form()
    ord_ = int_arg(form.get("ord"))
    with core.collection(user) as col:
        try:
            nt = get_nt(col, ntid)
            ord_, msg = card_type_op(col, nt, form)
            col.models.update_dict(nt)
        except LookupError:
            return RedirectResponse("/notetypes", 303)
        except Exception as e:
            msg = "!" + clean_err(e)
    if msg:
        core.set_flash(request, msg)
    return RedirectResponse(f"/notetypes/{ntid}/cards?ord={ord_}", 303)


# ---------- change note type ----------
def change_targets(col, nids_s, q):
    if q:
        return list(col.find_notes(q))
    return [int(x) for x in str(nids_s).split(",") if x.strip().isdigit()]


def change_html(col, nids, new_id, back, q):
    try:
        old_id = col.models.get_single_notetype_of_notes(nids)
    except Exception:
        raise ValueError(t("Please select notes from only one note type."))
    old = col.models.get(old_id)
    new = col.models.get(new_id) if new_id else old
    if not new:
        new = old
    info = col.models.change_notetype_info(old_notetype_id=old_id, new_notetype_id=new["id"])
    inp = info.input
    nt_opts = "".join(f'<option value="{m.id}"{" selected" if m.id == new["id"] else ""}>{html.escape(m.name)}</option>'
                      for m in sorted(col.models.all_names_and_ids(), key=lambda m: m.name.lower()))
    def mapping(prefix, new_names, old_names, current, nothing):
        rows = []
        for i, name in enumerate(new_names):
            opts = f'<option value="-1"{" selected" if current[i] < 0 else ""}>{nothing}</option>' + "".join(
                f'<option value="{j}"{" selected" if current[i] == j else ""}>{html.escape(o)}</option>'
                for j, o in enumerate(old_names))
            rows.append(f'<tr><td>{html.escape(name)}</td><td><select name="{prefix}{i}" '
                        f'aria-label="{html.escape(t("Old {name}", name=name))}">{opts}</select></td></tr>')
        return "".join(rows)
    nothing = html.escape(t("(Nothing)"))
    fields = mapping("f", info.new_field_names, info.old_field_names, list(inp.new_fields), nothing)
    if inp.is_cloze:
        tmpl_box = ('<p class="muted" style="font-size:14px">'
                    + html.escape(t("Cards are matched by cloze number when changing to or from a cloze note type."))
                    + '</p>')
    else:
        tmpl_box = (f'<table><tr><th>{html.escape(t("New card type"))}</th><th>{html.escape(t("Takes cards from"))}</th></tr>'
                    f'{mapping("t", info.new_template_names, info.old_template_names, list(inp.new_templates), nothing)}</table>')
    keep_q = f'<input type="hidden" name="q" value="{html.escape(q)}">' if q else \
        f'<input type="hidden" name="nids" value="{",".join(map(str, nids))}">'
    eb = html.escape(back)
    n = len(nids)
    of_type = html.escape(tn("{n} note of type {name}.", "{n} notes of type {name}.", n, name="\x00")).replace(
        "\x00", f"<b>{html.escape(old['name'])}</b>")
    ask = tn("Change the note type of {n} note? Fields or cards set to (Nothing) will be deleted.",
             "Change the note type of {n} notes? Fields or cards set to (Nothing) will be deleted.", n)
    return f"""<div class="box"><h2 style="margin-top:0">{html.escape(t("Change note type"))}</h2>
<p>{of_type}</p>
<form method="get" action="/notetypes/change">{keep_q}<input type="hidden" name="back" value="{eb}">
<label for="new">{html.escape(t("New note type"))}</label><select id="new" name="new" onchange="this.form.submit()">{nt_opts}</select>
<noscript><button>{html.escape(t("Choose"))}</button></noscript></form>
<form method="post" action="/notetypes/change" {confirm_attr(ask)}>
{keep_q}<input type="hidden" name="old" value="{old_id}"><input type="hidden" name="new" value="{new['id']}">
<input type="hidden" name="back" value="{eb}">
<h3>{html.escape(t("Fields"))}</h3><table><tr><th>{html.escape(t("New field"))}</th><th>{html.escape(t("Takes content from"))}</th></tr>{fields}</table>
<h3>{html.escape(t("Cards"))}</h3>{tmpl_box}
<p class="row" style="margin-bottom:0"><button class="primary">{html.escape(t("Change note type"))}</button><a class="btn" href="{eb}">{html.escape(t("Cancel"))}</a></p>
</form></div>"""


@router.get("/notetypes/change", response_class=HTMLResponse)
def change_page(request: Request, nids: str = "", q: str = "", new: int = 0, back: str = "/browse"):
    user = guard(request)
    if not user: return core.need_login(request)
    back = core.safe_back(back)
    with core.collection(user) as col:
        try:
            targets = change_targets(col, nids, q)
            if not targets:
                raise ValueError(t("Select some notes first."))
            body = change_html(col, targets, new, back, q)
        except Exception as e:
            core.set_flash(request, "!" + clean_err(e))
            return RedirectResponse(back, 303)
    return core.page(t("Change note type"), body, user, "browse", core.take_flash(request))


@router.post("/notetypes/change")
async def change_save(request: Request):
    user = guard(request)
    if not user: return core.need_login(request)
    form = await request.form()
    back = core.safe_back(str(form.get("back", "")))
    with core.collection(user) as col:
        try:
            nids = change_targets(col, form.get("nids", ""), str(form.get("q", "")))
            old_id, new_id = int_arg(form.get("old")), int_arg(form.get("new"))
            if not nids:
                raise ValueError(t("Select some notes first."))
            if col.models.get_single_notetype_of_notes(nids) != old_id or not col.models.get(new_id):
                raise ValueError(t("The notes changed meanwhile; please try again."))
            info = col.models.change_notetype_info(old_notetype_id=old_id, new_notetype_id=new_id)
            req = info.input
            n_old_f, n_old_t = len(info.old_field_names), len(info.old_template_names)
            def read(prefix, count, n_old, default):
                out = []
                for i in range(count):
                    v = int_arg(form.get(f"{prefix}{i}"), default[i])
                    out.append(v if 0 <= v < n_old else -1)
                return out
            fields = read("f", len(req.new_fields), n_old_f, list(req.new_fields))
            del req.new_fields[:]
            req.new_fields.extend(fields)
            if not req.is_cloze:
                tmpls = read("t", len(req.new_templates), n_old_t, list(req.new_templates))
                del req.new_templates[:]
                req.new_templates.extend(tmpls)
            req.note_ids.extend(nids)
            col.models.change_notetype_of_notes(req)
            core.set_flash(request, tn("Changed {n} note to “{name}”.", "Changed {n} notes to “{name}”.", len(nids),
                                       name=col.models.get(new_id)['name']))
        except Exception as e:
            core.set_flash(request, "!" + clean_err(e))
    return RedirectResponse(back, 303)
