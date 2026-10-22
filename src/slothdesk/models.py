"""Plain data objects shared by the engine, journal and report."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class Item:
    src: str  # path relative to the organised folder
    status: str  # planned | moved | skipped | failed
    reason: str | None = None
    dest: str | None = None
    category: str | None = None
    source: str | None = None  # rule | llm | cache
    confidence: float | None = None


@dataclass
class RunSummary:
    run_id: str
    mode: str  # plan | apply
    started_at: str
    finished_at: str = ""
    items: list[Item] = field(default_factory=list)
    llm_calls: int = 0
    llm_seconds: float = 0.0
    llm_tokens: int = 0
    cache_hits: int = 0
    deferred: int = 0
    stopped_reason: str | None = None

    def counts(self) -> dict[str, int]:
        return dict(Counter(i.status for i in self.items))

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("items")
        d["counts"] = self.counts()
        return d
