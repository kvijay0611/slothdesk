"""The morning report: what happened overnight, in plain Markdown."""

from __future__ import annotations

import json
from pathlib import Path

from .config import Config
from .journal import Journal


def _esc(s: str | None) -> str:
    return (s or "").replace("|", "\\|").replace("`", "'").replace("\n", " ")


def render_report(journal: Journal, run_id: str) -> str:
    run = journal.get_run(run_id)
    if run is None:
        raise KeyError(run_id)
    rows = journal.moves_for_run(run_id)
    summary = json.loads(run["summary_json"] or "{}")
    moved = [r for r in rows if r["status"] in ("moved", "undone")]
    review = [r for r in rows if r["status"] == "skipped"]
    failed = [r for r in rows if r["status"] == "failed"]
    calls = summary.get("llm_calls", 0)
    avg = (summary.get("llm_seconds", 0.0) / calls) if calls else 0.0
    lines = [
        f"# Sloth Desk report ({run['started_at'][:10]})",
        "",
        f"Run `{run_id}` with model `{run['model']}`. "
        f"Moved **{len(moved)}**, need your eyes **{len(review)}**, failed **{len(failed)}**.",
        "",
        f"Model work: {calls} call(s), average {avg:.1f}s each, "
        f"{summary.get('llm_tokens', 0)} tokens, {summary.get('cache_hits', 0)} cache hit(s). "
        "Cost: 0 (runs on this computer).",
    ]
    if summary.get("stopped_reason"):
        lines += ["", f"Note: {_esc(summary['stopped_reason'])}"]
    if moved:
        lines += ["", "## Moved", "", "| From | To | How | Confidence |", "|---|---|---|---|"]
        for r in moved:
            conf = f"{r['confidence']:.2f}" if r["confidence"] is not None else ""
            lines.append(f"| {_esc(r['src'])} | {_esc(r['dest'])} | {_esc(r['source'])} | {conf} |")
    if review:
        lines += ["", "## Needs your eyes (left where they were)", ""]
        lines += [f"- {_esc(r['src'])}: {_esc(r['reason'])}" for r in review]
    if failed:
        lines += ["", "## Failed (will be retried next run)", ""]
        lines += [f"- {_esc(r['src'])}: {_esc(r['detail'])}" for r in failed]
    lines += ["", f"To undo this run: `slothdesk undo --run {run_id}`", ""]
    return "\n".join(lines)


def write_report(cfg: Config, journal: Journal, run_id: str) -> Path:
    out_dir = cfg.sorted_dir / "_Reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    run = journal.get_run(run_id)
    day = run["started_at"][:10] if run else "unknown"
    path = out_dir / f"{day}-{run_id}.md"
    path.write_text(render_report(journal, run_id), encoding="utf-8")
    return path
