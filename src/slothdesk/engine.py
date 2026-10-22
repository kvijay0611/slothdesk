"""The orchestrator: scan, decide, (optionally) move, journal. One bad file never stops a run."""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from .classify import PROMPT_VERSION, Classifier, Decision
from .config import Config
from .extract import extract_text, sha256_file
from .journal import Journal
from .llm import LLMBadResponse, LLMClient, LLMResult, LLMUnavailable
from .logging_setup import set_run_id
from .models import Item, RunSummary, utc_now
from .ocr import is_ocr_image
from .organizer import destination_for, is_inside, name_rule, rule_decision, safe_move
from .report import write_report
from .scanner import scan_folder

log = logging.getLogger("slothdesk.engine")
Gate = Callable[[], tuple[bool, str]]
# Skips worth showing the person in the morning report (others are transient noise).
_RECORDED_SKIPS = {"low_confidence", "no_text", "bad_model_output", "too_large"}


def _rel(cfg: Config, p: Path) -> str:
    return p.relative_to(cfg.root).as_posix()


def reconcile_pending(cfg: Config, journal: Journal) -> int:
    """Crash recovery: settle moves that were journaled but whose outcome is unknown."""
    fixed = 0
    for row in journal.pending_moves():
        src, dest = cfg.root / row["src"], cfg.root / (row["dest"] or "")
        if row["dest"] and dest.is_file() and not src.exists():
            journal.update_move(row["id"], status="moved", detail="recovered_after_interruption")
        elif src.exists() and not (row["dest"] and dest.exists()):
            journal.update_move(row["id"], status="failed", detail="interrupted_before_move")
        else:
            journal.update_move(row["id"], status="failed", detail="inconsistent_state")
        fixed += 1
    if fixed:
        log.warning("reconciled %d interrupted move(s)", fixed)
    return fixed


class _Run:
    def __init__(self, cfg: Config, client: LLMClient, journal: Journal, summary: RunSummary,
                 apply: bool) -> None:
        self.cfg, self.journal, self.summary, self.apply = cfg, journal, summary, apply
        self.classifier = Classifier(cfg, client)
        self.llm_down = False
        self.taken: set[str] = set()

    def _count(self, calls: list[LLMResult]) -> None:
        self.summary.llm_calls += len(calls)
        self.summary.llm_seconds += sum(c.latency_s for c in calls)
        self.summary.llm_tokens += sum(c.tokens for c in calls)

    def _skip(self, rel: str, reason: str, decision: Decision | None = None) -> Item:
        item = Item(rel, "skipped", reason,
                    category=decision.category if decision else None,
                    source=decision.source if decision else None,
                    confidence=decision.confidence if decision else None)
        if self.apply and reason in _RECORDED_SKIPS:
            self.journal.add_move(self.summary.run_id, rel, "skipped", reason=reason,
                                  category=item.category, confidence=item.confidence,
                                  source=item.source, model=self.cfg.model)
        return item

    def process(self, path: Path) -> Item:
        cfg = self.cfg
        rel = _rel(cfg, path)
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return Item(rel, "skipped", "vanished")
        # With OCR on, photos are read first; a picture with no text is just a picture.
        # File-name hints first, then extension rules; with OCR on, photos are read before filing.
        decision = name_rule(path, cfg.categories) or (
            None if (cfg.ocr and is_ocr_image(path)) else rule_decision(path)
        )
        sha: str | None = None
        if decision is None:
            text = extract_text(path, cfg.text_chars, ocr=cfg.ocr, ocr_pages=cfg.ocr_pages)
            if text is None:
                decision = rule_decision(path)
                if decision is None:
                    return self._skip(rel, "no_text")
            else:
                sha = sha256_file(path)
                cached = self.journal.get_decision(sha, cfg.model, PROMPT_VERSION)
                if cached is not None:
                    decision = cached
                    self.summary.cache_hits += 1
                elif self.llm_down:
                    return Item(rel, "skipped", "llm_unavailable")
                else:
                    try:
                        decision, calls = self.classifier.classify(path.name, text)
                    except LLMUnavailable:
                        self.llm_down = True
                        log.warning("model unavailable; remaining files wait for the next run")
                        return Item(rel, "skipped", "llm_unavailable")
                    except LLMBadResponse:
                        return self._skip(rel, "bad_model_output")
                    self._count(calls)
                    self.journal.put_decision(sha, cfg.model, PROMPT_VERSION, decision)
        if decision.source != "rule" and decision.confidence < cfg.min_confidence:
            return self._skip(rel, "low_confidence", decision)

        dest = destination_for(cfg, path, decision, self.taken, mtime)
        base = Item(rel, "planned", dest=_rel(cfg, dest), category=decision.category,
                    source=decision.source, confidence=decision.confidence,
                    reason=decision.reason or None)
        if not self.apply:
            return base

        sha = sha or sha256_file(path)
        move_id = self.journal.add_move(
            self.summary.run_id, rel, "pending", dest=base.dest, sha256=sha,
            category=decision.category, title=decision.title, confidence=decision.confidence,
            source=decision.source, reason=decision.reason, model=cfg.model,
            prompt_version=PROMPT_VERSION)
        try:
            safe_move(path, dest)
        except FileExistsError:
            self.journal.update_move(move_id, status="failed", detail="destination_exists")
            return replace(base, status="failed", reason="destination_exists")
        except OSError as exc:  # e.g. file locked by another program on Windows
            self.journal.update_move(move_id, status="failed", detail=type(exc).__name__)
            return replace(base, status="failed", reason=type(exc).__name__)
        self.journal.update_move(move_id, status="moved")
        return replace(base, status="moved")


def run_once(
    cfg: Config,
    client: LLMClient,
    journal: Journal,
    *,
    apply: bool,
    stop: threading.Event | None = None,
    gate: Gate | None = None,
    now: float | None = None,
) -> RunSummary:
    """One pass over the folder. ``apply=False`` is a dry run that moves nothing."""
    now = time.time() if now is None else now
    run_id = uuid.uuid4().hex[:12]
    set_run_id(run_id)
    summary = RunSummary(run_id, "apply" if apply else "plan", utc_now())
    journal.start_run(run_id, summary.mode, cfg.model)
    status = "ok"
    try:
        if apply:
            reconcile_pending(cfg, journal)
        scan = scan_folder(cfg, now)
        summary.items.extend(scan.skipped)
        summary.deferred = scan.deferred
        if scan.deferred:
            summary.stopped_reason = "max_files_per_run reached; the rest wait for the next run"
        run = _Run(cfg, client, journal, summary, apply)
        for path in scan.candidates:
            if stop is not None and stop.is_set():
                summary.stopped_reason = "stopped"
                break
            if gate is not None:
                ok, why = gate()
                if not ok:
                    summary.stopped_reason = f"paused: {why}"
                    break
            try:
                summary.items.append(run.process(path))
            except Exception:  # noqa: BLE001  # isolate failures: one bad file must not end the run
                log.exception("unexpected error while processing a file")
                summary.items.append(Item(_rel(cfg, path), "failed", "unexpected_error"))
        if run.llm_down:
            summary.stopped_reason = (summary.stopped_reason or "") + " model unavailable"
            summary.stopped_reason = summary.stopped_reason.strip()
    except BaseException:
        status = "error"
        log.exception("run failed")
        raise
    finally:
        summary.finished_at = utc_now()
        journal.finish_run(run_id, status, summary.to_dict())
    log.info("run finished: %s", summary.counts())
    if apply and any(i.status in ("moved", "failed") or i.reason in _RECORDED_SKIPS
                     for i in summary.items):
        try:
            write_report(cfg, journal, run_id)
        except OSError:
            log.warning("could not write the morning report")
    return summary


def undo_run(cfg: Config, journal: Journal, run_id: str | None = None) -> dict[str, int]:
    """Put files from a run back where they were. Refuses if anything changed since."""
    run_id = run_id or journal.last_apply_run_id()
    if run_id is None:
        return {"undone": 0, "failed": 0}
    out = {"undone": 0, "failed": 0}
    for row in reversed(journal.moves_for_run(run_id)):
        if row["status"] != "moved":
            continue
        src, dest = cfg.root / row["src"], cfg.root / row["dest"]
        reason = None
        if not (is_inside(cfg.root, src) and is_inside(cfg.sorted_dir, dest)):
            reason = "path_outside_folder"
        elif not dest.is_file():
            reason = "file_missing"
        elif row["sha256"] and sha256_file(dest) != row["sha256"]:
            reason = "modified_since_move"
        elif src.exists():
            reason = "original_path_occupied"
        if reason is None:
            try:
                safe_move(dest, src)
            except OSError as exc:
                reason = type(exc).__name__
        if reason is None:
            journal.update_move(row["id"], status="undone")
            out["undone"] += 1
        else:
            journal.update_move(row["id"], detail=f"undo_failed:{reason}")
            out["failed"] += 1
            log.warning("could not undo one move: %s", reason)
    return out
