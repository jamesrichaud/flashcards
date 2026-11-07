# Flashcards

A web flashcard app that runs on Anki's real engine, so scheduling (FSRS), cloze cards
and .apkg import/export behave exactly like Anki. Made for iPad and Chromebook use in a browser.

## What it does
- Log in, with a separate set of cards per person
- Decks and subdecks, with New / Learning / Due counts
- Study screen with Again / Hard / Good / Easy and Anki's real intervals, plus undo
- Keyboard shortcuts: Space shows the answer, 1-4 answer the card
- Add and edit cards: Basic, Basic (and reversed), Cloze, plus images and audio
- Import .apkg decks (including shared decks from AnkiWeb, with their images and audio) with Anki's import options
- Import text files (CSV/TSV/TXT) with separator, note type, deck, field mapping, tags and duplicate handling
- Import a whole collection (.colpkg), replacing everything after a confirm and a backup
- Export the collection (.colpkg), a deck or Browse search (.apkg), or notes/cards as plain text
- Browse cards using Anki's search syntax (deck:, tag:, is:due, ...)
- Deck options: new cards per day, reviews per day, desired retention
- Stats: today, last 7 days, collection totals
- FSRS on/off, password change, adding more people
- All your devices share the same cards; sync with AnkiDroid, AnkiMobile and desktop Anki through Anki's own sync server (or AnkiWeb)

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

## Tests
```
python3 tests/test_engine.py   # Anki engine: decks, cloze, import/export, FSRS reviews
python3 tests/test_app.py      # whole app, end to end, acting as a user
python3 tests/test_sync.py     # two browsers, and syncing through Anki's sync server
```

## Licence
AGPL-3.0 (see LICENSE, which is Anki's), because it builds on Anki (https://github.com/ankitects/anki). If you host it for
other people, you must make this source code available to them.
