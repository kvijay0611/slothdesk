"""OCR flow, prompt hints, stats and model comparison."""

import json
import sys
from dataclasses import replace

import pytest
from conftest import FakeClient, make_file

from evaluation import compare, run_eval
from evaluation.corpus import SAMPLES
from slothdesk import cli, extract, ocr
from slothdesk.classify import CATEGORY_HINTS, PROMPT_VERSION, system_prompt
from slothdesk.config import DEFAULT_CATEGORIES, ConfigError, load_config
from slothdesk.engine import run_once

CERT = "Community certificate issued by the Tahsildar. Certificate number 4471 and date of issue."
CERT_REPLY = {"category": "Identity", "title": "Community certificate", "confidence": 0.9,
              "reason": "an official certificate"}


# ---- prompt ---------------------------------------------------------------
def test_prompt_explains_every_default_folder_and_version_bumped():
    s = system_prompt(DEFAULT_CATEGORIES)
    for cat in DEFAULT_CATEGORIES:
        assert f"- {cat}:" in s and cat in CATEGORY_HINTS
    assert "untrusted" in s and PROMPT_VERSION != "v1"  # old cached decisions are not reused


def test_custom_category_without_hint_is_still_listed():
    s = system_prompt(("Hobbies", "Misc"))
    assert "- Hobbies" in s and "- Misc:" in s


# ---- extract with OCR (OCR engine faked: fast and offline) --------------------
def test_scanned_pdf_uses_ocr_only_when_enabled(tmp_path, monkeypatch):
    p = tmp_path / "scan.pdf"
    p.write_bytes(b"%PDF-1.4 not really")
    monkeypatch.setattr(extract, "_pdf_text", lambda path: None)
    monkeypatch.setattr(ocr, "ocr_pdf", lambda path, pages: CERT)
    assert extract.extract_text(p, 500) is None
    assert extract.extract_text(p, 500, ocr=True) == CERT


def test_real_pdf_text_does_not_trigger_ocr(tmp_path, monkeypatch):
    p = tmp_path / "text.pdf"
    p.write_bytes(b"%PDF")
    monkeypatch.setattr(extract, "_pdf_text", lambda path: CERT)

    def boom(*a):
        raise AssertionError("OCR must not run when the PDF already has text")

    monkeypatch.setattr(ocr, "ocr_pdf", boom)
    assert extract.extract_text(p, 500, ocr=True) == CERT


def test_photo_text_needs_enough_characters(tmp_path, monkeypatch):
    p = tmp_path / "photo.jpg"
    p.write_bytes(b"x")
    monkeypatch.setattr(ocr, "ocr_image", lambda path: "EXIT")  # a sign in a holiday photo
    assert extract.extract_text(p, 500, ocr=True) is None
    monkeypatch.setattr(ocr, "ocr_image", lambda path: CERT)
    assert extract.extract_text(p, 500, ocr=True) == CERT
    assert extract.extract_text(p, 500) is None  # OCR off: images are never read


# ---- engine ---------------------------------------------------------------
def test_photo_of_certificate_is_read_and_filed_by_meaning(cfg, journal, monkeypatch):
    make_file(cfg.root, "IMG_001.jpg", b"jpgbytes")
    monkeypatch.setattr(ocr, "ocr_image", lambda path: CERT)
    s = run_once(replace(cfg, ocr=True), FakeClient(reply=lambda a, b: CERT_REPLY), journal,
                 apply=True)
    assert s.counts() == {"moved": 1}
    moved = list((cfg.sorted_dir / "Identity").iterdir())
    assert len(moved) == 1 and moved[0].suffix == ".jpg"
    assert "Community-certificate" in moved[0].name


def test_photo_without_text_is_still_just_an_image(cfg, journal, monkeypatch):
    make_file(cfg.root, "beach.jpg", b"jpgbytes")
    monkeypatch.setattr(ocr, "ocr_image", lambda path: None)
    client = FakeClient()
    s = run_once(replace(cfg, ocr=True), client, journal, apply=True)
    assert s.counts() == {"moved": 1} and client.calls == 0  # no model call for a plain photo
    assert (cfg.sorted_dir / "Images" / "beach.jpg").exists()


def test_without_ocr_images_never_reach_the_ocr_engine(cfg, journal, monkeypatch):
    make_file(cfg.root, "beach.jpg", b"jpgbytes")

    def boom(*a):
        raise AssertionError("OCR must stay off by default")

    monkeypatch.setattr(ocr, "ocr_image", boom)
    assert run_once(cfg, FakeClient(), journal, apply=True).counts() == {"moved": 1}


def test_scanned_pdf_is_no_longer_skipped_with_ocr(cfg, journal, monkeypatch):
    make_file(cfg.root, "scan001.pdf", b"%PDF-1.4 scan")
    monkeypatch.setattr(extract, "_pdf_text", lambda path: None)
    monkeypatch.setattr(ocr, "ocr_pdf", lambda path, pages: CERT)
    plain = run_once(cfg, FakeClient(), journal, apply=False)
    assert reasons_of(plain) == {"scan001.pdf": "no_text"}
    s = run_once(replace(cfg, ocr=True), FakeClient(reply=lambda a, b: CERT_REPLY), journal,
                 apply=False)
    assert s.counts() == {"planned": 1}


def reasons_of(summary):
    return {i.src: i.reason for i in summary.items}


# ---- ocr module (engine faked, so these run without the optional packages) -----------
def test_ocr_returns_none_instead_of_crashing_on_bad_files(tmp_path, monkeypatch):
    bad = tmp_path / "broken.png"
    bad.write_bytes(b"not an image")
    assert ocr.ocr_image(bad) is None
    badpdf = tmp_path / "broken.pdf"
    badpdf.write_bytes(b"not a pdf")
    assert ocr.ocr_pdf(badpdf, 2) is None


def test_available_is_false_when_packages_missing(monkeypatch):
    monkeypatch.setitem(sys.modules, "rapidocr_onnxruntime", None)
    assert ocr.available() is False


@pytest.mark.skipif(not ocr.available(), reason="optional OCR packages not installed")
def test_real_ocr_reads_text_from_an_image(tmp_path):
    from PIL import Image, ImageDraw, ImageFont

    font = None
    for name in ("DejaVuSans.ttf", "arial.ttf", "Arial.ttf", "LiberationSans-Regular.ttf"):
        try:
            font = ImageFont.truetype(name, 40)
            break
        except OSError:
            continue
    if font is None:
        pytest.skip("no TrueType font available to draw test text")
    img = Image.new("RGB", (1000, 220), "white")
    draw = ImageDraw.Draw(img)
    draw.text((20, 30), "COMMUNITY CERTIFICATE", fill="black", font=font)
    draw.text((20, 110), "Certificate number 4471 issued by the Tahsildar", fill="black", font=font)
    p = tmp_path / "c.png"
    img.save(p)
    text = ocr.ocr_image(p)
    assert text is not None and "CERTIFICATE" in text.upper().replace(" ", "")


@pytest.mark.skipif(not ocr.available(), reason="optional OCR packages not installed")
def test_real_pdf_render_path_runs(tmp_path):
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument.new()
    pdf.new_page(300, 300)
    p = tmp_path / "blank.pdf"
    pdf.save(str(p))
    assert ocr.ocr_pdf(p, 1) is None  # blank page: no text, no crash


# ---- config / cli -------------------------------------------------------------
def test_ocr_flag_from_env_and_bad_pages(root):
    assert load_config(root, env={"SLOTHDESK_OCR": "yes"}).ocr is True
    assert load_config(root, env={}).ocr is False
    with pytest.raises(ConfigError):
        replace(load_config(root, env={}), ocr_pages=0).__post_init__()


def test_cli_ocr_flag_fails_clearly_when_not_installed(root, monkeypatch, capsys):
    monkeypatch.setattr(ocr, "available", lambda: False)
    rc = cli.main(["run", "--root", str(root), "--ocr"], client_factory=lambda c: FakeClient())
    assert rc == cli.EXIT_USAGE and "slothdesk[ocr]" in capsys.readouterr().err


def test_doctor_mentions_ocr(root, monkeypatch, capsys):
    monkeypatch.setattr(ocr, "available", lambda: False)
    cli.main(["doctor", "--root", str(root)], client_factory=lambda c: FakeClient())
    assert "OCR missing" in capsys.readouterr().out


# ---- stats ------------------------------------------------------------------
def test_stats_empty_then_after_a_run(cfg, journal, capsys):
    empty = journal.stats()
    assert empty["moved"] == 0 and empty["by_folder"] == {}
    assert "nothing yet" in cli.render_stats(empty, 30)

    make_file(cfg.root, "bill.txt")
    make_file(cfg.root, "photo.png", b"\x89PNG")
    make_file(cfg.root, "empty.pdf", b"%PDF-1.4")
    run_once(cfg, FakeClient(), journal, apply=True)
    s = journal.stats()
    assert s["moved"] == 2 and s["runs"] == 1 and s["llm_calls"] == 1
    assert s["by_folder"] == {"Finance": 1, "Images": 1}
    text = cli.render_stats(s, 60)
    assert "Files sorted:   2" in text and "about 2 min" in text and "Finance 1" in text


def test_stats_counts_undone_and_dry_runs_do_not_count(cfg, journal):
    from slothdesk.engine import undo_run

    make_file(cfg.root, "bill.txt")
    run_once(cfg, FakeClient(), journal, apply=False)
    after_preview = journal.stats()
    assert after_preview["runs"] == 0 and after_preview["llm_calls"] == 1  # preview did the work
    run_once(cfg, FakeClient(), journal, apply=True)
    undo_run(cfg, journal)
    s = journal.stats()
    assert s["moved"] == 0 and s["undone"] == 1


def test_stats_command_and_validation(root, capsys):
    f = lambda c: FakeClient()  # noqa: E731
    assert cli.main(["stats", "--root", str(root)], client_factory=f) == cli.EXIT_OK
    assert "lazy payoff" in capsys.readouterr().out
    rc = cli.main(["stats", "--root", str(root), "--seconds-per-file", "-5"], client_factory=f)
    assert rc == cli.EXIT_USAGE


# ---- evaluation extras -----------------------------------------------------------
def test_eval_reports_accuracy_when_moving_and_left_alone(cfg):
    samples = [s for s in SAMPLES if not s.get("injection")][:6]
    labels = [s["label"] for s in samples]
    calls = iter(range(6))

    def reply(system, user):
        i = next(calls)
        if i < 3:  # confident and right
            return {"category": labels[i], "title": "t", "confidence": 0.9, "reason": "r"}
        wrong = "Misc" if labels[i] != "Misc" else "Work"  # unsure and wrong
        return {"category": wrong, "title": "t", "confidence": 0.2, "reason": "r"}

    res = run_eval.evaluate(cfg, FakeClient(reply=reply), samples)
    assert res["accuracy"] == 0.5
    assert res["n_would_move"] == 3 and res["accuracy_when_moving"] == 1.0
    assert res["left_alone_rate"] == 0.5
    md = run_eval.render_markdown(res)
    assert "Accuracy on the files it would move | 100%" in md and "leave for you | 50%" in md


def test_compare_table_ignores_mock_and_sorts_by_accuracy(tmp_path):
    def write(name, model, acc, provider="ollama"):
        (tmp_path / name).write_text(json.dumps({
            "model": model, "provider": provider, "accuracy": acc, "seconds_mean": 4.2,
            "injection_resisted": 2, "n_injection": 3, "accuracy_when_moving": 0.9,
            "n_would_move": 20, "left_alone_rate": 0.3}))

    write("a.json", "small", 0.5)
    write("b.json", "better", 0.8)
    write("c.json", "MOCK (not a real result)", 0.99)
    write("d.json", "big-remote", 0.95, provider="openai")
    (tmp_path / "junk.json").write_text("{not json")
    rows = compare.load_results(tmp_path)
    assert [r["model"] for r in rows] == ["small", "better", "big-remote"]
    table = compare.render_table(rows)
    order = [line.split("|")[1].strip() for line in table.splitlines()[2:]]
    assert order == ["big-remote", "better", "small"] and "remote (openai)" in table
    assert "No results" in compare.render_table([])
    assert compare.main(["--dir", str(tmp_path)]) == 0


# ---- held-out set and prompt examples: guard against data leakage ----------------------
def test_heldout_set_is_balanced_and_does_not_leak():
    from evaluation.corpus_heldout import SAMPLES as HELD
    from slothdesk.classify import EXAMPLES

    normal = [s for s in HELD if not s.get("injection")]
    assert len(normal) == 36 and sum(bool(s.get("injection")) for s in HELD) == 3
    for cat in ("Finance", "Study", "Medical", "Work", "Identity", "Personal"):
        assert sum(s["label"] == cat for s in normal) == 6
    dev_texts = {s["text"] for s in SAMPLES}
    held_texts = {s["text"] for s in HELD}
    example_texts = {text for text, _ in EXAMPLES}
    assert not dev_texts & held_texts  # the two test sets share nothing
    assert not example_texts & (dev_texts | held_texts)  # prompt examples are in neither set


def test_prompt_examples_only_use_configured_categories():
    s = system_prompt(("Study", "Misc"))
    assert "-> Study" in s and "-> Misc" in s and "-> Finance" not in s


def test_eval_set_flag_and_compare_shows_the_set(tmp_path, capsys):
    assert run_eval.main(["--mock", "--set", "heldout", "--out", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "heldout set" in out
    saved = json.loads(next(tmp_path.glob("*heldout*.json")).read_text())
    assert saved["set"] == "heldout" and saved["n_documents"] == 36
    saved["model"] = "real-model"  # compare ignores MOCK runs
    (tmp_path / "real.json").write_text(json.dumps(saved))
    assert "| real-model | heldout |" in compare.render_table(compare.load_results(tmp_path))
