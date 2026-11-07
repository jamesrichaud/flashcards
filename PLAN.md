# Build plan

Work top to bottom. One unchecked item per run. Tick `- [x]` when done and tested.
If an item fails twice, mark it `- [~]` with a one-line reason under it and move on.

## Core study features
- [x] Card actions in the study screen and browser: suspend/unsuspend, bury (card and note), flag (red/orange/green/blue/pink/turquoise/purple, keys Ctrl+1..7 on study screen), mark note (tag `marked`), reset to new, set due date. Show flag colour and suspended state in Browse. Keyboard: `-` bury, `@` suspend, `*` mark, as in Anki.
- [x] Card info page (`/card/{id}/info`): added date, first review, latest review, interval, due, reviews, lapses, ease or FSRS stability/difficulty/retrievability, average time, total time, deck, note type, full review history table. Link from study screen and browser.
- [x] Browse upgrade: switch between notes and cards mode, column for due date, interval, ease, reviews, lapses; sort by column; select many (checkboxes) and apply: change deck, add/remove tags, suspend, delete, reset, reposition new cards. Find and replace in fields.
- [x] Study screen parity: "Again" re-shows in the same session as in Anki; Edit returns to the same card; type-in-the-answer cards ({{type:Field}}) with an input box and Anki's comparison output; play/replay audio (R key); auto-play audio as Anki does; show "card is a leech" notice when Anki tags a leech.
- [x] Deck options parity: all main Anki deck option fields (learning steps, relearning steps, graduating interval, easy interval, insertion order, new/review order and mix, bury siblings options, leech threshold and action, maximum interval, starting ease, easy bonus, hard interval, FSRS desired retention, FSRS parameters + "Optimize" button using the engine's FSRS optimizer). Presets: create, rename, clone, delete, choose preset per deck. Apply to subdecks.
- [x] Custom study (Anki's dialog): increase today's new/review limit, review forgotten cards, review ahead, preview new cards, study by card state or tag. Uses the engine's custom study call.
- [x] Filtered decks: create/edit/rebuild/empty with search, limit, order, reschedule option, second filter.

## Content
- [x] Note type manager: list, add (from stock types: Basic, Basic+reversed, Basic optional reversed, Basic type-in, Cloze, Image Occlusion), rename, delete, field manager (add, rename, reorder, delete, sort field, font), card template editor (front/back HTML, styling CSS, add/remove card types) with live preview. Change note type of existing notes.
- [ ] Rich editor for Add/Edit: bold/italic/underline, sub/superscript, colour, lists, clear formatting, cloze buttons (Ctrl+Shift+C new number, Ctrl+Shift+Alt+C same number), paste images, record/attach audio, MathJax (\( \) and \[ \]) rendering in cards, sticky fields, duplicate-first-field warning, preview button.
- [ ] Image occlusion: create from an uploaded image, draw rectangles (and ellipses) to hide, "hide all guess one" and "hide one guess one", using Anki's image occlusion note type so cards are compatible with real Anki. Edit masks later.
- [ ] Import: CSV/TSV/TXT (choose delimiter, note type, deck, field mapping, HTML on/off, duplicate handling, tags), .colpkg (whole collection, with a clear "this replaces everything" confirm), .apkg options (update existing notes / scheduling). Export: whole collection .colpkg, notes as text/CSV.
- [ ] Media: check media (unused/missing files), delete unused, media served for audio/video/images in cards.

## Stats and polish
- [ ] Statistics page parity with Anki's graphs: future due, calendar heatmap, reviews (count and time), card counts pie, intervals, ease/difficulty, stability, retrievability, hourly breakdown, answer buttons, added, true retention table. Deck or whole-collection scope, time range 1 month / 3 months / 1 year / all. Use the engine's graphs/stats data.
- [ ] Deck list parity: collapse/expand subdecks, drag-free "move deck" control, deck description shown on overview (editable, markdown off), daily "today: studied N cards in M minutes" line under the deck list, shared decks link to ankiweb.net/shared.
- [ ] Undo/redo everywhere via the engine (Ctrl+Z), with a toast showing what was undone.
- [ ] Preferences: next day starts at, learn ahead limit, timebox time limit, show remaining count, show next review time above buttons, theme (system/light/dark), language left as English.
- [ ] Check database (engine `check_database`), empty cards report and delete, backups: automatic daily backup of each user's collection to `data/users/<name>/backups/` keeping 30, and restore from the settings page.
- [ ] Sync across devices: since every device uses the web app, confirm two browsers logged in as the same user always see the same state; add AnkiWeb-compatible sync server option documented in README (Anki's built-in `anki.syncserver`) so real Anki apps (AnkiDroid, desktop) can also sync to this server. Test with a second collection syncing via the engine's sync client.
- [ ] iPad and Chromebook polish: touch targets ≥44px, tap left/right halves of card for answers optional setting, swipe-safe, works in Safari "Add to Home Screen" (manifest + apple-touch-icon PNG), offline-safe error pages.
- [ ] Add-ons: a simple server-side add-on system (Python files in `data/addons/` with hooks: card render filter, after answer, extra page routes) and an Add-ons page to enable/disable. Document that desktop Anki add-ons do not run here.

## Final
- [ ] Full regression: run all tests, take Playwright screenshots of every page at iPad (820x1180) and Chromebook (1366x768) sizes into `shots/`, fix anything broken or ugly, update README feature list.
