# Flashcards

A web flashcard app that runs on Anki's real engine, so scheduling (FSRS), cloze cards
and .apkg import/export behave exactly like Anki. Made for iPad and Chromebook use in a browser.

## What it does
- Log in, with a separate set of cards per person
- Decks and subdecks, with New / Learning / Due counts
- Study screen with Again / Hard / Good / Easy and Anki's real intervals, plus undo
- Keyboard shortcuts: Space shows the answer, 1-4 answer the card
- Add and edit cards: Basic, Basic (and reversed), Cloze, plus images and audio
- Import .apkg decks (including shared decks from AnkiWeb, with their images and audio)
- Export any deck as .apkg
- Browse cards using Anki's search syntax (deck:, tag:, is:due, ...)
- Deck options: new cards per day, reviews per day, desired retention
- Stats: today, last 7 days, collection totals
- FSRS on/off, password change, adding more people

## Run it on the home server
```
docker compose up -d --build
```
Then open http://localhost:8000. The first username and password you enter becomes the first account.
All data is kept in `./data`, so back that folder up.

## Let a friend reach it
Use an HTTPS tunnel instead of opening router ports, for example Tailscale Funnel
(`tailscale funnel 8000`) or a Cloudflare Tunnel. Then, on the iPad, open the link in Safari,
tap Share, and choose "Add to Home Screen".

## Tests
```
python3 tests/test_engine.py   # Anki engine: decks, cloze, import/export, FSRS reviews
python3 tests/test_app.py      # whole app, end to end, acting as a user
```

## Licence
AGPL-3.0 (see LICENSE, which is Anki's), because it builds on Anki (https://github.com/ankitects/anki). If you host it for
other people, you must make this source code available to them.
