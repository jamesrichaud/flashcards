"""Maintenance: Check Database, Empty Cards, daily backups (keep 30) and restore."""
import html, os, re, sys, tempfile
os.environ["DATA_DIR"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fastapi.testclient import TestClient
import app as webapp
from flashcards import maintenance as mt

ok = lambda m: print("PASS", m)
c = TestClient(webapp.app)


def flash(r):
    m = re.search(r'<div class="flash( err)?">(.*?)</div>', r.text, re.S)
    return ("!" if m and m.group(1) else "") + html.unescape(m.group(2)) if m else ""


# ---------- login needed ----------
anon = TestClient(webapp.app)
for path in ("/backups", "/check-database", "/empty-cards", "/backups/file/backup-2026-01-01-10.00.00.colpkg",
             "/backups/restore/backup-2026-01-01-10.00.00.colpkg"):
    r = anon.get(path, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login", path
for path in ("/backups/create", "/check-database", "/empty-cards", "/backups/restore/backup-2026-01-01-10.00.00.colpkg"):
    r = anon.post(path, data={"confirm": "1"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login", path
ok("every maintenance page needs a login")

c.post("/login", data={"username": "keeper", "password": "secret123"})
r = c.get("/settings")
for href in ('href="/check-database"', 'href="/empty-cards"', 'href="/backups"'):
    assert href in r.text, href
assert "No backups yet." in r.text
ok("settings links to Check database, Empty cards and Backups")

# ---------- daily backup ----------
folder = mt.backup_folder("keeper")
assert mt.list_backups("keeper") == []
ok("a brand-new (empty) collection is not backed up")

with webapp.collection("keeper") as col:
    n = col.new_note(col.models.by_name("Basic")); n["Front"] = "one"; n["Back"] = "1"; col.add_note(n, 1)
mt._checked.clear()
with webapp.collection("keeper") as col:
    pass
names = mt.list_backups("keeper")
assert len(names) == 1 and mt.NAME_RE.fullmatch(names[0]), names
assert mt.backup_time(names[0]).date() == __import__("datetime").date.today()
assert not [n for n in os.listdir(folder) if n.startswith(".stage-")], os.listdir(folder)
ok("first use of the day makes the engine's backup in data/users/<name>/backups (staging folder removed)")

mt._checked.clear()
with webapp.collection("keeper") as col:
    n = col.new_note(col.models.by_name("Basic")); n["Front"] = "two"; col.add_note(n, 1)
mt._checked.clear()
with webapp.collection("keeper") as col:
    pass
assert mt.list_backups("keeper") == names
ok("only one automatic backup per day")

# Yesterday's backup only: today's is made.
os.rename(os.path.join(folder, names[0]), os.path.join(folder, "backup-2000-01-02-10.00.00.colpkg"))
mt._checked.clear()
c.get("/")
now = mt.list_backups("keeper")
assert len(now) == 2 and now[1] == "backup-2000-01-02-10.00.00.colpkg", now
ok("a backup from an earlier day leads to a new one today")

# Keep 30.
for d in range(1, 36):
    open(os.path.join(folder, f"backup-2001-01-{d % 28 + 1:02d}-{d % 24:02d}.00.00.colpkg"), "wb").write(b"x")
open(os.path.join(folder, "notes.txt"), "w").write("not a backup")
with webapp.collection("keeper") as col:
    assert mt.make_backup("keeper", col) is None   # engine: no changes since its latest backup
    n = col.new_note(col.models.by_name("Basic")); n["Front"] = "three"; col.add_note(n, 1)
    made = mt.make_backup("keeper", col)
kept = mt.list_backups("keeper")
assert len(kept) == 30 and kept[0] == made, (len(kept), kept[:2], made)
assert "backup-2000-01-02-10.00.00.colpkg" not in kept
assert os.path.exists(os.path.join(folder, "notes.txt"))
ok("the 30 newest backups are kept, older ones deleted, other files left alone")

# ---------- backups page, create, download ----------
r = c.get("/backups")
assert r.status_code == 200 and "Back up now" in r.text and "(latest)" in r.text
assert r.text.count("Restore…") == 30
r = c.post("/backups/create")
assert flash(r) in ("Backup created.", "No changes since latest backup."), flash(r)
with webapp.collection("keeper") as col:
    n = col.new_note(col.models.by_name("Basic")); n["Front"] = "three"; col.add_note(n, 1)
r = c.post("/backups/create")
assert flash(r) == "Backup created.", flash(r)
latest = mt.list_backups("keeper")[0]
r = c.get(f"/backups/file/{latest}")
assert r.status_code == 200 and r.content[:2] == b"PK" and latest in r.headers["content-disposition"]
for bad in ("..%2Fcollection.anki2", "notes.txt", "backup-1999-01-01-00.00.00.colpkg"):
    assert c.get(f"/backups/file/{bad}").status_code == 404, bad
assert "Latest:" in c.get("/settings").text
ok("Back up now (engine's 'Backup created.'), list, download; bad names refused")

# ---------- restore ----------
with webapp.collection("keeper") as col:
    assert col.note_count() == 4
    n = col.new_note(col.models.by_name("Basic")); n["Front"] = "four"; col.add_note(n, 1)
    col.media.write_data("keep.png", b"png")
before = set(mt.list_backups("keeper"))
r = c.get(f"/backups/restore/{latest}")
assert "replaces everything" in r.text and "5 notes" in r.text and 'name="confirm"' in r.text
r = c.post(f"/backups/restore/{latest}", data={})
assert flash(r).startswith("!Tick the box"), flash(r)
with webapp.collection("keeper") as col:
    assert col.note_count() == 5
r = c.post(f"/backups/restore/{latest}", data={"confirm": "1"})
assert "restored: 4 notes" in flash(r), flash(r)
with webapp.collection("keeper") as col:
    assert col.note_count() == 4
    assert sorted(col.find_notes("")) and "four" not in [col.get_note(i)["Front"] for i in col.find_notes("")]
    assert os.path.exists(os.path.join(col.media.dir(), "keep.png"))
safety = set(mt.list_backups("keeper")) - before
assert len(safety) == 1, safety
ok("restore asks for a tick, makes a safety backup, replaces the collection, keeps media")

# Restoring the safety backup brings the newer note back.
r = c.post(f"/backups/restore/{safety.pop()}", data={"confirm": "1"})
assert "restored: 5 notes" in flash(r), flash(r)
ok("the safety backup undoes a restore")

# Restoring the oldest backup when at the limit still works (it is copied before pruning).
with webapp.collection("keeper") as col:
    n = col.new_note(col.models.by_name("Basic")); n["Front"] = "five"; col.add_note(n, 1)
real_oldest = [n for n in mt.list_backups("keeper")]
# make the oldest kept file a real backup of a 1-note collection
import shutil
tmpd = tempfile.mkdtemp()
from anki.collection import Collection
small = Collection(os.path.join(tmpd, "s.anki2"))
sn = small.new_note(small.models.by_name("Basic")); sn["Front"] = "solo"; small.add_note(sn, 1)
os.makedirs(os.path.join(tmpd, "b"))
small.create_backup(backup_folder=os.path.join(tmpd, "b"), force=True, wait_for_completion=True)
small.close()
oldest = mt.list_backups("keeper")[-1]
shutil.copyfile(os.path.join(tmpd, "b", os.listdir(os.path.join(tmpd, "b"))[0]), os.path.join(folder, oldest))
assert len(mt.list_backups("keeper")) == 30
r = c.post(f"/backups/restore/{oldest}", data={"confirm": "1"})
assert "restored: 1 notes" in flash(r), flash(r)
assert oldest not in mt.list_backups("keeper") and len(mt.list_backups("keeper")) == 30
assert not [n for n in os.listdir(folder) if n.endswith(".colpkg") and not mt.NAME_RE.fullmatch(n)]
ok("restoring the oldest backup at the 30 limit works; temporary copy removed")

# A broken backup file leaves the collection unchanged.
broken = "backup-2001-01-01-00.00.00.colpkg"
open(os.path.join(folder, broken), "wb").write(b"not a zip")
r = c.post(f"/backups/restore/{broken}", data={"confirm": "1"})
assert flash(r).startswith("!Restore failed"), flash(r)
with webapp.collection("keeper") as col:
    assert col.note_count() == 1
r = c.get(f"/backups/restore/nothere.colpkg")
assert "not found" in flash(r)
ok("a broken backup is refused and the collection stays as it was")

# ---------- check database ----------
r = c.get("/check-database"); assert r.status_code == 200 and "Check Database" in r.text
r = c.post("/check-database")
assert "No problems found." in r.text and "Database rebuilt and optimized." in r.text
with webapp.collection("keeper") as col:
    nid = col.find_notes("")[0]
    cid = col.find_cards("")[0]
    col.db.execute("delete from notes where id = ?", nid)  # a card without a note
r = c.post("/check-database")
assert "Problems were found and fixed" in r.text, r.text
assert re.search(r"<li>[^<]*card", r.text, re.I)
with webapp.collection("keeper") as col:
    assert not col.db.scalar("select count() from cards where id = ?", cid)
r = c.post("/check-database"); assert "No problems found." in r.text
ok("Check Database runs the engine's check, reports what it fixed (card without a note removed)")

# ---------- empty cards ----------
r = c.get("/empty-cards"); assert "No empty cards." in r.text
with webapp.collection("keeper") as col:
    opt = col.models.by_name("Basic (optional reversed card)")
    a = col.new_note(opt); a["Front"] = "<b>apple</b>"; a["Back"] = "pomme"; a["Add Reverse"] = "y"; col.add_note(a, 1)
    a["Add Reverse"] = ""; col.update_note(a)
    basic = col.models.by_name("Basic")
    e = col.new_note(basic); e["Front"] = "blank soon"; e["Back"] = "x"; col.add_note(e, 1)
    e["Front"] = ""; col.update_note(e)
    nt = col.models.by_name("Basic"); nt_name = nt["name"]
    # note type names are escaped in the report
    opt["name"] = "<i>Opt</i>"; col.models.update_dict(opt)
    a_nid, e_nid = a.id, e.id
r = c.get("/empty-cards")
assert "2 empty card(s) in 2 note(s)" in r.text, r.text
assert "Keep notes with no valid cards" in r.text and "checked" in r.text
assert "&lt;i&gt;Opt&lt;/i&gt;" in r.text and "<i>Opt</i>" not in r.text
assert "1 of 2 cards empty (Card 2)." in r.text
assert f"nid%3A{a_nid}" in r.text and ">apple<" in r.text
assert "whole note would go" in r.text
ok("Empty Cards lists the engine's report per note type, escaped, with Browse links")

r = c.post("/empty-cards", data={"keep_notes": "1"})
assert flash(r) == "Deleted 1 card.", flash(r)
with webapp.collection("keeper") as col:
    assert len(col.get_note(a_nid).card_ids()) == 1
    assert len(col.get_note(e_nid).card_ids()) == 1  # kept: the note had no valid cards
r = c.get("/empty-cards"); assert "1 empty card(s) in 1 note(s)" in r.text
r = c.post("/empty-cards", data={})
assert flash(r) == "Deleted 1 card.", flash(r)
with webapp.collection("keeper") as col:
    assert e_nid not in col.find_notes("")
assert "No empty cards." in c.get("/empty-cards").text
r = c.post("/empty-cards", data={}); assert flash(r) == "Deleted 0 cards.", flash(r)
ok("Delete keeps a note's first card when 'keep notes' is ticked, deletes the note when not")

print("all maintenance tests passed")
