"""Command line interface. Dry-run is the default; nothing moves without --apply."""

from __future__ import annotations

import argparse
import contextlib
import logging
import os
import signal
import sys
import threading
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from . import __version__
from . import ocr as ocr_mod
from .config import Config, ConfigError, load_config
from .engine import run_once, undo_run
from .idle import lower_priority, should_run
from .journal import Journal, JournalError
from .llm import LLMClient, make_client
from .lock import AlreadyRunning, SingleInstance
from .logging_setup import setup_logging
from .models import RunSummary
from .report import render_report

log = logging.getLogger("slothdesk.cli")
EXIT_OK, EXIT_FAIL, EXIT_USAGE, EXIT_UNHEALTHY = 0, 1, 2, 3
ClientFactory = Callable[[Config], LLMClient]


def _parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", default=str(Path.home() / "Downloads"),
                        help="folder to tidy (default: ~/Downloads)")
    common.add_argument("--config", help="path to a TOML config file")
    common.add_argument("-v", "--verbose", action="store_true")
    common.add_argument("--ocr", action="store_true",
                        help="also read scanned PDFs and photos of documents (local OCR)")

    p = argparse.ArgumentParser(prog="slothdesk", description=__doc__)
    p.add_argument("--version", action="version", version=f"slothdesk {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("doctor", parents=[common], help="check setup and model health")
    r = sub.add_parser("run", parents=[common], help="tidy once (dry run unless --apply)")
    r.add_argument("--apply", action="store_true", help="actually move files")
    w = sub.add_parser("watch", parents=[common], help="tidy repeatedly, politely")
    w.add_argument("--apply", action="store_true")
    w.add_argument("--interval", type=int, default=3600, help="seconds between passes")
    u = sub.add_parser("undo", parents=[common], help="reverse a run")
    u.add_argument("--run", dest="run_id", help="run id (default: most recent applied run)")
    rep = sub.add_parser("report", parents=[common], help="print a run's report")
    rep.add_argument("--run", dest="run_id")
    pg = sub.add_parser("purge", parents=[common], help="delete old history (never your files)")
    pg.add_argument("--days", type=int, required=True)
    st = sub.add_parser("stats", parents=[common], help="what SlothDesk has done for you so far")
    st.add_argument("--seconds-per-file", type=int, default=30,
                    help="your own estimate of hand-sorting time per file (default 30)")
    b = sub.add_parser("backup", parents=[common], help="back up the journal database")
    b.add_argument("dest")
    return p


def _print_summary(s: RunSummary) -> None:
    verb = {"plan": "Would move", "apply": "Moved"}[s.mode]
    for i in s.items:
        if i.status in ("planned", "moved"):
            print(f"{verb}: {i.src} -> {i.dest} [{i.category}, {i.source}, {i.confidence:.2f}]")
        elif i.status in ("skipped", "failed"):
            print(f"{i.status.capitalize()}: {i.src} ({i.reason})")
    print(f"Run {s.run_id} ({s.mode}): {s.counts() or 'nothing to do'}"
          + (f" - {s.stopped_reason}" if s.stopped_reason else ""))
    if s.mode == "plan" and any(i.status == "planned" for i in s.items):
        print("Dry run only. Re-run with --apply to move files.")


def render_stats(s: dict[str, Any], seconds_per_file: int) -> str:
    folders = ", ".join(f"{k} {v}" for k, v in s["by_folder"].items()) or "nothing yet"
    left = ", ".join(f"{k} {v}" for k, v in s["left_for_you"].items()) or "nothing"
    per_call = s["llm_seconds"] / s["llm_calls"] if s["llm_calls"] else 0.0
    saved_min = s["moved"] * seconds_per_file / 60
    return "\n".join([
        "SlothDesk lazy payoff",
        f"  Applied runs:   {s['runs']}",
        f"  Files sorted:   {s['moved']} ({s['undone']} undone)",
        f"  Sorted into:    {folders}",
        f"  Left for you:   {sum(s['left_for_you'].values())} ({left})",
        f"  Model time:     {s['llm_seconds']:.0f} s over {s['llm_calls']} calls "
        f"({per_call:.1f} s per call, previews included)",
        f"  Time saved:     about {saved_min:.0f} min = {s['moved']} files x {seconds_per_file} s "
        "by hand (your own estimate; change it with --seconds-per-file)",
    ])


def _cmd_doctor(cfg: Config, client: LLMClient, journal: Journal) -> int:
    ok = True
    print(f"OK    folder: {cfg.root}")
    print(f"OK    journal: {cfg.db_path}")
    health = client.health()
    print(f"{'OK   ' if health.ok else 'FAIL '} model: {health.detail}")
    ok &= health.ok
    for mod, hint in (("psutil", "idle/battery gating: pip install 'slothdesk[idle]'"),
                      ("pypdf", "PDF support: pip install 'slothdesk[pdf]'")):
        try:
            __import__(mod)
            print(f"OK    {mod} installed")
        except ImportError:
            print(f"INFO  {mod} missing ({hint})")
    if ocr_mod.available():
        print("OK    OCR installed (add --ocr to read scanned PDFs and photos of documents)")
    else:
        print("INFO  OCR missing (scanned files are skipped: pip install 'slothdesk[ocr]')")
    return EXIT_OK if ok else EXIT_UNHEALTHY


def _cmd_watch(cfg: Config, client: LLMClient, journal: Journal, apply: bool, interval: int,
               stop: threading.Event, max_cycles: int | None = None) -> int:
    cycles = 0
    while not stop.is_set():
        ok, why = should_run(cfg)
        if ok:
            lower_priority()
            try:
                s = run_once(cfg, client, journal, apply=apply, stop=stop,
                             gate=lambda: should_run(cfg))
                log.info("pass complete: %s", s.counts())
            except Exception:  # noqa: BLE001  # a long-running watcher must survive one bad pass
                log.exception("pass failed; will retry next interval")
        else:
            log.info("not a good time (%s); sleeping", why)
        cycles += 1
        if max_cycles is not None and cycles >= max_cycles:
            break
        stop.wait(interval)
    return EXIT_OK


def _make_output_safe() -> None:
    """Windows consoles often cannot encode every filename; never crash while printing."""
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(AttributeError, ValueError, OSError):
            stream.reconfigure(errors="replace")  # type: ignore[union-attr]


def main(argv: Sequence[str] | None = None, *, client_factory: ClientFactory | None = None,
         stop: threading.Event | None = None, max_cycles: int | None = None) -> int:
    _make_output_safe()
    args = _parser().parse_args(argv)
    try:
        env = dict(os.environ)
        if args.ocr:
            env["SLOTHDESK_OCR"] = "1"
        cfg = load_config(args.root, config_file=args.config, env=env)
        if cfg.ocr and not ocr_mod.available():
            print("error: OCR was requested but is not installed. Run: "
                  "pip install \"slothdesk[ocr]\"", file=sys.stderr)
            return EXIT_USAGE
        setup_logging("DEBUG" if args.verbose else cfg.log_level, cfg.state_dir / "slothdesk.log")
        client = (client_factory or make_client)(cfg)
        journal = Journal(cfg.db_path)
    except (ConfigError, JournalError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    needs_lock = args.cmd in ("watch", "undo") or (args.cmd == "run" and args.apply)
    try:
        if needs_lock:
            with SingleInstance(cfg.state_dir / "slothdesk.lock"):
                return _dispatch(args, cfg, client, journal, stop, max_cycles)
        return _dispatch(args, cfg, client, journal, stop, max_cycles)
    except AlreadyRunning as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAIL
    finally:
        journal.close()


def _dispatch(args: Any, cfg: Config, client: LLMClient, journal: Journal,
              stop: threading.Event | None, max_cycles: int | None) -> int:
    cmd = args.cmd
    if cmd == "doctor":
        return _cmd_doctor(cfg, client, journal)
    if cmd == "run":
        _print_summary(run_once(cfg, client, journal, apply=args.apply))
        return EXIT_OK
    if cmd == "watch":
        ev = stop or threading.Event()
        if stop is None:
            for sig in (signal.SIGINT, signal.SIGTERM):
                with contextlib.suppress(ValueError, OSError):  # not on main thread / unsupported
                    signal.signal(sig, lambda *_: ev.set())
        return _cmd_watch(cfg, client, journal, args.apply, args.interval, ev, max_cycles)
    if cmd == "stats":
        if args.seconds_per_file < 0:
            print("error: --seconds-per-file must not be negative", file=sys.stderr)
            return EXIT_USAGE
        print(render_stats(journal.stats(), args.seconds_per_file))
        return EXIT_OK
    if cmd == "undo":
        res = undo_run(cfg, journal, args.run_id)
        print(f"Undone: {res['undone']}, could not undo: {res['failed']}")
        return EXIT_OK if res["failed"] == 0 else EXIT_FAIL
    if cmd == "report":
        rid = args.run_id or journal.last_apply_run_id()
        if rid is None or journal.get_run(rid) is None:
            print("error: no such run", file=sys.stderr)
            return EXIT_USAGE
        print(render_report(journal, rid))
        return EXIT_OK
    if cmd == "purge":
        if args.days < 1:
            print("error: --days must be at least 1", file=sys.stderr)
            return EXIT_USAGE
        print(f"Removed {journal.purge_older_than(args.days)} old record(s)")
        return EXIT_OK
    if cmd == "backup":
        try:
            journal.backup_to(Path(args.dest))
        except JournalError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_USAGE
        print(f"Journal backed up to {args.dest}")
        return EXIT_OK
    return EXIT_USAGE  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
