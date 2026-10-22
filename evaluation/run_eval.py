"""Measure how well a model sorts the sample corpus. Run on YOUR machine.

    python -m evaluation.run_eval                       # local Ollama model from your env/config
    python -m evaluation.run_eval --model llama3.2:1b   # compare another small model
    python -m evaluation.run_eval --mock                # smoke test only (no model, not a result)

For a closed-model baseline (optional) point it at an OpenAI-compatible endpoint:
    SLOTHDESK_API_KEY=... SLOTHDESK_ALLOW_REMOTE=true python -m evaluation.run_eval \
        --provider openai --base-url https://api.openai.com/v1 --model <model-name>
This sends the synthetic corpus (not your files) to that provider.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import statistics
import tempfile
import time
from collections import defaultdict
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from slothdesk.classify import Classifier
from slothdesk.config import Config, load_config
from slothdesk.llm import Health, LLMBadResponse, LLMError, LLMResult, make_client

from .corpus import SAMPLES
from .corpus_heldout import SAMPLES as HELDOUT_SAMPLES

_KEYWORDS = {
    "Finance": ("bill", "bank", "invoice", "tax", "credit", "statement", "fund"),
    "Study": ("lecture", "assignment", "exam", "syllabus", "lab record", "paper"),
    "Medical": ("blood", "prescription", "discharge", "vaccin", "dental", "health"),
    "Work": ("sprint", "offer letter", "project status", "resume", "design document",
             "cover letter"),
    "Personal": ("ticket", "birthday", "trip", "recipe", "rental", "wedding"),
    "Identity": ("certificate", "passport", "licence", "identity card", "birth"),
}


class MockClient:
    """Keyword matcher used only to prove the harness works. Its numbers mean nothing."""

    def complete(self, system: str, user: str, schema: dict) -> LLMResult:
        low = user.lower()
        scores = {c: sum(k in low for k in ks) for c, ks in _KEYWORDS.items()}
        best = max(scores, key=lambda c: scores[c])
        out = {"category": best if scores[best] else "Misc", "title": "Sample document",
               "confidence": 0.9, "reason": "keyword match"}
        return LLMResult(json.dumps(out), 0.0, 0)

    def health(self) -> Health:
        return Health(True, "mock")


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, math.ceil(pct / 100 * len(ordered)) - 1)]


def evaluate(cfg: Config, client, samples: list[dict] | None = None) -> dict:
    samples = SAMPLES if samples is None else samples
    clf = Classifier(cfg, client)
    rows = []
    for i, s in enumerate(samples):
        t0 = time.monotonic()
        try:
            decision, calls = clf.classify(f"sample-{i}.txt", s["text"])
            pred, conf, err = decision.category, decision.confidence, None
            tokens = sum(c.tokens for c in calls)
        except LLMBadResponse:
            pred, conf, err, tokens = None, None, "bad_output", 0
        except LLMError as exc:
            pred, conf, err, tokens = None, None, f"unavailable: {exc}", 0
        rows.append({"i": i, "label": s["label"], "pred": pred, "confidence": conf, "error": err,
                     "injection": bool(s.get("injection")), "seconds": time.monotonic() - t0,
                     "tokens": tokens})
    return summarise(cfg, rows)


def summarise(cfg: Config, rows: list[dict]) -> dict:
    normal = [r for r in rows if not r["injection"]]
    inj = [r for r in rows if r["injection"]]
    correct = [r for r in normal if r["pred"] == r["label"]]
    per_cat: dict[str, list[bool]] = defaultdict(list)
    for r in normal:
        per_cat[r["label"]].append(r["pred"] == r["label"])
    secs = [r["seconds"] for r in rows if r["error"] is None]
    # What matters in real use: of the files the tool would actually move (confident enough),
    # how many are right, and how many does it politely leave for you?
    acted = [r for r in normal if r["pred"] is not None and r["confidence"] is not None
             and r["confidence"] >= cfg.min_confidence]
    return {
        "min_confidence": cfg.min_confidence,
        "n_would_move": len(acted),
        "accuracy_when_moving": (sum(r["pred"] == r["label"] for r in acted) / len(acted))
        if acted else 0.0,
        "left_alone_rate": 1 - len(acted) / len(normal) if normal else 0.0,
        "model": cfg.model, "provider": cfg.provider,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "n_documents": len(normal), "accuracy": len(correct) / len(normal) if normal else 0.0,
        "per_category_accuracy": {c: sum(v) / len(v) for c, v in sorted(per_cat.items())},
        "invalid_output_rate": sum(r["error"] == "bad_output" for r in rows) / max(1, len(rows)),
        "unavailable": sum(bool(r["error"] and r["error"].startswith("unavailable")) for r in rows),
        "n_injection": len(inj),
        "injection_resisted": sum(r["pred"] == r["label"] for r in inj),
        "seconds_mean": statistics.fmean(secs) if secs else 0.0,
        "seconds_p95": percentile(secs, 95),
        "tokens_total": sum(r["tokens"] for r in rows),
        "rows": rows,
    }


def render_markdown(res: dict) -> str:
    lines = [
        f"### Results: `{res['model']}` via {res['provider']} ({res.get('set', 'dev')} set)",
        "",
        "| Metric | Value |", "|---|---|",
        f"| Accuracy on {res['n_documents']} documents | {res['accuracy']:.0%} |",
        f"| Files it would move (confidence >= {res.get('min_confidence', 0):.2f}) "
        f"| {res.get('n_would_move', 0)} of {res['n_documents']} |",
        f"| Accuracy on the files it would move | {res.get('accuracy_when_moving', 0):.0%} |",
        f"| Files it would leave for you | {res.get('left_alone_rate', 0):.0%} |",
        f"| Prompt-injection attempts resisted | "
        f"{res['injection_resisted']} of {res['n_injection']} |",
        f"| Invalid model outputs (after 1 retry) | {res['invalid_output_rate']:.0%} |",
        f"| Mean seconds per file | {res['seconds_mean']:.1f} |",
        f"| 95th percentile seconds | {res['seconds_p95']:.1f} |",
        f"| Tokens used | {res['tokens_total']} |",
        "", "Per-category accuracy: "
        + ", ".join(f"{c} {a:.0%}" for c, a in res["per_category_accuracy"].items()),
        "", f"_Small sample (synthetic, n={res['n_documents'] + res['n_injection']}): "
        "treat as a sanity check, not a benchmark._",
    ]
    if res["unavailable"]:
        lines.append(
            f"\n**Warning:** {res['unavailable']} call(s) failed: the model was unavailable."
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--provider")
    ap.add_argument("--model")
    ap.add_argument("--base-url")
    ap.add_argument("--mock", action="store_true", help="smoke test with a fake keyword model")
    ap.add_argument("--set", choices=("dev", "heldout"), default="dev",
                    help="dev = used while tuning; heldout = untouched set for the final number")
    ap.add_argument("--out", default="evaluation/results")
    args = ap.parse_args(argv)

    env = dict(os.environ)
    for flag, key in (("provider", "SLOTHDESK_PROVIDER"), ("model", "SLOTHDESK_MODEL"),
                      ("base_url", "SLOTHDESK_BASE_URL")):
        if getattr(args, flag):
            env[key] = getattr(args, flag)
    with tempfile.TemporaryDirectory() as tmp:
        cfg = replace(load_config(tmp, env=env), root=Path(tmp))
        client = MockClient() if args.mock else make_client(cfg)
        if not args.mock:
            health = client.health()
            print(health.detail)
            if not health.ok:
                return 3
        res = evaluate(cfg, client, HELDOUT_SAMPLES if args.set == "heldout" else SAMPLES)
        res["set"] = args.set
    if args.mock:
        res["model"] = "MOCK (not a real result)"
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^\w.-]+", "_", res["model"])
    path = out_dir / f"{safe}-{args.set}-{datetime.now(UTC):%Y%m%d-%H%M%S}.json"
    path.write_text(json.dumps(res, indent=2), encoding="utf-8")
    print(render_markdown(res))
    print(f"\nRaw results saved to {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
