import zipfile

from slothdesk import extract
from slothdesk.extract import extract_text, sha256_file


def test_plain_text_is_cleaned_and_truncated(tmp_path):
    p = tmp_path / "a.txt"
    p.write_text("Hello\x00   world\n\n\n\n\nsecond   line " + "x" * 5000)
    out = extract_text(p, 300)
    assert out is not None and len(out) <= 300 and "\x00" not in out
    assert out.startswith("Hello world")


def test_binary_and_unknown_and_empty(tmp_path):
    b = tmp_path / "b.txt"
    b.write_bytes(bytes(range(128, 256)) * 50)
    assert extract_text(b, 500) is None
    e = tmp_path / "e.txt"
    e.write_text("")
    assert extract_text(e, 500) is None
    assert extract_text(tmp_path / "x.unknown", 500) is None
    assert extract_text(tmp_path / "gone.txt", 500) is None


def _docx(path, body):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", body)


def test_docx_text_and_entities(tmp_path):
    p = tmp_path / "d.docx"
    _docx(p, "<w:p><w:t>Tom &amp; Jerry</w:t></w:p><w:p><w:t>Chapter two</w:t></w:p>")
    out = extract_text(p, 500)
    assert out == "Tom & Jerry\nChapter two"


def test_docx_bomb_and_corrupt_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(extract, "MAX_DOCX_MEMBER", 1000)
    p = tmp_path / "bomb.docx"
    _docx(p, "<w:t>" + "A" * 5000 + "</w:t>")
    assert extract_text(p, 500) is None
    bad = tmp_path / "bad.docx"
    bad.write_bytes(b"not a zip")
    assert extract_text(bad, 500) is None
    nodoc = tmp_path / "nodoc.docx"
    with zipfile.ZipFile(nodoc, "w") as z:
        z.writestr("other.xml", "x")
    assert extract_text(nodoc, 500) is None


def test_pdf_without_library_or_garbage(tmp_path, monkeypatch):
    p = tmp_path / "x.pdf"
    p.write_bytes(b"%PDF-1.4 garbage")
    assert extract_text(p, 500) is None  # corrupt PDF must not crash
    import builtins

    real = builtins.__import__

    def fake(name, *a, **k):
        if name == "pypdf":
            raise ImportError
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake)
    assert extract_text(p, 500) is None


def test_sha256(tmp_path):
    p = tmp_path / "h.txt"
    p.write_bytes(b"abc")
    assert sha256_file(p) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
