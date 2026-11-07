# Notes

Decisions made during unattended builds.

## Card actions (suspend, bury, flag, mark, reset, set due date)
- One endpoint `POST /card/{id}/action` (action + arg + back). It returns JSON for `fetch` calls (study screen) and redirects with a message for plain forms (edit page).
- Study screen: a "More" menu plus Anki's keys: `-` bury card, `=` bury note, `@` suspend card, `!` suspend note, `*` mark, Ctrl+1..7 flag, Ctrl+Shift+D set due date, Ctrl+Alt+N reset. Flag and mark update in place without reloading, so you stay on the same side of the card. The other actions move on to the next card.
- Chrome keeps Ctrl+1..7 for switching tabs when the page does not have focus. The menu's flag buttons always work, which matters most on touch screens.
- Pressing the same flag again clears it, as in Anki. "Reset to new" uses `schedule_cards_as_new(restore_position=True, reset_counts=False)`, which matches Anki's dialog defaults.
- Browse is still in notes mode (cards mode comes with the Browse upgrade item), so per-card actions are in a "Cards" panel on the note's edit page, which Browse links to. Browse rows are tinted like Anki's: yellow for suspended, the flag colour for flagged, purple for marked.
- The study screen's Undo now also covers these actions. It uses the engine's own undo labels: Suspend, Bury, Set Flag, Update/Remove Tag, Reset Card, Set Due Date.
- Screenshots: Playwright's Chromium can't start on this machine because the system library libnspr4 is missing. Installing it needs sudo, which these rules forbid, so no screenshots were taken. `tests/shots_card_actions.py` will make them once the library is installed (`sudo npx playwright install-deps`). The pages were checked through the test client instead.

## Card info page
- Data comes from the engine's `col.card_stats_data(cid)` (the same call Anki's Card Info window uses), so dates, times and the review history match Anki. History is newest first, as in Anki.
- Difficulty is shown as a percentage, `(D - 1) / 9`, as Anki does. Stability is shown as a time span.
- Turning FSRS off with the Settings toggle leaves the FSRS memory state on cards. The page follows the collection's FSRS setting: stability/difficulty/retrievability when it is on, ease when it is off.
- Links: study screen "More" menu and the `I` key (Anki's reviewer shortcut); a "Card info" button per card on the edit page's Cards panel; an ⓘ link per row in Browse, which opens the note's first card because Browse is still notes-only. Each link passes `back=` so the page has a Back button. Off-site back URLs are ignored.
- Leaving the study screen for card info and returning restarts the answer timer for that card. This is acceptable for now.
- Screenshots: Chromium still can't start (libnspr4 missing, needs sudo). `tests/shots_card_info.py` will take them once it is installed.
