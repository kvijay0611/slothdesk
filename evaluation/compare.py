"""Compare saved evaluation runs side by side (handy for your write-up).

    python -m evaluation.compare                 # every real result in evaluation/results
    python -m evaluation.compare --dir some/folder

Mock runs are ignored. Run `python -m evaluation.run_eval --model <name>` once per model first.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def load_results(folder: Path) -> list[dict]:
    rows = []
    for p in sorted(folder.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict) or "accuracy" not in data:
            continue
        if not str(data.get("model", "")).startswith("MOCK"):
            rows.append(data)
    return rows


def render_table(rows: list[dict]) -> str:
    if not rows:
        return "No results found. Run: python -m evaluation.run_eval --model <name>"
    head = (
        "| Model | Test set | Where it runs | Accuracy | Accuracy when it moves | Left for you "
        "| Seconds/file | Injection resisted |\n|---|---|---|---|---|---|---|---|"
    )
    lines = [head]
    for r in sorted(rows, key=lambda r: -r["accuracy"]):
        where = "local" if r.get("provider") == "ollama" else f"remote ({r.get('provider')})"
        lines.append(
            f"| {r['model']} | {r.get('set', 'dev')} | {where} | {r['accuracy']:.0%} "
            f"| {r.get('accuracy_when_moving', 0):.0%} ({r.get('n_would_move', 0)} files) "
            f"| {r.get('left_alone_rate', 0):.0%} | {r['seconds_mean']:.1f} "
            f"| {r['injection_resisted']}/{r['n_injection']} |"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--dir", default="evaluation/results")
    args = ap.parse_args(argv)
    print(render_table(load_results(Path(args.dir))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
