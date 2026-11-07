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

## Browse upgrade
- Rows and cells come from the engine's browser table (`browser_row_for_id`), so the columns read exactly as in Anki's browser (dates, "3 days", "250%", notes-mode "Avg. Ease" etc.). The engine reads Anki's `BROWSER_TABLE_SHOW_NOTES_MODE` flag to decide whether an id is a card or a note, so the page keeps that flag in sync with the chosen mode. (Forgetting this made notes-mode rows vanish at random, because note ids were looked up as card ids.)
- Notes mode stays the default, as Browse was notes-only before. The chosen mode is remembered in the collection config (`flashcardsBrowseMode`). Sort column and direction use Anki's own keys (`sortType`/`sortBackwards`, `noteSortType`/`browserNoteSortBackwards`), and chosen columns use `activeCols`/`activeNoteCols`. So they match desktop Anki after a sync.
- Default columns, used until the user picks their own: sort field, card type (cards mode) or note type (notes mode), deck, due, interval, ease, reviews, lapses, plus tags in notes mode. Anki's own defaults have only four columns, and the plan asks for the scheduling ones. "Default columns" in the chooser restores these.
- Clicking a header sorts by the column's natural order (Anki's `sorting_*` value); clicking again reverses it. Columns the engine can't sort in the current mode (question, answer; FSRS columns in notes mode) aren't links.
- The State column (flag dot, ★, Suspended/Buried/…) and the ⓘ card-info link are kept in both modes. Row tints use the engine's row colour in cards mode, and the card-based summary in notes mode.
- Selection: a checkbox per row, a "select all on page" box, and "All N in this search", which applies to every match, not only the visible 100. One Action menu with its options shown for the chosen action. Delete and Reset ask first.
- Delete removes whole notes in both modes, as Anki's browser does. Reposition only touches new cards (`reposition_new_cards`). It defaults to start 0, step 1, no randomize, no shift. If none of the selected cards is new, it says so.
- Find and replace uses `col.find_and_replace` (one field or all fields) and `col.tags.find_and_replace` for the "Tags" choice. It works on the selected notes, or every note in the search when "All" is ticked. A bad regex is caught before calling the engine. The field list is every field name across note types. The engine skips notes that don't have that field.
- No undo button on Browse yet; the engine records these as undoable ops, and the "Undo/redo everywhere" plan item will expose it.
- Screenshots: Chromium still can't start (libnspr4 missing). `tests/shots_browse.py` will take them later.
