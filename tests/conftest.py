from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from slothdesk.config import Config
from slothdesk.journal import Journal
from slothdesk.llm import Health, LLMResult

GOOD = {"category": "Finance", "title": "Electricity bill", "confidence": 0.9, "reason": "a bill"}


class FakeClient:
    """Stands in for Ollama so tests are fast, offline and deterministic."""

    def __init__(self, reply=None, error=None, healthy=True):
        self.reply = reply or (lambda system, user: GOOD)
        self.error = error
        self.healthy = healthy
        self.calls = 0
        self.prompts: list[str] = []

    def complete(self, system, user, schema):
        self.calls += 1
        self.prompts.append(user)
        if self.error:
            raise self.error
        out = self.reply(system, user)
        return LLMResult(out if isinstance(out, str) else json.dumps(out), 0.01, 10)

    def health(self):
        return Health(self.healthy, "fake")


def make_file(root, name, content=b"Payment due for electricity.", age=3600):
    p = root / name
    p.write_bytes(content if isinstance(content, bytes) else content.encode())
    t = time.time() - age
    os.utime(p, (t, t))
    return p


@pytest.fixture
def root(tmp_path):
    d = tmp_path / "Downloads"
    d.mkdir()
    return d


@pytest.fixture
def cfg(root):
    return Config(root=root, min_age_s=0)


@pytest.fixture
def journal(cfg):
    j = Journal(cfg.db_path)
    yield j
    j.close()


class _Handler(BaseHTTPRequestHandler):
    def _serve(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n)
        script = self.server.script
        script.requests.append((self.command, self.path, body, dict(self.headers)))
        r = script.responses
        status, payload, delay = r.pop(0) if len(r) > 1 else r[0]
        time.sleep(delay)
        data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    do_GET = do_POST = _serve

    def log_message(self, *a):
        pass


class Script:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []


@pytest.fixture
def fake_server():
    servers = []

    def start(responses):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        srv.script = Script(responses)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        return f"http://127.0.0.1:{srv.server_address[1]}", srv.script

    yield start
    for s in servers:
        s.shutdown()
        s.server_close()
