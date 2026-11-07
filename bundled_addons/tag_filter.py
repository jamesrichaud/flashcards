"""A tag tree on the deck overview: browse or study just the cards with one tag (a::b::c tags nest).

Like the desktop add-ons for hierarchical tags. Shows the tags used by the notes in the deck (and
its subdecks) as a tree with note counts. "Browse" opens those cards in Browse; "Study" opens the
Create filtered deck page with the deck and tag already filled in. A parent tag includes its
child tags, as Anki's tag: search does.
"""
import html
from urllib.parse import quote

from anki.collection import SearchNode
from anki.utils import ids2str

from flashcards.i18n import t, N_

NAME = "Hierarchical Tags Filter"   # plain literal: flashcards/addons.py reads it without importing
N_("Hierarchical Tags Filter")      # ...so mark it for translation here
N_('A tag tree on the deck overview: browse or study just the cards with one tag (a::b::c tags nest).')
MAX_TAGS = 400      # nodes shown; very large tag sets are cut (a note says so)


def tree(col, did):
    """{"name": str, "path": str, "n": notes, "kids": {...}} for the deck's tags, or None."""
    deck = col.decks.get(did, default=False)
    if not deck:
        return None, ""
    scope = col.build_search_string(SearchNode(deck=deck["name"]))
    nids = col.find_notes(scope)
    root = {"kids": {}}
    if not nids:
        return root, scope
    for tags in col.db.list(f"select tags from notes where id in {ids2str(nids)}"):
        seen = set()
        for tag in tags.split():
            parts = tag.split("::")
            node = root
            for i, part in enumerate(parts):
                key = part.lower()       # Anki's tags ignore case
                node = node["kids"].setdefault(key, {"name": part, "path": "::".join(parts[:i + 1]), "n": 0, "kids": {}})
                if node["path"].lower() not in seen:
                    seen.add(node["path"].lower())
                    node["n"] += 1
    return root, scope


def render(col, node, scope, depth, budget):
    out = []
    e = lambda x: html.escape(x, quote=False)
    for key in sorted(node["kids"], key=str.lower):
        if budget[0] <= 0:
            break
        budget[0] -= 1
        kid = node["kids"][key]
        q = col.build_search_string(scope, SearchNode(tag=kid["path"]))
        links = (f'<a class="btn" href="/browse?q={quote(q, safe="")}">{e(t("Browse"))}</a>'
                 f'<a class="btn" href="/filtered/new?search={quote(q, safe="")}">{e(t("Study"))}</a>')
        label = f'<span class="tf-name">{e(kid["name"])}</span> <span class="muted">{kid["n"]}</span>'
        if kid["kids"]:
            inner = render(col, kid, scope, depth + 1, budget)
            out.append(f'<details class="tf-node"{" open" if depth == 0 and len(node["kids"]) <= 8 else ""}>'
                       f'<summary class="tf-row">{label}<span class="tf-acts">{links}</span></summary>'
                       f'<div class="tf-kids">{inner}</div></details>')
        else:
            out.append(f'<div class="tf-row tf-leaf">{label}<span class="tf-acts">{links}</span></div>')
    return "".join(out)


def deck_overview(col, did):
    root, scope = tree(col, did)
    if root is None:
        return ""
    e = lambda x: html.escape(x, quote=False)
    title = f'<h3 style="margin-top:0">{e(t("Tags in this deck"))}</h3>'
    if not root["kids"]:
        return f'<div class="box tf">{title}<p class="muted" style="margin:0">{e(t("No tags in this deck."))}</p></div>'
    budget = [MAX_TAGS]
    body = render(col, root, scope, 0, budget)
    cut = (f'<p class="muted">{e(t("Only the first {n} tags are shown.", n=MAX_TAGS))}</p>' if budget[0] <= 0 else "")
    return f"""<style>.tf-row{{display:flex;align-items:center;gap:8px;min-height:48px;padding:2px 4px;border-radius:10px;
cursor:pointer;flex-wrap:wrap}}.tf-row:hover{{background:var(--bg)}}.tf-leaf{{cursor:default;padding-left:26px}}
.tf-name{{flex:1;min-width:120px;overflow-wrap:anywhere}}.tf-acts{{display:flex;gap:6px}}.tf-acts .btn{{padding:8px 12px}}
.tf-kids{{margin-left:22px;border-left:1px solid var(--line);padding-left:6px}}
details.tf-node>summary{{list-style:none}}details.tf-node>summary::-webkit-details-marker{{display:none}}
details.tf-node>summary:before{{content:"▸";width:18px;color:var(--muted);flex:none}}
details.tf-node[open]>summary:before{{content:"▾"}}</style>
<div class="box tf">{title}<p class="muted" style="margin-top:0">{e(t("Number of notes with each tag. Study makes a filtered deck from the tag."))}</p>
{body}{cut}</div>"""
