"""Optional, fully local OCR for scanned PDFs and photos of documents.

Everything here runs on your CPU with open-source libraries (RapidOCR + ONNX Runtime,
PDFium for rendering PDF pages). No image or text is sent anywhere. Install with:
    pip install "slothdesk[ocr]"
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

log = logging.getLogger("slothdesk.ocr")

IMAGE_EXT = frozenset({".jpg", ".jpeg", ".png", ".bmp", ".webp"})
MAX_PIXELS = 25_000_000  # refuse absurdly large images instead of exhausting memory
MAX_SIDE = 2000  # downscale for speed on a slow CPU
PDF_RENDER_SCALE = 1.5
MIN_CONFIDENCE = 0.5
MIN_USEFUL_CHARS = 40

_engine: Any = None


def available() -> bool:
    """True if the optional OCR packages are installed."""
    try:
        import numpy  # noqa: F401
        import PIL  # noqa: F401
        import pypdfium2  # noqa: F401
        import rapidocr_onnxruntime  # noqa: F401
    except ImportError:
        return False
    return True


def is_ocr_image(path: Path) -> bool:
    return path.suffix.lower() in IMAGE_EXT


def _get_engine() -> Any:
    global _engine
    if _engine is None:
        from rapidocr_onnxruntime import RapidOCR

        _engine = RapidOCR()
    return _engine


def _read_pil(img: Any) -> str:
    import numpy as np

    img = img.convert("RGB")
    img.thumbnail((MAX_SIDE, MAX_SIDE))
    result, _ = _get_engine()(np.asarray(img))
    if not result:
        return ""
    lines = []
    for _box, text, score in result:
        try:
            if float(score) >= MIN_CONFIDENCE:
                lines.append(str(text))
        except (TypeError, ValueError):
            continue
    return " ".join(lines)


def ocr_image(path: Path) -> str | None:
    try:
        from PIL import Image

        with Image.open(path) as img:
            if img.width * img.height > MAX_PIXELS:
                log.warning("image too large for OCR; skipping")
                return None
            img.load()
            return _read_pil(img) or None
    except Exception:  # noqa: BLE001  # decoders of untrusted images may raise anything
        log.warning("OCR failed on an image; skipping it")
        return None


def ocr_pdf(path: Path, max_pages: int) -> str | None:
    pdf = None
    try:
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(str(path))
        parts: list[str] = []
        for i in range(min(len(pdf), max_pages)):
            page = pdf[i]
            try:
                parts.append(_read_pil(page.render(scale=PDF_RENDER_SCALE).to_pil()))
            finally:
                page.close()
        return " ".join(p for p in parts if p) or None
    except Exception:  # noqa: BLE001  # malformed or encrypted PDFs raise assorted errors
        log.warning("OCR failed on a PDF; skipping it")
        return None
    finally:
        if pdf is not None:
            pdf.close()
