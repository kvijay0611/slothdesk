"""LLM clients (Ollama native API, plus any OpenAI-compatible server).

Reliability features: timeouts, retries with exponential backoff, a circuit breaker,
response-size limits, and a guard that keeps your data on this machine by default.
"""

from __future__ import annotations

import json
import logging
import random
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlparse

from .config import LOCAL_HOSTS, Config, ConfigError

log = logging.getLogger("slothdesk.llm")
MAX_RESPONSE_BYTES = 1_000_000


class LLMError(Exception):
    """Base class for model-call failures."""


class LLMUnavailable(LLMError):  # noqa: N818
    """The model server cannot be reached (down, timing out, or circuit open)."""


class LLMBadResponse(LLMError):
    """The server answered, but the answer is unusable."""


@dataclass(frozen=True)
class LLMResult:
    content: str
    latency_s: float
    tokens: int


@dataclass(frozen=True)
class Health:
    ok: bool
    detail: str


class LLMClient(Protocol):
    def complete(self, system: str, user: str, schema: dict[str, Any]) -> LLMResult: ...
    def health(self) -> Health: ...


class CircuitBreaker:
    """Stops hammering a failing server; allows one probe after a cool-down."""

    def __init__(
        self,
        threshold: int = 3,
        reset_after: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.threshold = threshold
        self.reset_after = reset_after
        self._clock = clock
        self._failures = 0
        self._opened_at: float | None = None

    def allow(self) -> bool:
        if self._opened_at is None:
            return True
        return self._clock() - self._opened_at >= self.reset_after  # half-open probe

    def success(self) -> None:
        self._failures = 0
        self._opened_at = None

    def failure(self) -> None:
        self._failures += 1
        if self._failures >= self.threshold:
            self._opened_at = self._clock()


class _Retryable(Exception):  # noqa: N818
    pass


def endpoint_is_local(url: str) -> bool:
    return (urlparse(url).hostname or "") in LOCAL_HOSTS


class _HTTPClient:
    def __init__(
        self,
        cfg: Config,
        *,
        sleep: Callable[[float], None] = time.sleep,
        breaker: CircuitBreaker | None = None,
    ) -> None:
        if not endpoint_is_local(cfg.base_url):
            if not cfg.allow_remote:
                raise ConfigError(
                    "model endpoint is not on this machine; set SLOTHDESK_ALLOW_REMOTE=true "
                    "to send file text to it (this defeats the privacy goal)"
                )
            if urlparse(cfg.base_url).scheme != "https":
                raise ConfigError("remote model endpoints must use https")
        self.cfg = cfg
        self._base = cfg.base_url.rstrip("/")
        self._sleep = sleep
        self.breaker = breaker or CircuitBreaker()

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.cfg.api_key:
            h["Authorization"] = f"Bearer {self.cfg.api_key}"
        return h

    def _once(self, method: str, url: str, payload: dict[str, Any] | None) -> dict[str, Any]:
        if urlparse(url).scheme not in ("http", "https"):  # defence in depth: no file:// etc.
            raise LLMBadResponse("refusing non-http(s) URL")
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(  # noqa: S310
            url, data=data, headers=self._headers(), method=method
        )
        try:
            with urllib.request.urlopen(  # noqa: S310  # nosec B310 - scheme checked above
                req, timeout=self.cfg.timeout_s
            ) as resp:
                raw = resp.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            if exc.code >= 500 or exc.code in (408, 429):
                raise _Retryable(f"http {exc.code}") from exc
            raise LLMBadResponse(f"http {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            raise _Retryable(type(exc).__name__) from exc
        if len(raw) > MAX_RESPONSE_BYTES:
            raise LLMBadResponse("response too large")
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise LLMBadResponse("response is not JSON") from exc
        if not isinstance(parsed, dict):
            raise LLMBadResponse("response is not a JSON object")
        return parsed

    def _request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if not self.breaker.allow():
            raise LLMUnavailable("circuit open: model server recently failed")
        url = self._base + path
        last = "unknown"
        for attempt in range(self.cfg.max_retries + 1):
            try:
                result = self._once(method, url, payload)
            except _Retryable as exc:
                last = str(exc)
                log.warning("model call failed (attempt %d): %s", attempt + 1, last)
                if attempt < self.cfg.max_retries:
                    jitter = random.uniform(0, 0.25)  # noqa: S311  # nosec B311 - retry jitter only
                    self._sleep(min(2**attempt, 30) + jitter)
                continue
            self.breaker.success()
            return result
        self.breaker.failure()
        raise LLMUnavailable(f"model server unavailable ({last})")


class OllamaClient(_HTTPClient):
    def complete(self, system: str, user: str, schema: dict[str, Any]) -> LLMResult:
        payload = {
            "model": self.cfg.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "stream": False,
            "format": schema,  # structured output: constrains the model to our JSON schema
            "keep_alive": "10m",
            "options": {"temperature": 0, "seed": 0, "num_predict": 150, "num_ctx": 2048},
        }
        t0 = time.monotonic()
        data = self._request("POST", "/api/chat", payload)
        msg = data.get("message")
        content = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(content, str):
            raise LLMBadResponse("missing message.content")
        tokens = int(data.get("eval_count") or 0) + int(data.get("prompt_eval_count") or 0)
        return LLMResult(content, time.monotonic() - t0, tokens)

    def health(self) -> Health:
        try:
            data = self._request("GET", "/api/tags")
        except LLMError as exc:
            return Health(False, f"cannot reach Ollama at {self._base}: {exc}")
        names = [m.get("name", "") for m in data.get("models", []) if isinstance(m, dict)]
        want = self.cfg.model
        if want in names or f"{want}:latest" in names:
            return Health(True, f"Ollama reachable, model '{want}' is installed")
        return Health(
            False, f"Ollama reachable but model '{want}' missing; run: ollama pull {want}"
        )


class OpenAICompatClient(_HTTPClient):
    def complete(self, system: str, user: str, schema: dict[str, Any]) -> LLMResult:
        del schema  # json_object mode is the portable subset; we validate the output ourselves
        payload = {
            "model": self.cfg.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0,
            "max_tokens": 150,
            "response_format": {"type": "json_object"},
        }
        t0 = time.monotonic()
        data = self._request("POST", "/chat/completions", payload)
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMBadResponse("unexpected chat completion shape") from exc
        if not isinstance(content, str):
            raise LLMBadResponse("empty completion")
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        return LLMResult(content, time.monotonic() - t0, int(usage.get("total_tokens") or 0))

    def health(self) -> Health:
        try:
            self._request("GET", "/models")
        except LLMError as exc:
            return Health(False, f"cannot reach server at {self._base}: {exc}")
        return Health(True, "server reachable")


def make_client(cfg: Config, **kwargs: Any) -> LLMClient:
    cls = OllamaClient if cfg.provider == "ollama" else OpenAICompatClient
    return cls(cfg, **kwargs)
