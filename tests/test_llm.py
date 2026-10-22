import json

import pytest

from slothdesk import llm
from slothdesk.config import Config, ConfigError
from slothdesk.llm import (
    CircuitBreaker,
    LLMBadResponse,
    LLMUnavailable,
    OllamaClient,
    OpenAICompatClient,
    make_client,
)

OK = {"message": {"content": '{"category":"Misc"}'}, "eval_count": 5, "prompt_eval_count": 7}
SCHEMA = {"type": "object"}


def client_for(root, url, cls=OllamaClient, **kw):
    cfg = Config(root=root, base_url=url, **{"max_retries": 2, "timeout_s": 5, **kw})
    return cls(cfg, sleep=lambda s: None)


def test_ollama_success_sends_structured_request(root, fake_server):
    url, script = fake_server([(200, OK, 0)])
    res = client_for(root, url).complete("sys", "usr", SCHEMA)
    assert res.content == '{"category":"Misc"}' and res.tokens == 12
    method, path, body, _ = script.requests[0]
    payload = json.loads(body)
    assert (method, path) == ("POST", "/api/chat")
    assert payload["format"] == SCHEMA and payload["options"]["temperature"] == 0
    assert payload["stream"] is False


def test_retries_transient_errors_then_succeeds(root, fake_server):
    url, script = fake_server([(500, {}, 0), (503, {}, 0), (200, OK, 0)])
    assert client_for(root, url).complete("s", "u", SCHEMA).tokens == 12
    assert len(script.requests) == 3


def test_gives_up_after_retries(root, fake_server):
    url, script = fake_server([(500, {}, 0)])
    with pytest.raises(LLMUnavailable):
        client_for(root, url, max_retries=1).complete("s", "u", SCHEMA)
    assert len(script.requests) == 2


def test_client_errors_are_not_retried(root, fake_server):
    url, script = fake_server([(404, {}, 0)])
    with pytest.raises(LLMBadResponse):
        client_for(root, url).complete("s", "u", SCHEMA)
    assert len(script.requests) == 1


def test_circuit_opens_then_probes(root, fake_server):
    url, script = fake_server([(500, {}, 0)])
    now = [0.0]
    breaker = CircuitBreaker(threshold=1, reset_after=60, clock=lambda: now[0])
    cfg = Config(root=root, base_url=url, max_retries=0)
    c = OllamaClient(cfg, sleep=lambda s: None, breaker=breaker)
    with pytest.raises(LLMUnavailable):
        c.complete("s", "u", SCHEMA)
    before = len(script.requests)
    with pytest.raises(LLMUnavailable, match="circuit open"):
        c.complete("s", "u", SCHEMA)
    assert len(script.requests) == before  # no request while open
    now[0] = 61  # half-open: one probe allowed
    with pytest.raises(LLMUnavailable):
        c.complete("s", "u", SCHEMA)
    assert len(script.requests) == before + 1


def test_breaker_resets_on_success():
    b = CircuitBreaker(threshold=2)
    b.failure()
    b.success()
    b.failure()
    assert b.allow()


def test_timeout_is_enforced(root, fake_server):
    url, _ = fake_server([(200, OK, 1.0)])
    with pytest.raises(LLMUnavailable):
        client_for(root, url, timeout_s=0.2, max_retries=0).complete("s", "u", SCHEMA)


def test_oversized_and_malformed_responses(root, fake_server, monkeypatch):
    monkeypatch.setattr(llm, "MAX_RESPONSE_BYTES", 50)
    url, _ = fake_server([(200, {"message": {"content": "x" * 500}}, 0)])
    with pytest.raises(LLMBadResponse, match="too large"):
        client_for(root, url).complete("s", "u", SCHEMA)
    monkeypatch.undo()
    for payload in (b"not json", b"[1,2]", {"message": {}}, {"message": "str"}):
        url, _ = fake_server([(200, payload, 0)])
        with pytest.raises(LLMBadResponse):
            client_for(root, url).complete("s", "u", SCHEMA)


@pytest.mark.parametrize("url,allow", [
    ("http://example.com", False), ("http://example.com", True), ("http://10.0.0.5:11434", False)])
def test_remote_endpoints_refused_by_default(root, url, allow):
    with pytest.raises(ConfigError):
        OllamaClient(Config(root=root, base_url=url, allow_remote=allow))


def test_remote_https_allowed_only_when_opted_in(root):
    OllamaClient(Config(root=root, base_url="https://api.example.com", allow_remote=True))
    for host in ("localhost", "127.0.0.1", "host.docker.internal"):
        assert llm.endpoint_is_local(f"http://{host}:11434")
    assert not llm.endpoint_is_local("http://localhost.evil.com")


def test_health_variants(root, fake_server):
    url, _ = fake_server([(200, {"models": [{"name": "qwen2.5:1.5b"}]}, 0)])
    assert client_for(root, url).health().ok
    url, _ = fake_server([(200, {"models": [{"name": "other:latest"}]}, 0)])
    h = client_for(root, url).health()
    assert not h.ok and "ollama pull" in h.detail
    url, _ = fake_server([(200, {"models": [{"name": "m:latest"}]}, 0)])
    assert client_for(root, url, model="m").health().ok
    h = client_for(root, "http://127.0.0.1:9", max_retries=0, timeout_s=1).health()
    assert not h.ok and "cannot reach" in h.detail


def test_openai_compat_and_auth_header(root, fake_server):
    body = {"choices": [{"message": {"content": "{}"}}], "usage": {"total_tokens": 9}}
    url, script = fake_server([(200, body, 0)])
    c = client_for(root, url + "/v1", OpenAICompatClient, api_key="k123")
    res = c.complete("s", "u", SCHEMA)
    assert res.tokens == 9
    assert script.requests[0][1] == "/v1/chat/completions"
    assert script.requests[0][3]["Authorization"] == "Bearer k123"
    assert c.health().ok
    url, _ = fake_server([(200, {"choices": []}, 0)])
    with pytest.raises(LLMBadResponse):
        client_for(root, url, OpenAICompatClient).complete("s", "u", SCHEMA)
    url, _ = fake_server([(200, {"choices": [{"message": {"content": None}}]}, 0)])
    with pytest.raises(LLMBadResponse):
        client_for(root, url, OpenAICompatClient).complete("s", "u", SCHEMA)
    assert not client_for(root, "http://127.0.0.1:9", OpenAICompatClient,
                          max_retries=0, timeout_s=1).health().ok


def test_make_client_picks_provider(root):
    assert isinstance(make_client(Config(root=root)), OllamaClient)
    assert isinstance(make_client(Config(root=root, provider="openai")), OpenAICompatClient)


def test_non_http_urls_are_refused_at_the_call_site(root):
    c = client_for(root, "http://127.0.0.1:9")
    with pytest.raises(LLMBadResponse, match="non-http"):
        c._once("GET", "file:///etc/passwd", None)
