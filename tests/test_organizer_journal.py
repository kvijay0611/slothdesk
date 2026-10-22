import os
import sqlite3

import pytest

from slothdesk.classify import Decision
from slothdesk.journal import MIGRATIONS, Journal, JournalError
from slothdesk.organizer import destination_for, is_inside, rule_decision, safe_move


def dec(cat="Finance", title="Bill"):
    return Decision(cat, title, 0.9, "r", "llm")


def test_rule_decisions(tmp_path):
    assert rule_decision(tmp_path / "A.PNG").category == "Images"
    assert rule_decision(tmp_path / "setup.exe").category == "Installers"
    assert rule_decision(tmp_path / "notes.txt") is None


def test_destination_adds_date_and_avoids_collisions(cfg, root):
    src = root / "x.txt"
    src.write_text("x")
    taken: set[str] = set()
    a = destination_for(cfg, src, dec(), taken, 0)
    b = destination_for(cfg, src, dec(), taken, 0)
    assert a != b and "(2)" in b.name and a.suffix == ".txt"
    a.parent.mkdir(parents=True)
    a.write_text("existing")
    c = destination_for(cfg, src, dec(), set(), 0)
    assert c != a


def test_rule_files_keep_their_name(cfg, root):
    src = root / "My Photo.JPG"
    d = destination_for(cfg, src, Decision("Images", None, 1, "", "rule"), set(), 0)
    assert d.name == "My Photo.jpg"


def test_long_names_are_shortened(cfg, root):
    src = root / "x.txt"
    d = destination_for(cfg, src, dec(title="a" * 60), set(), 0)
    assert len(str(d)) < 260
    src2 = root / ("n" * 200 + ".png")
    d2 = destination_for(cfg, src2, Decision("Images", None, 1, "", "rule"), set(), 0)
    assert len(str(d2)) <= 240 + 20


def test_weird_extension_dropped(cfg, root):
    d = destination_for(cfg, root / "file.we ird!", dec(), set(), 0)
    assert d.suffix == ""


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_symlinked_category_folder_is_refused(cfg, root, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    cfg.sorted_dir.mkdir()
    (cfg.sorted_dir / "Finance").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="escapes"):
        destination_for(cfg, root / "x.txt", dec(), set(), 0)
    assert not is_inside(cfg.sorted_dir, outside)


def test_safe_move_never_overwrites(tmp_path):
    a, b = tmp_path / "a.txt", tmp_path / "sub" / "b.txt"
    a.write_text("A")
    safe_move(a, b)
    assert b.read_text() == "A" and not a.exists()
    a.write_text("new")
    with pytest.raises(FileExistsError):
        safe_move(a, b)
    assert b.read_text() == "A" and a.read_text() == "new"


def test_safe_move_falls_back_when_links_unsupported(tmp_path, monkeypatch):
    import errno

    def no_link(*a, **k):
        raise OSError(errno.EPERM, "nope")

    monkeypatch.setattr(os, "link", no_link)
    a, b = tmp_path / "a.txt", tmp_path / "b.txt"
    a.write_text("A")
    safe_move(a, b)
    assert b.read_text() == "A"


def test_safe_move_propagates_other_errors(tmp_path, monkeypatch):
    import errno

    def boom(*a, **k):
        raise OSError(errno.EIO, "io")

    monkeypatch.setattr(os, "link", boom)
    a = tmp_path / "a.txt"
    a.write_text("A")
    with pytest.raises(OSError):
        safe_move(a, tmp_path / "b.txt")


# journal ---------------------------------------------------------------------
def test_migration_is_idempotent_and_versioned(tmp_path):
    p = tmp_path / "s" / "j.db"
    Journal(p).close()
    j = Journal(p)
    assert j._db.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)
    j.close()


def test_newer_database_is_refused(tmp_path):
    p = tmp_path / "j.db"
    Journal(p).close()
    c = sqlite3.connect(p)
    c.execute("PRAGMA user_version = 99")
    c.close()
    with pytest.raises(JournalError):
        Journal(p)


def test_transactions_roll_back(journal):
    journal.start_run("r1", "apply", "m")
    with pytest.raises(RuntimeError), journal.tx() as db:
        db.execute("UPDATE runs SET status='x' WHERE id='r1'")
        raise RuntimeError("boom")
    assert journal.get_run("r1")["status"] == "running"


def test_update_rejects_unknown_columns(journal):
    journal.start_run("r1", "apply", "m")
    mid = journal.add_move("r1", "a.txt", "pending")
    with pytest.raises(JournalError):
        journal.update_move(mid, src="evil")
    with pytest.raises(sqlite3.IntegrityError):
        journal.update_move(mid, status="bogus")


def test_decision_cache_roundtrip(journal):
    assert journal.get_decision("h", "m", "v1") is None
    journal.put_decision("h", "m", "v1", dec())
    d = journal.get_decision("h", "m", "v1")
    assert d.source == "cache" and d.category == "Finance"
    assert journal.get_decision("h", "other-model", "v1") is None


def test_purge_and_backup(journal, tmp_path):
    journal.start_run("r1", "apply", "m")
    journal.add_move("r1", "a.txt", "moved")
    journal.put_decision("h", "m", "v1", dec())
    assert journal.purge_older_than(1) == 0
    journal._db.execute("UPDATE moves SET created_at='2000-01-01T00:00:00+00:00'")
    journal._db.execute("UPDATE decisions SET created_at='2000-01-01T00:00:00+00:00'")
    journal._db.execute("UPDATE runs SET started_at='2000-01-01T00:00:00+00:00', status='ok'")
    assert journal.purge_older_than(30) == 2
    assert journal.get_run("r1") is None
    out = tmp_path / "backup.db"
    journal.backup_to(out)
    assert out.exists()
    with pytest.raises(JournalError):
        journal.backup_to(out)


def test_last_apply_run_none(journal):
    assert journal.last_apply_run_id() is None


def test_state_folder_is_private(cfg):
    Journal(cfg.db_path).close()
    if os.name != "nt":
        assert oct(cfg.state_dir.stat().st_mode & 0o777) == "0o700"


def test_add_move_rejects_unknown_fields(journal):
    journal.start_run("r1", "apply", "m")
    with pytest.raises(JournalError):
        journal.add_move("r1", "a.txt", "pending", evil="x")
