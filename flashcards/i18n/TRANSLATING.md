# Writing a language file

`en.json` lists every interface string (made by `python3 flashcards/i18n/extract.py`).
Each language file (`es.json`, `fr.json`, ...) has exactly the same keys:

- Plain entries: `"English text": "translation"`.
- Plural entries (the value in en.json is a list `[singular, plural]`): the translation is a list of
  forms in this order:
  - es, fr, de, pt, it: `[one, other]` (French uses "one" for 0 and 1)
  - pl: `[one (1), few (2-4, 22-24, ...), many (0, 5-21, 25-31, ...)]`
  - ga: `[one (1), two (2), few (3-6), many (7-10), other (0, 11+)]`
  Every form must be non-empty. Use `{n}` and the same other placeholders as English.
- Keep every `{placeholder}` exactly (same names, none added or dropped). Keep HTML tags, `&nbsp;`,
  `Ctrl+Z`-style key names, `…`, emoji, leading/trailing spaces and punctuation like `:` that the
  English has.
- Keep product names as they are: Anki, AnkiWeb, AnkiDroid, AnkiMobile, AnkiConnect, FSRS, SM-2,
  LaTeX, MathJax, Web Push, iPad, Chromebook, CSV.
- Match desktop Anki's own wording in that language where the term exists there (deck, note, card,
  note type, field, cloze, filtered deck, bury, suspend, flag, learn ahead, Again/Hard/Good/Easy...).
  Anki's translations: https://github.com/ankitects/anki-core-i18n (ftl files) — e.g. Spanish
  "mazo", French "paquet", German "Stapel", Portuguese (Portugal) "baralho", Italian "mazzo",
  Irish "paca", Polish "talia". Portuguese is European Portuguese (pt_PT).
- Short, plain, friendly UI language; button labels stay short (the screen is an iPad).
