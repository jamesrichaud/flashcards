# Flashcards

A web flashcard app that runs on Anki's real engine, so scheduling (FSRS), cloze cards
and .apkg import/export behave exactly like Anki. Made for iPad and Chromebook use in a browser.

## What it does
Everything runs on Anki's own engine, so cards, scheduling and files stay compatible with Anki.

**Studying**
- Decks and subdecks with New / Learning / Due counts; collapse/expand, rename, move under another deck, descriptions, "studied N cards in M minutes today"
- Study screen with Again / Hard / Good / Easy and Anki's intervals (SM-2 or FSRS); Again comes back in the same session
- Keyboard as in Anki: Space shows the answer, 1-4 answer, `-` bury, `@` suspend, `*` mark, Ctrl+1..7 flags, R replays audio, Ctrl+Z undo
- Type-in-the-answer cards with Anki's comparison, audio auto-play and replay, MathJax, leech notice
- Card actions: suspend, bury card or note, flags, mark, reset to new, set due date, edit and come back to the same card, card info with full review history
- Optional "tap the card to answer" for iPads (left half Again, right half Good)
- Study reminders: a daily notification at your chosen time and days, only when cards are due (Web Push; needs the HTTPS link, and on iPad the Home Screen app)
- Custom study (raise today's limits, forgotten cards, review ahead, preview new, by state or tag) and filtered decks
- Deck options with presets: steps, intervals, limits, order, burying, leeches, ease, FSRS retention and Optimize
- Undo and redo of everything, with a message saying what was undone

**Cards and note types**
- Rich editor for Add/Edit: formatting, colours, lists, cloze buttons and keys, pictures, audio recording, MathJax, sticky fields, duplicate warning, preview
- LaTeX on cards ([latex], [$], [$$]) as images, like Anki: turn on "Generate LaTeX images" in Preferences; needs latex + dvipng on the server (in the Docker image), otherwise the LaTeX source is shown; Render LaTeX in Check Media
- Image occlusion (rectangles and ellipses, hide all or hide one), compatible with desktop Anki
- Note type manager: stock types, fields, card templates with live preview, change note type
- Browse with Anki's search syntax: cards or notes, sortable columns, flags and suspended shown, bulk change deck, tags, suspend, delete, reset, reposition, find and replace
- Browse sidebar like Anki's: saved searches, Today, flags, card states, deck tree, note types and card types, hierarchical tags (rename, delete, clear unused); a slide-out panel on iPad

**Files, stats and upkeep**
- Import .apkg (shared decks from AnkiWeb, with media, from a file or by pasting a link: the server downloads it with a progress bar), .colpkg (whole collection, after a confirm and a backup) and CSV/TSV/TXT with field mapping; export .colpkg, .apkg and plain text
- Statistics with Anki's graphs (future due, calendar, reviews, card counts, intervals, ease or FSRS difficulty/stability/retrievability, hours, buttons, added, retention) for a deck or everything
- Preferences: next day starts at, learn ahead, timebox, counts and next times on buttons, light/dark/system theme
- Languages: English, Español, Français, Deutsch, Português, Italiano, Gaeilge, Polski; each person picks one in Preferences (default: the browser's language); Anki's own wording is used where Anki has it
- Check database, check media, empty cards, daily backups (30 kept) with restore
- Separate cards per person; all devices share the same cards; sync with AnkiDroid, AnkiMobile and desktop Anki through Anki's own sync server (or AnkiWeb)
- Server-side add-ons (card render, after answer, extra pages); desktop Anki add-ons don't run here
- Works on iPad and Chromebook: Add to Home Screen / install, 44px touch targets, safe areas, dark mode, offline page

## Run it on the home server
```
docker compose up -d --build
```
Then open http://localhost:8000. The first username and password you enter becomes the first account.
All data is kept in `./data`, so back that folder up.

## Let a friend reach it
Use an HTTPS tunnel instead of opening router ports, for example Tailscale Funnel
(`tailscale funnel 8000`) or a Cloudflare Tunnel. Then, on the iPad, open the link in Safari,
tap Share, and choose "Add to Home Screen". It opens full screen with its own icon. On a
Chromebook, use Chrome's "Install" (or "Create shortcut… > Open as window").

Over HTTPS (or on localhost), a page that can't reach the server shows a "You're offline" page with
Try again instead of the browser's error, and a red bar appears while the device has no connection.
Studying itself needs the connection: answers are saved on the server straight away.

For iPads, Preferences has "Tap the card to answer": tap the card to show the answer, then tap its
left half for Again or its right half for Good (the answer buttons still work).

Study reminders (Settings > Study reminders) are Web Push notifications. They need the HTTPS link.
On iPad (iPadOS 16.4 or later) open the app from the Home Screen first, then tap "Turn on
notifications here"; Safari tabs can't get them. On a Chromebook, Chrome shows them while Chrome is
running. The server keeps its push keys in `data/vapid_private.pem` and `data/vapid_public.txt`;
if they are deleted, each device has to turn notifications on again.

## Using it on several devices
Every iPad, Chromebook or computer that logs in as the same person uses the same cards on the
server, so they never need syncing with each other. A page left open on one device refreshes itself
when you come back to it, if another device changed something (unless you had typed into it).

## Sync with Anki apps (AnkiDroid, AnkiMobile, desktop Anki)
Anki comes with its own sync server (`python -m anki.syncserver`), which works like AnkiWeb.
The web app and the Anki apps all sync with it; changes are merged both ways.

1. Choose a sync user name and password for each person, and put them in a `.env` file next to
   `docker-compose.yml`:
   ```
   SYNC_USER1=james:a-long-password
   ```
   (For more people add `SYNC_USER2=...` etc. to the `flashcards-sync` service's `environment`.)
2. Start the app together with the sync server:
   ```
   docker compose --profile sync up -d --build
   ```
   The sync server listens on port 8080 and keeps its copy of the cards in `./data/syncserver`.
3. In the web app: Settings → Sync. The server address is filled in (`http://flashcards-sync:8080/`
   inside Docker). Log in with the sync user name and password, then tap **Sync now**. The first
   time it asks to upload your cards to the empty server. Tick "Sync automatically when the deck
   list opens" if you like.
4. In AnkiDroid: Settings → Sync → Custom sync server, set the sync URL to
   `http://<your-server>:8080/`. Desktop Anki: Preferences → Syncing → Self-hosted sync server.
   AnkiMobile: Settings → Syncing → Custom server. Then log in with the same user name and password
   and sync (choose Download the first time).

Without Docker: `SYNC_USER1=james:pw SYNC_BASE=./data/syncserver python3 -m anki.syncserver`,
then use `http://<computer>:8080/` as the address. Over the internet, put the sync server behind
HTTPS as well (the same tunnel as the app). Leaving the address blank on the Sync page syncs with
AnkiWeb instead. Only a sync key is kept (in `data/users/<name>/sync.json`), never the password.

## Add-ons
Desktop Anki add-ons (the ones on ankiweb.net) **don't run here**: they are written for Anki's
desktop window (Qt, `aqt`), which this web app doesn't have. The app recognises them and won't
turn them on.

Instead it has its own small add-ons: Python files in `data/addons/` (`<name>.py`, or a folder
with an `__init__.py`). They start switched off; turn them on or off in Settings → Add-ons
(this applies to everyone using the server). An add-on can define any of:

```python
NAME = "My add-on"                         # shown on the Add-ons page
DESCRIPTION = "What it does"               # or the file's docstring

def card_render(text, card, kind, col):    # kind: "question" or "answer"
    return text                            # change the card's HTML before it is shown

def after_answer(col, card, rating):       # rating: 1 Again, 2 Hard, 3 Good, 4 Easy
    ...

def deck_overview(col, did):               # extra HTML on the deck overview
    return ""
def study_page(col, card):                 # extra HTML/script on the study screen
    return ""
def browse_tools(col, mode):               # extra HTML in Browse's action bar; a button with
    return ""                              # data-addon formaction="/addon/<id>/..." posts the selection

from fastapi import APIRouter
from flashcards import addons
router = APIRouter()                       # extra pages at /addon/<name>/... (login needed)
@router.get("/")
def home(request):
    return addons.page(request, "Title", "<div class='box'>Hello</div>")
MENU = [("Open", "/")]                     # buttons on the Add-ons page
```

Eleven add-ons come with the app in `bundled_addons/`, all off until turned on in Settings → Add-ons:
Edit Field During Review, Speed Focus Mode, Read Aloud (text-to-speech), Pop-up Dictionary,
Study Streaks & Achievements, AnkiConnect (for Yomitan and other tools; each person makes their
own key on the add-on's page), Customize Keyboard Shortcuts, More Overview Stats, Advanced Copy
Fields (a panel in Browse), Hierarchical Tags Filter (tag tree on the deck overview) and Anki
Simulator (future daily workload from the engine's FSRS simulator). Add-ons are turned on for the whole server; each person has their own settings.

A worked example is in `addons_examples/answer_counter.py`; copy it into `data/addons/` to try
it. An add-on that raises an error is skipped (studying carries on) and the error is shown on
the Add-ons page; an edited file is picked up without a restart. Add-ons run with the server's
full rights, so only install files you trust.

## Tests
```
python3 tests/run_all.py         # every tests/test_*.py file, each in its own process
python3 tests/test_engine.py     # Anki engine: decks, cloze, import/export, FSRS reviews
python3 tests/test_app.py        # whole app, end to end, acting as a user
python3 tests/test_regression.py # visits every page the app links to and checks each one
python3 tests/shots_all.py       # screenshots of every page at iPad and Chromebook sizes (needs Playwright + Chromium)
```

## Licence
AGPL-3.0 (see LICENSE, which is Anki's), because it builds on Anki (https://github.com/ankitects/anki). If you host it for
other people, you must make this source code available to them.
