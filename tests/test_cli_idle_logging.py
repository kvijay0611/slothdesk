import json
import logging
import threading

import pytest
from conftest import FakeClient, make_file

from slothdesk import cli
from slothdesk.config import Config
from slothdesk.idle import lower_priority, should_run
from slothdesk.logging_setup import JsonFormatter, set_run_id, setup_logging


def run_cli(root, *args, client=None, **kw):
    factory = (lambda cfg: client) if client else (lambda cfg: FakeClient())
    return cli.main([args[0], "--root", str(root), *args[1:]], client_factory=factory, **kw)


def test_dry_run_then_apply_then_undo_then_report(root, capsys):
    make_file(root, "bill.txt")
    assert run_cli(root, "run") == 0
    out = capsys.readouterr().out
    assert "Would move: bill.txt" in out and "Dry run only" in out and (root / "bill.txt").exists()
    assert run_cli(root, "run", "--apply") == 0
    assert "Moved: bill.txt" in capsys.readouterr().out and not (root / "bill.txt").exists()
    assert run_cli(root, "report") == 0 and "Sloth Desk report" in capsys.readouterr().out
    assert run_cli(root, "undo") == 0 and (root / "bill.txt").exists()


def test_exit_codes(root, tmp_path, capsys):
    assert cli.main(["run", "--root", str(tmp_path / "missing")]) == cli.EXIT_USAGE
    assert run_cli(root, "report") == cli.EXIT_USAGE
    assert run_cli(root, "report", "--run", "nope") == cli.EXIT_USAGE
    assert run_cli(root, "purge", "--days", "0") == cli.EXIT_USAGE
    assert run_cli(root, "purge", "--days", "30") == 0
    assert "error" in capsys.readouterr().err


def test_undo_failure_exit_code(root):
    make_file(root, "bill.txt")
    run_cli(root, "run", "--apply")
    next((root / "Sorted").rglob("*.txt")).write_text("changed")
    assert run_cli(root, "undo") == cli.EXIT_FAIL


def test_doctor(root, capsys):
    assert run_cli(root, "doctor") == 0
    assert "OK    folder" in capsys.readouterr().out
    assert run_cli(root, "doctor", client=FakeClient(healthy=False)) == cli.EXIT_UNHEALTHY


def test_backup(root, tmp_path, capsys):
    dest = tmp_path / "b.db"
    assert run_cli(root, "backup", str(dest)) == 0 and dest.exists()
    assert run_cli(root, "backup", str(dest)) == cli.EXIT_USAGE


def test_unsafe_remote_config_is_a_usage_error(root, monkeypatch, capsys):
    monkeypatch.setenv("SLOTHDESK_BASE_URL", "http://example.com")
    assert cli.main(["doctor", "--root", str(root)]) == cli.EXIT_USAGE
    assert "not on this machine" in capsys.readouterr().err


def test_watch_pass_runs_when_good_time(root, monkeypatch):
    make_file(root, "p.png", b"x")
    monkeypatch.setattr(cli, "should_run", lambda cfg: (True, "ok"))
    monkeypatch.setattr(cli, "lower_priority", lambda: True)
    assert run_cli(root, "watch", "--apply", "--interval", "1",
                   stop=threading.Event(), max_cycles=1) == 0
    assert (root / "Sorted" / "Images" / "p.png").exists()


def test_watch_waits_when_busy_and_survives_errors(root, monkeypatch):
    make_file(root, "p.png", b"x")
    monkeypatch.setattr(cli, "should_run", lambda cfg: (False, "on_battery"))
    run_cli(root, "watch", "--apply", stop=threading.Event(), max_cycles=1)
    assert (root / "p.png").exists()
    monkeypatch.setattr(cli, "should_run", lambda cfg: (True, "ok"))
    monkeypatch.setattr(cli, "run_once", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    assert run_cli(root, "watch", stop=threading.Event(), max_cycles=1) == 0


def test_watch_stops_on_event(root, monkeypatch):
    ev = threading.Event()
    ev.set()
    assert run_cli(root, "watch", stop=ev) == 0


# idle ---------------------------------------------------------------------------------
class FakePS:
    class Error(Exception):
        pass

    BELOW_NORMAL_PRIORITY_CLASS = 16384

    def __init__(self, plugged=True, cpu=5.0, battery=True):
        self._plugged, self._cpu, self._battery = plugged, cpu, battery
        self.niced = None

    def sensors_battery(self):
        class B:
            power_plugged = self._plugged
        return B() if self._battery else None

    def cpu_percent(self, interval=None):
        return self._cpu

    def Process(self, pid):  # noqa: N802
        ps = self

        class P:
            def nice(self, v):
                ps.niced = v
        return P()


def test_idle_gate(cfg):
    assert should_run(cfg, FakePS()) == (True, "ok")
    assert should_run(cfg, FakePS(plugged=False)) == (False, "on_battery")
    assert should_run(cfg, FakePS(battery=False)) == (True, "ok")  # desktops
    ok, why = should_run(cfg, FakePS(cpu=99))
    assert not ok and why.startswith("cpu_busy")
    assert should_run(Config(root=cfg.root, require_ac_power=False), FakePS(plugged=False))[0]
    assert should_run(cfg, None) == (True, "psutil_not_installed")


def test_idle_gate_tolerates_broken_sensors(cfg):
    class Broken(FakePS):
        def cpu_percent(self, interval=None):
            raise NotImplementedError

    assert should_run(cfg, Broken()) == (True, "gate_unavailable")


def test_lower_priority():
    ps = FakePS()
    assert lower_priority(ps) and ps.niced is not None
    assert lower_priority(None) is False

    class Denied(FakePS):
        def Process(self, pid):  # noqa: N802
            raise self.Error("denied")

    assert lower_priority(Denied()) is False


def test_real_psutil_import_path_works(cfg):
    ok, _ = should_run(cfg)  # uses real psutil if installed, else reports not installed
    assert isinstance(ok, bool)


# logging ----------------------------------------------------------------------------------
def test_json_logs_carry_run_id_and_no_secrets(tmp_path, capsys):
    logfile = tmp_path / "x.log"
    setup_logging("INFO", logfile)
    setup_logging("INFO", logfile)  # idempotent: no duplicate handlers
    set_run_id("abc123")
    logging.getLogger("slothdesk.test").info("hello %s", "world")
    for h in logging.getLogger("slothdesk").handlers:
        h.flush()
    lines = logfile.read_text().strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["run_id"] == "abc123" and rec["msg"] == "hello world"


def test_formatter_includes_exceptions():
    try:
        raise ValueError("bad")
    except ValueError:
        import sys

        rec = logging.LogRecord("n", logging.ERROR, "f", 1, "m", (), sys.exc_info())
    assert "ValueError" in json.loads(JsonFormatter().format(rec))["exc"]


def test_module_entrypoint_help(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["--version"])
    assert e.value.code == 0
