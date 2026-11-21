"""Tests for document text extraction."""

from __future__ import annotations

import io

import pytest

from app.rag.extractors import UnsupportedFileTypeError, extract


class TestPlainText:
    def test_extracts_utf8(self):
        result = extract("notes.txt", b"Hello world")
        assert result.text == "Hello world"
        assert result.mime_type == "text/plain"
        assert result.warning is None

    def test_extracts_urdu(self):
        text = "چھٹی کا نظام"
        result = extract("urdu.txt", text.encode("utf-8"))
        assert result.text == text

    def test_markdown_gets_markdown_mime(self):
        assert extract("readme.md", b"# Title").mime_type == "text/markdown"

    def test_falls_back_to_cp1252(self):
        """Files from older Windows tools are often cp1252, not UTF-8."""
        data = "café".encode("cp1252")
        result = extract("legacy.txt", data)
        assert "caf" in result.text
        assert result.warning is not None

    def test_undecodable_bytes_are_replaced_not_fatal(self):
        result = extract("binary.txt", b"\xff\xfe\x00\x01")
        assert isinstance(result.text, str)
        assert result.warning is not None


class TestUnsupported:
    @pytest.mark.parametrize("filename", ["image.png", "archive.zip", "sheet.xlsx", "noextension"])
    def test_unsupported_extension_raises(self, filename):
        with pytest.raises(UnsupportedFileTypeError):
            extract(filename, b"data")

    def test_error_message_lists_supported_types(self):
        with pytest.raises(UnsupportedFileTypeError) as exc:
            extract("file.xyz", b"data")
        assert ".pdf" in str(exc.value)
        assert ".docx" in str(exc.value)


class TestPdf:
    def test_invalid_pdf_raises_value_error(self):
        with pytest.raises(ValueError):
            extract("broken.pdf", b"this is not a pdf at all")

    def test_real_pdf_is_extracted(self):
        """Build a minimal PDF with pypdf and read it back."""
        pytest.importorskip("pypdf")
        from pypdf import PdfWriter

        writer = PdfWriter()
        writer.add_blank_page(width=200, height=200)
        buffer = io.BytesIO()
        writer.write(buffer)

        result = extract("blank.pdf", buffer.getvalue())
        assert result.mime_type == "application/pdf"
        # A blank page has no text, and the extractor must say so rather than
        # silently returning an empty string.
        assert result.warning is not None
        assert "OCR" in result.warning or "no text" in result.warning.lower()


class TestDocx:
    def test_real_docx_is_extracted(self):
        docx = pytest.importorskip("docx")
        document = docx.Document()
        document.add_paragraph("The refund window is thirty days.")
        document.add_paragraph("Contact support for details.")
        buffer = io.BytesIO()
        document.save(buffer)

        result = extract("policy.docx", buffer.getvalue())
        assert "thirty days" in result.text
        assert "Contact support" in result.text
        assert result.warning is None

    def test_docx_tables_are_included(self):
        """Tables hold a lot of the useful content in real documents."""
        docx = pytest.importorskip("docx")
        document = docx.Document()
        table = document.add_table(rows=2, cols=2)
        table.cell(0, 0).text = "Amount"
        table.cell(0, 1).text = "Approver"
        table.cell(1, 0).text = "50000"
        table.cell(1, 1).text = "Finance director"
        buffer = io.BytesIO()
        document.save(buffer)

        result = extract("table.docx", buffer.getvalue())
        assert "Finance director" in result.text
        assert "|" in result.text

    def test_invalid_docx_raises_value_error(self):
        with pytest.raises(ValueError):
            extract("broken.docx", b"not a real docx")
