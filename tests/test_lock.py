from __future__ import annotations

import pytest
from conftest import FakeClient, make_file

from slothdesk import cli
from slothdesk.lock import AlreadyRunning, SingleInstance


def test_second_holder_is_rejected_then_released(tmp_path):
    path = tmp_path / "state" / "x.lock"
    with SingleInstance(path), pytest.raises(AlreadyRunning), SingleInstance(path):
        pass
    with SingleInstance(path):  # free again after the first exits
        pass


def test_apply_refuses_while_another_run_holds_lock(root):
    make_file(root, "bill.txt")
    lock = root / ".slothdesk" / "slothdesk.lock"
    with SingleInstance(lock):
        rc = cli.main(
            ["run", "--root", str(root), "--apply"], client_factory=lambda c: FakeClient()
        )
    assert rc == cli.EXIT_FAIL
    assert (root / "bill.txt").exists()  # nothing moved


def test_dry_run_does_not_need_the_lock(root):
    make_file(root, "bill.txt")
    lock = root / ".slothdesk" / "slothdesk.lock"
    with SingleInstance(lock):
        rc = cli.main(["run", "--root", str(root)], client_factory=lambda c: FakeClient())
    assert rc == cli.EXIT_OK


def test_unencodable_filenames_do_not_crash_a_legacy_console(root, monkeypatch):
    import io

    make_file(root, "日本語の請求書.txt")
    raw = io.BytesIO()
    console = io.TextIOWrapper(raw, encoding="cp1252", errors="strict")
    monkeypatch.setattr("sys.stdout", console)
    rc = cli.main(["run", "--root", str(root)], client_factory=lambda c: FakeClient())
    console.flush()
    assert rc == cli.EXIT_OK
    assert b"Would move" in raw.getvalue()
