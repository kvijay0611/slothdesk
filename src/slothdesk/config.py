"""Configuration loading and validation. Fails fast on anything unsafe."""

from __future__ import annotations

import os
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

DEFAULT_CATEGORIES = ("Finance", "Study", "Medical", "Work", "Identity", "Personal", "Misc")
SORTED_DIR = "Sorted"
STATE_DIR = ".slothdesk"
PROVIDERS = ("ollama", "openai")
# Hosts treated as "this machine". host.docker.internal is Docker's bridge to the host.
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "host.docker.internal"})
_CATEGORY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,30}$")
_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")


class ConfigError(ValueError):
    """Raised when configuration is missing, malformed, or unsafe."""


@dataclass(frozen=True)
class Config:
    root: Path
    provider: str = "ollama"
    base_url: str = "http://127.0.0.1:11434"
    model: str = "qwen2.5:1.5b"
    api_key: str = field(default="", repr=False)  # env only; never logged
    allow_remote: bool = False
    timeout_s: float = 180.0
    max_retries: int = 2
    text_chars: int = 1500
    min_confidence: float = 0.6
    min_age_s: int = 120
    max_file_mb: int = 25
    max_files_per_run: int = 200
    require_ac_power: bool = True
    max_cpu_percent: float = 60.0
    ocr: bool = False  # read scanned PDFs and photos of documents locally (needs slothdesk[ocr])
    ocr_pages: int = 2
    categories: tuple[str, ...] = DEFAULT_CATEGORIES
    log_level: str = "INFO"

    def __post_init__(self) -> None:
        _validate(self)

    @property
    def sorted_dir(self) -> Path:
        return self.root / SORTED_DIR

    @property
    def state_dir(self) -> Path:
        return self.root / STATE_DIR

    @property
    def db_path(self) -> Path:
        return self.state_dir / "journal.db"


def _validate(c: Config) -> None:
    if not c.root.is_dir():
        raise ConfigError(f"root folder does not exist: {c.root}")
    resolved = c.root.resolve()
    if resolved == Path(resolved.anchor) or resolved == Path.home().resolve():
        raise ConfigError("refusing to organise a filesystem root or your whole home folder")
    if c.provider not in PROVIDERS:
        raise ConfigError(f"provider must be one of {PROVIDERS}")
    if urlparse(c.base_url).scheme not in ("http", "https") or not urlparse(c.base_url).hostname:
        raise ConfigError("base_url must be an http(s) URL")
    checks = {
        "timeout_s": 0 < c.timeout_s <= 3600,
        "max_retries": 0 <= c.max_retries <= 5,
        "text_chars": 200 <= c.text_chars <= 8000,
        "min_confidence": 0 < c.min_confidence <= 1,
        "min_age_s": c.min_age_s >= 0,
        "max_file_mb": 1 <= c.max_file_mb <= 500,
        "max_files_per_run": 1 <= c.max_files_per_run <= 5000,
        "max_cpu_percent": 1 <= c.max_cpu_percent <= 100,
        "ocr_pages": 1 <= c.ocr_pages <= 10,
    }
    for key, ok in checks.items():
        if not ok:
            raise ConfigError(f"{key} is out of range")
    if not c.categories or len(set(c.categories)) != len(c.categories):
        raise ConfigError("categories must be non-empty and unique")
    if any(not _CATEGORY_RE.match(x) for x in c.categories):
        raise ConfigError("category names must be simple (letters, digits, - and _)")
    if "Misc" not in c.categories:
        raise ConfigError("categories must include 'Misc' (the safe fallback)")
    if c.log_level not in _LOG_LEVELS:
        raise ConfigError(f"log_level must be one of {_LOG_LEVELS}")


_INTS = {"max_retries", "text_chars", "min_age_s", "max_file_mb", "max_files_per_run", "ocr_pages"}
_FLOATS = {"timeout_s", "min_confidence", "max_cpu_percent"}
_BOOLS = {"allow_remote", "require_ac_power", "ocr"}
_ENV = {
    "SLOTHDESK_PROVIDER": "provider",
    "SLOTHDESK_BASE_URL": "base_url",
    "SLOTHDESK_MODEL": "model",
    "SLOTHDESK_API_KEY": "api_key",
    "SLOTHDESK_ALLOW_REMOTE": "allow_remote",
    "SLOTHDESK_TIMEOUT_S": "timeout_s",
    "SLOTHDESK_LOG_LEVEL": "log_level",
    "SLOTHDESK_OCR": "ocr",
}


def _coerce(key: str, value: Any) -> Any:
    try:
        if key in _BOOLS:
            if isinstance(value, bool):
                return value
            s = str(value).strip().lower()
            if s in {"1", "true", "yes", "on"}:
                return True
            if s in {"0", "false", "no", "off"}:
                return False
            raise ValueError(s)
        if key in _INTS:
            return int(value)
        if key in _FLOATS:
            return float(value)
        if key == "categories":
            if isinstance(value, str):
                value = [v.strip() for v in value.split(",") if v.strip()]
            return tuple(str(v) for v in value)
        return str(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"invalid value for {key!r}: {value!r}") from exc


def load_config(
    root: str | Path,
    *,
    config_file: str | Path | None = None,
    env: Mapping[str, str] | None = None,
) -> Config:
    """Load config: defaults < TOML file < environment variables."""
    env = os.environ if env is None else env
    root_path = Path(root).expanduser()
    if not root_path.is_dir():
        raise ConfigError(f"root folder does not exist: {root_path}")
    root_path = root_path.resolve()

    values: dict[str, Any] = {}
    path = Path(config_file) if config_file else root_path / ".slothdesk" / "config.toml"
    if path.is_file():
        try:
            values.update(tomllib.loads(path.read_text("utf-8")))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
            raise ConfigError(f"cannot read config file {path}: {exc}") from exc
    elif config_file:
        raise ConfigError(f"config file not found: {path}")
    if "api_key" in values:
        raise ConfigError("api_key must come from SLOTHDESK_API_KEY, never from a file")

    for env_key, key in _ENV.items():
        if env.get(env_key):
            values[key] = env[env_key]

    allowed = {f.name for f in fields(Config)} - {"root"}
    unknown = set(values) - allowed
    if unknown:
        raise ConfigError(f"unknown config keys: {sorted(unknown)}")
    return Config(root=root_path, **{k: _coerce(k, v) for k, v in values.items()})
