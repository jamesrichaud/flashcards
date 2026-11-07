# Translating the interface

All user-visible text goes through `t()` / `tn()` from `flashcards/i18n/__init__.py`.
Language files: `en.json` (rebuilt by `python3 flashcards/i18n/extract.py`) plus
`es fr de pt it ga pl .json`. Keys are the English text itself.

## Rules for code

1. Import: `from flashcards.i18n import t, tn, N_` (app.py already has `t, tn`).
2. Wrap every user-visible English string: page titles, headings, labels, buttons, hints,
   placeholders, `title=` / `aria-label=` text, flash messages (keep the leading `"!"` for errors
   *outside* t(): `"!" + t("Wrong username or password.")`), error messages shown to people,
   confirm() texts and other strings used by page JavaScript.
3. The key must be ONE plain string literal (adjacent literals are fine). Never `t(f"...")`.
   Variable parts use `{name}` placeholders: `t("Added {n} notes to {deck}.", n=n, deck=name)`.
   Keep whole sentences together; don't build sentences out of translated fragments.
4. Plurals: `tn("{n} card", "{n} cards", n)` (Polish and Irish have more forms; this handles it).
   Replace little English pluralisers like `plural(n, "note")` with `tn(...)`.
5. Module-level constants (dicts/lists of labels built at import time) must NOT call t() — that
   would freeze English. Mark them with `N_("Again")` and translate where they are rendered:
   `t(label)`. (Using t() with a variable is fine only for values marked with N_.)
6. Escaping: t() returns plain text. A translation may contain `'`, `"` or `&`.
   Inside HTML attributes use double quotes and `html.escape(t(...))`. In text nodes escape too when
   the surrounding code would escape user text. For JavaScript use `core.js(t(...))` / `json.dumps`
   (gives a quoted JS string) — never paste t() output inside a hand-written '...' JS string.
7. Small HTML inside a sentence (`<b>`, `<a>`) — prefer putting the markup in a placeholder:
   `t("FSRS is {state}.", state=f"<b>{html.escape(t('on'))}</b>")` with escaped text around it, or
   leave the markup in the key if splitting would wreck the sentence (keep such keys rare).
8. ENGLISH OUTPUT MUST NOT CHANGE. Tests run in English and compare exact text. The key must be
   exactly the English that was there before (same punctuation, spacing, capitals).
9. Keep `col.tr.*` calls (the engine's translations) as they are — they already follow the language.
10. Do not translate: CSS, URLs, form field names/values, anything sent to the engine (search
    syntax like `deck:`, tag names like `missing-media`, config keys, note type/field names that
    are data), log/print output, machine protocols (AnkiConnect JSON results/errors), and user
    content (deck names, card text).
11. Watch out for local variables named `t` (`for t in tags`, `t = lambda ...`). Rename the local
    (e.g. `tag`, `tm`) in any function where you need `t()`.
