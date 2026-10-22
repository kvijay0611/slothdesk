"""Text extraction with hard limits. Treats every file as untrusted input."""

from __future__ import annotations

import hashlib
import html
import logging
import re
import zipfile
from pathlib import Path

from . import ocr as ocr_mod

log = logging.getLogger("slothdesk.extract")
logging.getLogger("pypdf").setLevel(logging.ERROR)  # quiet harmless 'malformed PDF' chatter

TEXT_EXT = frozenset({".txt", ".md", ".csv", ".tsv", ".log", ".json"})
SUPPORTED_EXT = TEXT_EXT | {".docx", ".pdf"}
MAX_READ_BYTES = 64 * 1024
MAX_DOCX_MEMBER = 5 * 1024 * 1024
_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"[ \t\r\f\v]+")
_BLANKS = re.compile(r"\n{3,}")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_head(path: Path) -> str:
    with path.open("rb") as fh:
        return fh.read(MAX_READ_BYTES).decode("utf-8", errors="replace")


def _docx_text(path: Path) -> str:
    with zipfile.ZipFile(path) as zf:
        info = zf.getinfo("word/document.xml")
        if info.file_size > MAX_DOCX_MEMBER:
            raise ValueError("docx body too large")
        with zf.open(info) as member:
            xml = member.read(MAX_DOCX_MEMBER + 1).decode("utf-8", errors="replace")
    if len(xml) > MAX_DOCX_MEMBER:
        raise ValueError("docx body too large")
    xml = xml.replace("</w:p>", "\n")
    return html.unescape(_TAG.sub("", xml))


def _pdf_text(path: Path) -> str | None:
    try:
        from pypdf import PdfReader
    except ImportError:
        log.info("pypdf not installed; PDFs are skipped (pip install 'slothdesk[pdf]')")
        return None
    try:
        reader = PdfReader(str(path))
        if reader.is_encrypted:
            return None
        return "\n".join((page.extract_text() or "") for page in reader.pages[:2])
    except Exception:  # noqa: BLE001  # parser of untrusted input may raise anything
        log.warning("could not read a PDF; skipping it")
        return None


def _clean(raw: str, max_chars: int) -> str | None:
    if not raw:
        return None
    if raw.count("\ufffd") > 0.3 * len(raw):  # looks binary
        return None
    text = _CTRL.sub(" ", raw)
    text = "\n".join(_SPACE.sub(" ", line).strip() for line in text.splitlines())
    text = _BLANKS.sub("\n\n", text).strip()
    return text[:max_chars] or None


def _useful(text: str | None) -> str | None:
    return text if text and len(text) >= ocr_mod.MIN_USEFUL_CHARS else None


def extract_text(
    path: Path, max_chars: int, *, ocr: bool = False, ocr_pages: int = 2
) -> str | None:
    """Return up to ``max_chars`` of cleaned text, or None if nothing usable.

    With ``ocr=True`` (needs ``slothdesk[ocr]``), scanned PDFs and photos of documents are
    read locally with OCR instead of being skipped.
    """
    ext = path.suffix.lower()
    try:
        if ext in TEXT_EXT:
            raw: str | None = _read_head(path)
        elif ext == ".docx":
            raw = _docx_text(path)
        elif ext == ".pdf":
            raw = _pdf_text(path)
        elif ocr and ocr_mod.is_ocr_image(path):
            return _useful(_clean(ocr_mod.ocr_image(path) or "", max_chars))
        else:
            return None
    except (OSError, zipfile.BadZipFile, KeyError, ValueError, RuntimeError):
        log.warning("could not extract text from a %s file", ext)
        return None
    cleaned = _clean(raw or "", max_chars)
    if ocr and ext == ".pdf" and _useful(cleaned) is None:
        scanned = _useful(_clean(ocr_mod.ocr_pdf(path, ocr_pages) or "", max_chars))
        return scanned or cleaned
    return cleaned
