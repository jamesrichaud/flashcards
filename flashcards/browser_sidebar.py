"""Browse sidebar, like the one in Anki's browser.

A collapsible tree beside the card list: Saved searches, Today, Flags, Card state, Decks,
Note types (with their card types) and Tags (a tree split on ::). Tapping an item runs its
search; Ctrl/Cmd-click adds it to the current search with AND, Shift-click with OR, as in
desktop Anki. On narrow screens (iPad portrait) it is a slide-out panel.

Every search string comes from the engine (`col.build_search_string` with `SearchNode`s),
so names with spaces, quotes or wildcards are escaped exactly as desktop Anki does it.
Saved searches live in the collection config under Anki's own key, `savedFilters`, so they
sync with desktop Anki and AnkiDroid. Open/closed state of decks and tags is stored in the
engine (the deck's browser-collapsed flag and the tag's collapsed flag, as desktop Anki);
the sections and note types remember it in the browser (localStorage).
"""
import html, json, re
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse

from anki.collection import SearchNode
from anki.decks import DeckCollapseScope

import app as core
from flashcards.i18n import t, tn, N_

router = APIRouter()
SAVED_KEY = "savedFilters"   # Anki's config key: {name: search}
MAX_NAME = 100

# Labels are marked with N_ and translated where they are shown.
TODAY = [(N_("Added today"), SearchNode(added_in_days=1)),
         (N_("Edited today"), SearchNode(edited_in_days=1)),
         (N_("Studied today"), SearchNode(rated=SearchNode.Rated(days=1))),
         (N_("Again today"), SearchNode(rated=SearchNode.Rated(days=1, rating=SearchNode.RATING_AGAIN)))]
STATES = [(N_("New"), SearchNode.CARD_STATE_NEW), (N_("Learning"), SearchNode.CARD_STATE_LEARN),
          (N_("Review"), SearchNode.CARD_STATE_REVIEW), (N_("Suspended"), SearchNode.CARD_STATE_SUSPENDED),
          (N_("Buried"), SearchNode.CARD_STATE_BURIED)]


def esc(s):
    """Escape translated text for an HTML text node (quotes left alone, so English output is unchanged)."""
    return html.escape(s, quote=False)
# Anki's flag number (card.flags) -> SearchNode flag value (the enum is shifted by FLAG_NONE/FLAG_ANY).
FLAG_NODE = {0: SearchNode.FLAG_NONE, 1: SearchNode.FLAG_RED, 2: SearchNode.FLAG_ORANGE, 3: SearchNode.FLAG_GREEN,
             4: SearchNode.FLAG_BLUE, 5: SearchNode.FLAG_PINK, 6: SearchNode.FLAG_TURQUOISE, 7: SearchNode.FLAG_PURPLE}


def saved_searches(col):
    d = col.get_config(SAVED_KEY, {})
    return {str(k): str(v) for k, v in d.items()} if isinstance(d, dict) else {}


def normalize(col, q):
    """The engine's normal form of a search, for comparing; the text itself if it doesn't parse."""
    try:
        return col.build_search_string(q)
    except Exception:  # noqa: BLE001
        return q


def joined(col, q, add, how):
    """The current search with another one ANDed/ORed on (Ctrl/Shift-click), via the engine."""
    if not q.strip():
        return col.build_search_string(add)
    return col.join_searches(SearchNode(parsable_text=q), SearchNode(parsable_text=add),
                             "OR" if how == "or" else "AND")


def tag_search(col, tag):
    return col.build_search_string(SearchNode(tag=tag))


# ---------- tree html ----------
class Tree:
    def __init__(self, col, q):
        self.col, self.cur = col, normalize(col, q)

    def item(self, label, search, *, kids="", open_=True, toggle="", menu="", icon="", cls=""):
        """One row (with optional children). toggle: data attributes for the open/closed button."""
        on = search is not None and search == self.cur
        if search is None:
            link = f'<span class="sbl">{icon}{label}</span>'
        else:
            cur = ' aria-current="page"' if on else ""
            link = (f'<a class="sbl" href="{html.escape(core.browse_url(search))}" data-s="{html.escape(search)}"'
                    f'{cur}>{icon}{label}</a>')
        if kids:
            btn = (f'<button type="button" class="sbt" {toggle} aria-expanded="{"true" if open_ else "false"}" '
                   f'aria-label="{html.escape(t("Show or hide"))}">▾</button>')
        else:
            btn = '<span class="sbt"></span>'
        row = f'<div class="sbrow{" on" if on else ""}">{btn}{link}{menu}</div>'
        kid = f'<div class="sbkids">{kids}</div>' if kids else ""
        return f'<div class="sbn{" shut" if not open_ else ""}{(" " + cls) if cls else ""}">{row}{kid}</div>'

    def section(self, key, title, inner):
        return self.item(f"<b>{html.escape(title)}</b>", None, kids=inner or f'<p class="sbempty">{esc(t("None"))}</p>',
                         toggle=f'data-local="sec:{key}"', cls="sbsec")

    def search(self, *nodes):
        return self.col.build_search_string(*nodes)


def menu_btn(kind, name):
    return (f'<button type="button" class="sbm" data-kind="{kind}" data-name="{html.escape(name)}" '
            f'aria-label="{html.escape(t("More for {name}", name=name))}">⋯</button>')


def sidebar_html(col, q):
    tree = Tree(col, q)
    esc = html.escape
    # Saved searches
    saved = sorted(saved_searches(col).items(), key=lambda kv: kv[0].lower())
    inner = "".join(tree.item(esc(name), normalize(col, s), menu=menu_btn("saved", name)) for name, s in saved)
    inner += (f'<button type="button" class="sbact" data-op="save">＋ {esc(t("Save current search"), quote=False)}</button>'
              if q.strip() else f'<p class="sbempty">{esc(t("Search for something, then save it here."), quote=False)}</p>')
    parts = [tree.section("saved", t("Saved searches"), inner)]
    # Today
    parts.append(tree.section("today", t("Today"), "".join(tree.item(esc(t(n), quote=False), tree.search(s))
                                                           for n, s in TODAY)))
    # Flags (custom names from Anki's Rename Flag, if any)
    labels = col.get_config("flagLabels", {}) or {}
    flags = "".join(tree.item(esc(str(labels.get(str(n)) or t(name))) if n else esc(t("No flag"), quote=False),
                              tree.search(SearchNode(flag=FLAG_NODE[n])), icon=core.flag_dot(n) + " " if n else "")
                    for n, name, _ in core.FLAGS[1:] + core.FLAGS[:1])
    parts.append(tree.section("flags", t("Flags"), flags))
    # Card state
    parts.append(tree.section("state", t("Card state"),
                              "".join(tree.item(esc(t(n), quote=False), tree.search(SearchNode(card_state=s)))
                                      for n, s in STATES)))

    # Decks (engine tree; its collapsed flag is the browser one)
    def decks(node, prefix):
        out = []
        for ch in node.children:
            full = f"{prefix}::{ch.name}" if prefix else ch.name
            out.append(tree.item(esc(ch.name), tree.search(SearchNode(deck=full)), kids=decks(ch, full),
                                 open_=not ch.collapsed, toggle=f'data-deck="{ch.deck_id}"'))
        return "".join(out)
    parts.append(tree.section("decks", t("Decks"), decks(col.decks.deck_tree(), "")))
    # Note types and their card types
    nts = []
    for nt in sorted(col.models.all_names_and_ids(), key=lambda n: n.name.lower()):
        m = col.models.get(nt.id)
        if not m:
            continue
        kids = "".join(tree.item(esc(tm["name"]), tree.search(SearchNode(note=nt.name), SearchNode(template=tm["ord"])))
                       for tm in m["tmpls"])
        nts.append(tree.item(esc(nt.name), tree.search(SearchNode(note=nt.name)), kids=kids, open_=False,
                             toggle=f'data-local="nt:{nt.id}"'))
    parts.append(tree.section("notetypes", t("Note types"), "".join(nts)))

    # Tags (engine tree on ::, collapsed flags from the engine)
    def tags(node, prefix):
        out = []
        for ch in node.children:
            full = f"{prefix}::{ch.name}" if prefix else ch.name
            out.append(tree.item(esc(ch.name), tag_search(col, full), kids=tags(ch, full), open_=not ch.collapsed,
                                 toggle=f'data-tag="{esc(full)}"', menu=menu_btn("tag", full)))
        return "".join(out)
    inner = tags(col.tags.tree(), "")
    inner += f'<button type="button" class="sbact" data-op="clear_tags">{esc(t("Clear unused tags"), quote=False)}</button>'
    parts.append(tree.section("tags", t("Tags"), inner))
    # Words used by the script (prompts and confirmations), read from data-l.
    words = {"saveName": t("Name for this saved search:"),
             "clearTags": t("Remove tags that no note uses any more?"),
             "renameTag": t("Rename tag (use :: for a parent tag):"),
             "renameSaved": t("Rename saved search:"),
             "deleteTag": t("Remove the tag “{name}” (and the tags under it) from every note?"),
             "deleteSaved": t("Delete the saved search “{name}”?")}
    return f"""<aside class="sb" aria-label="{esc(t("Sidebar"))}" data-l="{esc(json.dumps(words))}"><div class="sbhead"><b>{esc(t("Sidebar"), quote=False)}</b>
<button type="button" class="sbclose" aria-label="{esc(t("Close sidebar"))}">✕</button></div>{''.join(parts)}
<p class="sbhint muted">{esc(t("Ctrl-click: add to the search (AND). Shift-click: OR."), quote=False)}</p></aside>
<div class="sbback"></div><div class="sbpop" hidden><button type="button" data-do="rename">{esc(t("Rename…"), quote=False)}</button>
<button type="button" data-do="delete" class="danger">{esc(t("Delete…"), quote=False)}</button></div>
<form class="sbform" method="post" action="/browse/sidebar" style="display:none"><input type="hidden" name="op">
<input type="hidden" name="name"><input type="hidden" name="new"><input type="hidden" name="q" value="{esc(q)}"></form>"""


CSS = """main{max-width:1560px}
.bwrap{display:flex;gap:16px;align-items:flex-start}.bmain{flex:1;min-width:0}
.sb{flex:none;width:280px;position:sticky;top:calc(75px + env(safe-area-inset-top));max-height:calc(100vh - 90px);
overflow:auto;overscroll-behavior:contain;background:var(--card);border:1px solid var(--line);border-radius:14px;padding:6px 6px 10px;font-size:15px}
.sbhead{display:none;align-items:center;justify-content:space-between;padding:4px 4px 4px 10px}
.sbrow{display:flex;align-items:center;min-height:44px;border-radius:10px}.sbrow.on{background:var(--bg)}
.sbrow.on .sbl{color:var(--accent);font-weight:600}
.sbt{flex:none;width:36px;min-height:44px;padding:0;border:0;background:none;color:var(--muted);font-size:14px;
display:inline-flex;align-items:center;justify-content:center;transition:transform .15s}
.sbn.shut>.sbrow>.sbt{transform:rotate(-90deg)}.sbn.shut>.sbkids{display:none}
.sbkids{padding-left:12px}.sbsec>.sbkids{padding-left:6px}.sbsec{margin-top:2px}
.sbl{flex:1;min-width:0;display:flex;align-items:center;min-height:44px;padding:0 6px;color:var(--ink);
text-decoration:none;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
a.sbl:hover{text-decoration:underline}.sbl>span{margin-right:6px}
.sbm{flex:none;min-width:44px;min-height:44px;padding:0;border:0;background:none;color:var(--muted);font-size:18px}
.sbm:hover,.sbt:hover{background:var(--bg)}
.sbact{display:block;width:calc(100% - 12px);margin:4px 6px;font-size:15px;padding:8px 12px;text-align:left}
.sbempty{margin:6px 12px;color:var(--muted);font-size:14px}.sbhint{font-size:13px;margin:10px 10px 0}
.sbpop{position:fixed;z-index:40;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:6px;
box-shadow:0 6px 24px rgba(0,0,0,.18);display:flex;flex-direction:column;min-width:160px}
.sbpop[hidden]{display:none}.sbpop button{border:0;text-align:left;background:none}.sbpop button:hover{background:var(--bg)}
.sbopen{display:none}.sbback{display:none}
@media (max-width:1000px){
.sb{position:fixed;z-index:30;top:0;left:0;bottom:0;width:min(330px,88vw);max-height:none;border-radius:0 14px 14px 0;
padding-top:calc(6px + env(safe-area-inset-top));padding-left:calc(6px + env(safe-area-inset-left));
transform:translateX(-105%);transition:transform .2s;visibility:hidden}
.sb.open{transform:none;visibility:visible}.sbhead{display:flex}.sbclose{border:0;background:none;min-width:44px}
.sbopen{display:inline-block}.sbback.open{display:block;position:fixed;inset:0;z-index:29;background:rgba(0,0,0,.35)}
.sbhint{display:none}}
"""

JS = """<script>(function(){
const sb=document.querySelector('.sb'),back=document.querySelector('.sbback'),pop=document.querySelector('.sbpop'),
 form=document.querySelector('.sbform'),F=n=>form.elements[n],Q=F('q').value,L=JSON.parse(sb.dataset.l||'{}');
const store={get:k=>{try{return localStorage.getItem('fcSb:'+k)}catch(e){return null}},
 set:(k,v)=>{try{localStorage.setItem('fcSb:'+k,v)}catch(e){}}};
// sections and note types: open/closed remembered in this browser
sb.querySelectorAll('.sbt[data-local]').forEach(b=>{const v=store.get(b.dataset.local);
 if(v!==null){const n=b.closest('.sbn');n.classList.toggle('shut',v==='0');b.setAttribute('aria-expanded',v==='0'?'false':'true');}});
try{const y=sessionStorage.getItem('fcSbY');if(y)sb.scrollTop=+y;}catch(e){}
function setOpen(o){sb.classList.toggle('open',o);back.classList.toggle('open',o);if(!o)pop.hidden=true;}
document.querySelectorAll('.sbopen').forEach(b=>b.addEventListener('click',()=>setOpen(true)));
back.addEventListener('click',()=>setOpen(false));sb.querySelector('.sbclose').addEventListener('click',()=>setOpen(false));
document.addEventListener('keydown',e=>{if(e.key==='Escape'){pop.hidden=true;setOpen(false);}});
function post(op,name,nw){F('op').value=op;F('name').value=name||'';F('new').value=nw||'';form.submit();}
async function remember(b,open){const fd=new FormData();fd.append('op','collapse');fd.append('collapsed',open?'0':'1');
 if(b.dataset.deck)fd.append('deck',b.dataset.deck);else fd.append('tag',b.dataset.tag);
 try{const r=await(await fetch('/browse/sidebar',{method:'POST',body:fd})).json();
  if(r.stamp&&window.fcStampSeen)fcStampSeen(r.stamp);}catch(e){}}
let menuFor=null;
sb.addEventListener('click',e=>{
 const t=e.target.closest('.sbt[aria-expanded]');
 if(t){const n=t.closest('.sbn'),open=n.classList.toggle('shut')===false;t.setAttribute('aria-expanded',open);
  if(t.dataset.local)store.set(t.dataset.local,open?'1':'0');else remember(t,open);return;}
 const a=e.target.closest('a.sbl');
 if(a){try{sessionStorage.setItem('fcSbY',sb.scrollTop)}catch(err){}
  const how=(e.ctrlKey||e.metaKey)?'and':e.shiftKey?'or':'';
  if(how&&Q.trim()){e.preventDefault();location.href='/browse?q='+encodeURIComponent(Q)+'&add='+encodeURIComponent(a.dataset.s)+'&join='+how;}
  return;}
 const m=e.target.closest('.sbm');
 if(m){e.stopPropagation();if(menuFor===m&&!pop.hidden){pop.hidden=true;return;}menuFor=m;pop.hidden=false;
  const r=m.getBoundingClientRect();pop.style.top=Math.min(r.bottom,innerHeight-pop.offsetHeight-8)+'px';
  pop.style.left=Math.max(8,Math.min(r.right-pop.offsetWidth,innerWidth-pop.offsetWidth-8))+'px';return;}
 const act=e.target.closest('.sbact');
 if(act){if(act.dataset.op==='save'){const n=prompt(L.saveName,Q);if(n&&n.trim())post('save',n.trim());}
  else if(confirm(L.clearTags))post('clear_tags');}
});
sb.addEventListener('scroll',()=>{pop.hidden=true;},{passive:true});
document.addEventListener('click',e=>{if(!pop.hidden&&!pop.contains(e.target))pop.hidden=true;});
pop.addEventListener('click',e=>{const b=e.target.closest('button');if(!b||!menuFor)return;pop.hidden=true;
 const kind=menuFor.dataset.kind,name=menuFor.dataset.name;
 if(b.dataset.do==='rename'){const n=prompt(kind==='tag'?L.renameTag:L.renameSaved,name);
  if(n&&n.trim()&&n.trim()!==name)post(kind==='tag'?'rename_tag':'rename_saved',name,n.trim());}
 else if(confirm((kind==='tag'?L.deleteTag:L.deleteSaved).split('{name}').join(name)))
  post(kind==='tag'?'delete_tag':'delete_saved',name);});
})();</script>"""


def layout(side, main_html):
    """The Browse page body with the sidebar (from sidebar_html) beside it."""
    return f'<style>{CSS}</style><div class="bwrap">{side}<div class="bmain">{main_html}</div></div>{JS}'


def open_button():
    """The ☰ Sidebar button for narrow screens (in the current language)."""
    return (f'<button type="button" class="sbopen" aria-label="{html.escape(t("Open the sidebar"))}">'
            f'☰ {esc(t("Sidebar"))}</button>')


def __getattr__(name):
    # `OPEN_BUTTON` was a constant; it is now built per request so it follows the language.
    if name == "OPEN_BUTTON":
        return open_button()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# ---------- actions ----------
def run(col, form):
    """Do one sidebar action; returns (flash message, new search or None to keep the old one)."""
    op = str(form.get("op", ""))
    name = str(form.get("name", "")).strip()
    new = str(form.get("new", "")).strip()
    q = str(form.get("q", ""))
    if op == "save":
        if not q.strip(): raise ValueError(t("Search for something first, then save it."))
        if not name or len(name) > MAX_NAME:
            raise ValueError(t("Give the search a name (up to {n} characters).", n=MAX_NAME))
        search = col.build_search_string(q)   # raises on a search the engine can't read
        saved = saved_searches(col)
        replaced = name in saved
        saved[name] = search
        col.set_config(SAVED_KEY, saved)
        if replaced:
            return (t("Saved search “{name}” updated.", name=name), None)
        return (t("Saved search “{name}” added.", name=name), None)
    if op == "rename_saved":
        saved = saved_searches(col)
        if name not in saved: raise ValueError(t("That saved search no longer exists."))
        if not new or len(new) > MAX_NAME:
            raise ValueError(t("Enter a new name (up to {n} characters).", n=MAX_NAME))
        if new in saved and new != name:
            raise ValueError(t("There is already a saved search called “{name}”.", name=new))
        saved = {(new if k == name else k): v for k, v in saved.items()}
        col.set_config(SAVED_KEY, saved)
        return (t("Renamed to “{name}”.", name=new), None)
    if op == "delete_saved":
        saved = saved_searches(col)
        if saved.pop(name, None) is None: raise ValueError(t("That saved search no longer exists."))
        col.set_config(SAVED_KEY, saved)
        return (t("Saved search “{name}” deleted.", name=name), None)
    if op == "rename_tag":
        new = new.strip(":")
        if not name: raise ValueError(t("No tag chosen."))
        if not new or any(c.isspace() for c in new):
            raise ValueError(t("Tag names can't be empty or contain spaces."))
        n = col.tags.rename(name, new).count
        after = tag_search(col, new) if normalize(col, q) == tag_search(col, name) else None
        return (tn("Tag “{old}” renamed to “{new}” on {n} note.", "Tag “{old}” renamed to “{new}” on {n} notes.",
                   n, old=name, new=new), after)
    if op == "delete_tag":
        if not name: raise ValueError(t("No tag chosen."))
        n = col.tags.remove(name).count
        after = "" if normalize(col, q) == tag_search(col, name) else None
        return (tn("Tag “{name}” removed from {n} note.", "Tag “{name}” removed from {n} notes.", n, name=name), after)
    if op == "clear_tags":
        n = col.tags.clear_unused_tags().count
        return (tn("{n} unused tag cleared.", "{n} unused tags cleared.", n) if n else t("No unused tags."), None)
    raise ValueError(t("Unknown sidebar action."))


@router.post("/browse/sidebar")
async def sidebar_action(request: Request):
    user = core.current_user(request)
    form = await request.form()
    if form.get("op") == "collapse":
        if not user: return JSONResponse({"ok": False}, 401)
        collapsed = form.get("collapsed") == "1"
        with core.collection(user) as col:
            try:
                if form.get("deck"):
                    did = int(str(form.get("deck")))
                    if not col.decks.get(did, default=False): raise ValueError
                    col.decks.set_collapsed(did, collapsed, DeckCollapseScope.BROWSER)
                elif form.get("tag"):
                    col.tags.set_collapsed(str(form.get("tag")), collapsed)
                else:
                    raise ValueError
            except Exception:  # noqa: BLE001
                return JSONResponse({"ok": False}, 400)
        return {"ok": True, "stamp": core.sync_mod.stamp(user)}
    if not user: return core.need_login(request)
    q = str(form.get("q", ""))
    with core.collection(user) as col:
        try:
            msg, after = run(col, form)
            if after is not None: q = after
        except Exception as e:  # noqa: BLE001
            msg = "!" + (re.sub(r"[\u2068\u2069]", "", str(e)) or t("That did not work."))
    core.set_flash(request, msg)
    return RedirectResponse(core.browse_url(q), 303)
