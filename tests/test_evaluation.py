import json

from conftest import FakeClient

from evaluation import run_eval
from evaluation.corpus import SAMPLES
from slothdesk.llm import LLMUnavailable


def test_corpus_is_balanced_and_labelled():
    normal = [s for s in SAMPLES if not s.get("injection")]
    assert len(normal) == 36 and sum(s.get("injection", False) for s in SAMPLES) == 3
    for cat in ("Finance", "Study", "Medical", "Work", "Identity", "Personal"):
        assert sum(s["label"] == cat for s in normal) == 6


def test_mock_run_end_to_end(tmp_path, capsys):
    assert run_eval.main(["--mock", "--out", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "Accuracy on 36 documents" in out and "sanity check" in out
    saved = json.loads(next(tmp_path.glob("*.json")).read_text())
    assert saved["model"].startswith("MOCK") and saved["accuracy"] > 0.8


def test_evaluate_counts_failures(cfg):
    res = run_eval.evaluate(cfg, FakeClient(reply=lambda s, u: "garbage"), SAMPLES[:4])
    assert res["invalid_output_rate"] == 1.0 and res["accuracy"] == 0.0
    res = run_eval.evaluate(cfg, FakeClient(error=LLMUnavailable("down")), SAMPLES[:2])
    assert res["unavailable"] == 2
    assert "Warning" in run_eval.render_markdown(res)


def test_percentile_edges():
    assert run_eval.percentile([], 95) == 0.0
    assert run_eval.percentile([1, 2, 3, 4], 95) == 4
    assert run_eval.percentile([5], 50) == 5


def test_main_fails_cleanly_when_model_unreachable(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("SLOTHDESK_BASE_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("SLOTHDESK_TIMEOUT_S", "1")
    assert run_eval.main(["--out", str(tmp_path)]) == 3
