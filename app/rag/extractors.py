"""Extract plain text from uploaded files.

Every extractor returns text plus a note about anything it could not read.
That note is surfaced in the API response rather than swallowed: a PDF that
was mostly scanned images extracts to almost nothing, and a user who is told
"12 characters" can work out why their question gets no answer.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

SUPPORTED_EXTENSIONS = {".txt", ".md", ".markdown", ".pdf", ".docx"}


@dataclass(slots=True)
class Extraction:
    text: str
    mime_type: str
    warning: str | None = None


class UnsupportedFileTypeError(ValueError):
    """Raised for a file whose extension we cannot read."""


def _extension(filename: str) -> str:
    lowered = filename.lower()
    dot = lowered.rfind(".")
    return lowered[dot:] if dot != -1 else ""


def _decode_text(data: bytes) -> tuple[str, str | None]:
    """Decode bytes as text, trying UTF-8 first.

    UTF-8 is the right default and covers Urdu. The fallback exists because
    files exported from older Windows tools are often cp1252, and refusing to
    read them would be unhelpful.
    """
    try:
        return data.decode("utf-8"), None
    except UnicodeDecodeError:
        pass

    try:
        return data.decode("cp1252"), "File was not valid UTF-8; decoded as Windows-1252."
    except UnicodeDecodeError:
        return data.decode("utf-8", errors="replace"), (
            "File contained bytes that are not valid text; unreadable characters were replaced."
        )


def _extract_pdf(data: bytes) -> Extraction:
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception as exc:  # pypdf raises a variety of errors on bad input
        raise ValueError(f"Could not read PDF: {exc}") from exc

    pages: list[str] = []
    empty_pages = 0
    for page in reader.pages:
        try:
            page_text = page.extract_text() or ""
        except Exception:
            page_text = ""
        if page_text.strip():
            pages.append(page_text)
        else:
            empty_pages += 1

    text = "\n\n".join(pages)
    warning = None
    if empty_pages:
        warning = (
            f"{empty_pages} of {len(reader.pages)} pages had no extractable text. "
            "Scanned pages need OCR before they can be searched."
        )
    if not text.strip():
        warning = (
            "No text could be extracted from this PDF. It is probably a scan; "
            "run OCR on it first."
        )

    return Extraction(text=text, mime_type="application/pdf", warning=warning)


def _extract_docx(data: bytes) -> Extraction:
    import docx

    try:
        document = docx.Document(io.BytesIO(data))
    except Exception as exc:
        raise ValueError(f"Could not read Word document: {exc}") from exc

    blocks: list[str] = [
        paragraph.text for paragraph in document.paragraphs if paragraph.text.strip()
    ]

    # Tables are where a lot of the useful content lives in real documents
    # (policies, price lists, approval matrices), so they are flattened into
    # pipe-separated rows rather than dropped.
    for table in document.tables:
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells]
            if any(cells):
                blocks.append(" | ".join(cells))

    text = "\n\n".join(blocks)
    warning = None if text.strip() else "This Word document contained no readable text."

    return Extraction(
        text=text,
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        warning=warning,
    )


def extract(filename: str, data: bytes) -> Extraction:
    """Extract text from ``data`` based on the file's extension."""
    extension = _extension(filename)

    if extension == ".pdf":
        return _extract_pdf(data)
    if extension == ".docx":
        return _extract_docx(data)
    if extension in {".txt", ".md", ".markdown"}:
        text, warning = _decode_text(data)
        mime = "text/markdown" if extension in {".md", ".markdown"} else "text/plain"
        return Extraction(text=text, mime_type=mime, warning=warning)

    raise UnsupportedFileTypeError(
        f"Unsupported file type '{extension or filename}'. "
        f"Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
    )
