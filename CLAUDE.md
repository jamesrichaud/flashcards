# Flashcards: rules for unattended work

This app is a web front-end over Anki's real engine (the `anki` Python package, imported as
`from anki.collection import Collection`). Goal: match desktop Anki's features in the browser,
for use on an iPad and a Chromebook. Licence AGPL-3.0.

## You are running unattended overnight
- Never ask the user questions. Nobody is watching. Make the most reasonable choice, write it
  down in `NOTES.md` under the feature's heading, and continue.
- Do exactly ONE unchecked item from `PLAN.md` per run, then stop.
- Stay inside this folder. Do not touch anything else on the machine. No sudo, no deleting
  outside this repo, no changes to Docker, Windows, or other projects.
- Never touch `data/` (it holds real user collections).

## How to build a feature
1. Read `PLAN.md`, take the first `- [ ]` item.
2. Prefer the engine's own functions (look in the installed package:
   `python3 -c "import anki, os; print(os.path.dirname(anki.__file__))"`, e.g. `collection.py`,
   `scheduler/v3.py`, `decks.py`, `models.py`, `stats.py`, `importing`, `_backend_generated.py`).
   Anki's source is also at https://github.com/ankitects/anki for reference.
3. Keep the code organised: if `app.py` grows past ~1500 lines, split routes into modules in
   `flashcards/` (e.g. `flashcards/study.py`) without changing behaviour.
4. Match the existing look (the CSS in `app.py`): plain, touch-friendly, works in dark mode.
5. Add tests for the feature to `tests/test_app.py` or a new `tests/test_<feature>.py`.
6. Run ALL tests: `python3 tests/test_engine.py && python3 tests/test_app.py` plus any new test
   files. Everything must pass. Never delete or weaken an existing test to make it pass.
7. If Playwright is installed, take a screenshot of the new page at 820x1180 into `shots/` and
   look at it; fix anything that looks broken.
8. Tick the item in `PLAN.md` (`- [x]`), add a short line to `CHANGELOG.md`, then
   `git add -A && git commit -m "<feature>"`.

## If stuck
If the same item has failed twice (see `NOTES.md`), mark it `- [~]` in `PLAN.md` with a one-line
reason, commit, and stop so the next run moves on. Never leave tests failing at the end of a run:
if you cannot fix them, `git checkout -- .` to discard the half-done work, note why, and stop.
