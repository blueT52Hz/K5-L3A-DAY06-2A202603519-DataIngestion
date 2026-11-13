"""Read and normalize the PDF corpus listed in data/manifest.json.

Each physical PDF page listed as a content page becomes one DocumentPage that
keeps document_id, source, 1-based physical page number and metadata
(chapter, chapter_title, section) taken from the manifest and PDF outline.
Pages that fail to extract or are too short are logged, never silently dropped.
"""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any

from pypdf import PdfReader

from .models import DocumentPage

MIN_CONTENT_CHARS = 300  # pages shorter than this after cleaning are logged as empty/short

# Running headers/footers printed on every OpenStax page.
_NOISE_LINE_PATTERNS = [
    re.compile(r"^Access for free at openstax\.org\.?$", re.I),
    re.compile(r"^\d{1,3}\s+\d{1,2}\s*•\s*.+$"),            # even page header: "12 1 • Statements"
    re.compile(r"^\d{1,2}\.\d{1,2}\s*•\s*.+\s\d{1,3}$"),     # odd page header: "1.2 • Input/output 13"
    re.compile(r"^\d{1,3}$"),                                # bare page number
]

_LIGATURES = {"ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "ﬃ": "ffi", "ﬄ": "ffl"}


def normalize_text(text: str) -> str:
    """Normalize Unicode and whitespace while keeping line structure.

    Line breaks are kept (paragraph-aware chunking relies on them); only
    running headers/footers and trailing spaces are removed.
    """
    text = unicodedata.normalize("NFC", text)
    for lig, rep in _LIGATURES.items():
        text = text.replace(lig, rep)
    text = text.replace("­", "").replace(" ", " ").replace("\t", " ")
    lines = []
    for raw in text.splitlines():
        line = re.sub(r" {2,}", " ", raw).strip()
        if not line:
            lines.append("")
            continue
        if any(p.match(line) for p in _NOISE_LINE_PATTERNS):
            continue
        lines.append(line)
    out = "\n".join(lines)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def _outline_sections(reader: PdfReader) -> list[tuple[int, str]]:
    """Return sorted (1-based page, title) for every bookmark in the PDF outline."""
    found: list[tuple[int, str]] = []

    def walk(items: list[Any]) -> None:
        for item in items:
            if isinstance(item, list):
                walk(item)
                continue
            try:
                page_no = reader.get_destination_page_number(item) + 1
            except Exception:
                continue
            found.append((page_no, str(item.title).strip()))

    try:
        walk(reader.outline)
    except Exception:
        return []
    return sorted(found)


def _section_for_page(sections: list[tuple[int, str]], page_no: int, chapter: int) -> str | None:
    """Latest numbered section bookmark of the same chapter (e.g. '1.2 Input/output')
    starting at or before the page; None on a chapter opener page."""
    current = None
    for start, title in sections:
        if start > page_no:
            break
        if re.match(rf"^{chapter}\.\d+\s", title):
            current = title
    return current


def load_pdf(path: Path, document_id: str, source: str,
             chapters: list[dict[str, Any]], base_metadata: dict[str, Any] | None = None,
             exclude_pages: set[int] | None = None) -> tuple[list[DocumentPage], list[dict]]:
    """Extract the content pages of one PDF.

    chapters: [{"chapter": 1, "chapter_title": "...", "first_page": a, "last_page": b}]
    Only pages inside a chapter range (minus exclude_pages) are content pages.
    Returns (pages, error_log_entries).
    """
    reader = PdfReader(str(path))
    sections = _outline_sections(reader)
    exclude_pages = exclude_pages or set()
    pages: list[DocumentPage] = []
    errors: list[dict] = []
    for ch in chapters:
        for page_no in range(ch["first_page"], ch["last_page"] + 1):
            if page_no in exclude_pages:
                errors.append({"document_id": document_id, "page": page_no,
                               "status": "excluded_by_rule"})
                continue
            try:
                raw = reader.pages[page_no - 1].extract_text() or ""
            except Exception as exc:  # keep going, but record the failure
                errors.append({"document_id": document_id, "page": page_no,
                               "status": "extract_error", "error": repr(exc)})
                continue
            text = normalize_text(raw)
            if len(text) < MIN_CONTENT_CHARS:
                errors.append({"document_id": document_id, "page": page_no,
                               "status": "empty_or_short", "chars": len(text)})
                continue
            meta = dict(base_metadata or {})
            meta.update({"chapter": ch["chapter"], "chapter_title": ch["chapter_title"],
                         "section": _section_for_page(sections, page_no, ch["chapter"])})
            pages.append(DocumentPage(document_id, source, page_no, text, meta))
    return pages, errors


def load_corpus(manifest_path: Path, repo_root: Path) -> tuple[list[DocumentPage], list[dict]]:
    """Load every document in the manifest; enforce the 30 content page minimum."""
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    all_pages: list[DocumentPage] = []
    all_errors: list[dict] = []
    for doc in manifest["documents"]:
        pdf_path = repo_root / doc["local_path"]
        if not pdf_path.exists():
            raise FileNotFoundError(
                f"{pdf_path} not found. Download it from {doc['source_path_or_url']} (see README).")
        pages, errors = load_pdf(
            pdf_path, doc["source_id"], doc["source_path_or_url"], doc["chapters"],
            base_metadata=doc.get("metadata", {}),
            exclude_pages=set(doc.get("exclude_pages", [])))
        all_pages += pages
        all_errors += errors
    if len(all_pages) < 30:
        raise ValueError(f"Only {len(all_pages)} valid content pages (< 30 required)")
    return all_pages, all_errors
