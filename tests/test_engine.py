"""Step 1: prove Anki's real engine works headless.
Creates a collection, adds basic + cloze cards, exports to .apkg,
imports that .apkg into a fresh collection, and reviews cards."""
import os, sys, tempfile
from anki.collection import Collection, ImportAnkiPackageRequest, ExportAnkiPackageOptions
from anki.scheduler.v3 import CardAnswer

tmp = tempfile.mkdtemp()
ok = lambda msg: print("PASS", msg)

# 1. Create a collection and a deck
col = Collection(os.path.join(tmp, "a.anki2"))
did = col.decks.id("Test Deck")
ok(f"created deck id={did}")

# 2. Add a Basic card
basic = col.models.by_name("Basic")
n = col.new_note(basic); n["Front"] = "Capital of Ireland?"; n["Back"] = "Dublin"
col.add_note(n, did)

# 3. Add a Cloze note (2 cards)
cloze = col.models.by_name("Cloze")
c = col.new_note(cloze); c["Text"] = "{{c1::Supply}} and {{c2::demand}} set price."
col.add_note(c, did)
assert col.card_count() == 3, col.card_count()
ok("added 1 basic + 1 cloze note -> 3 cards")

# 4. Export to .apkg
apkg = os.path.join(tmp, "deck.apkg")
col.export_anki_package(out_path=apkg, options=ExportAnkiPackageOptions(
    with_scheduling=False, with_deck_configs=True, with_media=True, legacy=False), limit=None)
assert os.path.getsize(apkg) > 0
ok(f"exported .apkg ({os.path.getsize(apkg)} bytes)")
col.close()

# 5. Import .apkg into a brand-new collection
col2 = Collection(os.path.join(tmp, "b.anki2"))
col2.import_anki_package(ImportAnkiPackageRequest(package_path=apkg))
assert col2.card_count() == 3, col2.card_count()
ok("imported .apkg into fresh collection -> 3 cards")

# 6. Review with the real v3 scheduler (FSRS on)
col2.set_config("fsrs", True)
deck = col2.decks.by_name("Test Deck")
col2.decks.select(deck["id"])
answered = []
for rating in (CardAnswer.GOOD, CardAnswer.AGAIN, CardAnswer.EASY):
    q = col2.sched.get_queued_cards()
    card = col2.get_card(q.cards[0].card.id)
    states = q.cards[0].states
    card.start_timer()
    question = card.question()
    col2.sched.answer_card(col2.sched.build_answer(card=card, states=states, rating=rating))
    answered.append((card.id, rating))
ok(f"answered {len(answered)} cards with Good/Again/Easy")

# 7. Check scheduling changed and rendering works
counts = col2.sched.counts()
revlog = col2.db.scalar("select count() from revlog")
assert revlog == 3, revlog
ok(f"review log has {revlog} entries; queue counts new/learn/review = {counts}")
import re
cloze_ids = [cid for cid in col2.find_cards("") if "[...]" in col2.get_card(cid).question()]
assert len(cloze_ids) == 2, cloze_ids
ok("cloze cards render with [...] blanks")
col2.close()
print("ALL ENGINE TESTS PASSED")
