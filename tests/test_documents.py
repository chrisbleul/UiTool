import pytest

from uiflow.documents import _parse_page_spec, ocr_image_text, read_pdf_text


def test_parse_page_spec_none_means_all_pages():
    assert _parse_page_spec(None, 3) == [0, 1, 2]


def test_parse_page_spec_single_and_range():
    assert _parse_page_spec("1,3-4", 5) == [0, 2, 3]


def test_parse_page_spec_drops_out_of_range_indices():
    assert _parse_page_spec("1,9", 2) == [0]


@pytest.fixture
def pdf_path(tmp_path):
    pypdf = pytest.importorskip("pypdf")
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.add_blank_page(width=200, height=200)
    path = tmp_path / "doc.pdf"
    with open(path, "wb") as f:
        writer.write(f)
    return path


def test_read_pdf_text_on_blank_pages_returns_empty_string(pdf_path):
    # Blank pages have no text layer - this exercises the extraction path
    # end-to-end without needing a real text-bearing fixture PDF.
    assert read_pdf_text(str(pdf_path)) == "\n"


def test_read_pdf_text_missing_file_raises(tmp_path):
    with pytest.raises(Exception):
        read_pdf_text(str(tmp_path / "missing.pdf"))


def test_ocr_image_text_calls_pytesseract(monkeypatch, tmp_path):
    from PIL import Image

    path = tmp_path / "img.png"
    Image.new("RGB", (10, 10), color="white").save(path)

    monkeypatch.setattr("pytesseract.image_to_string", lambda image, lang=None: f"OCR:{lang}")

    assert ocr_image_text(str(path), lang="deu") == "OCR:deu"
