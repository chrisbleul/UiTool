"""PDF text extraction and image OCR for the engine's `read_pdf` and
`ocr_image` actions. Kept separate from engine.py so they're independently
unit-testable, matching excel.py's precedent.

OCR requires the Tesseract OCR *binary* to be installed and on PATH - pytesseract
is only a wrapper around it. If it's missing, pytesseract raises
TesseractNotFoundError with a clear message; the engine wraps that like any
other backend failure (see engine._run_ocr_image) rather than trying to detect
or install it."""

from __future__ import annotations


def _parse_page_spec(spec: str | None, page_count: int) -> list[int]:
    """Parses "1,3-5" into 0-based page indices. None/"" means all pages."""
    if not spec:
        return list(range(page_count))
    indices: list[int] = []
    for chunk in spec.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "-" in chunk:
            start, end = chunk.split("-", 1)
            indices.extend(range(int(start) - 1, int(end)))
        else:
            indices.append(int(chunk) - 1)
    return [i for i in indices if 0 <= i < page_count]


def read_pdf_text(path: str, pages: str | None = None) -> str:
    from pypdf import PdfReader

    reader = PdfReader(path)
    selected = _parse_page_spec(pages, len(reader.pages))
    return "\n".join(reader.pages[i].extract_text() or "" for i in selected)


def ocr_image_text(path: str, lang: str = "eng") -> str:
    import pytesseract
    from PIL import Image

    with Image.open(path) as image:
        return pytesseract.image_to_string(image, lang=lang or "eng")
