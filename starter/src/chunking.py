"""Fixed-size (strategy A) and structure-aware (strategy B) chunking.

Chunks never cross a page boundary so every chunk maps to exactly one
source page (exact traceability). Trade-off: a paragraph that continues on
the next page is split; this is reported as a coherence limitation.
Chunk IDs are deterministic (strategy, document, page, index, content hash),
so re-ingesting the same content produces the same IDs (no duplicates).
"""
from __future__ import annotations

import hashlib
import re

from .models import Chunk, DocumentPage


def _chunk_id(strategy: str, page: DocumentPage, index: int, text: str) -> str:
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:10]
    return f"{strategy}:{page.document_id}:p{page.page}:{index}:{digest}"


def _make(strategy: str, page: DocumentPage, index: int, text: str, **extra) -> Chunk:
    meta = dict(page.metadata)
    meta.update(extra)
    return Chunk(_chunk_id(strategy, page, index, text), page.document_id, page.source,
                 page.page, text, meta)


# ---------------------------------------------------------------- strategy A
def fixed_size(page: DocumentPage, size: int, overlap: int, strategy: str = "A") -> list[Chunk]:
    """Character windows of `size` with `overlap`; whitespace collapsed to single spaces."""
    if not 0 <= overlap < size:
        raise ValueError("require 0 <= overlap < size")
    text = re.sub(r"\s+", " ", page.text).strip()
    chunks, start, index = [], 0, 0
    while start < len(text):
        piece = text[start:start + size].strip()
        if piece:
            chunks.append(_make(strategy, page, index, piece, char_start=start))
            index += 1
        if start + size >= len(text):
            break
        start += size - overlap
    return chunks


# ---------------------------------------------------------------- strategy B
# Block headings used throughout the OpenStax textbook.
_HEADING_RE = re.compile(
    r"^(\d+\.\d+\s+[A-Z].{0,80}|Learning objectives|CHECKPOINT\b.*|CONCEPTS IN PRACTICE\b.*|"
    r"EXAMPLE \d+\.\d+.*|TRY IT\b.*|Chapter Summary|Key Terms|Introduction|[A-Z][A-Za-z '/()-]{2,50})$")
_SENTENCE_END = re.compile(r"[.!?:]\"?\)?$")


def _is_heading(line: str) -> bool:
    if len(line) > 90 or line.endswith((".", ",", ";")):
        return False
    if not re.match(r"^(\d+\.\d+\s|[A-Z]{3,})", line) and len(line.split()) > 7:
        return False  # long title-case lines are usually prose/code output, not headings
    return bool(_HEADING_RE.match(line))


def _paragraphs(text: str) -> list[tuple[str, str | None]]:
    """Split page text into (paragraph, heading) blocks.

    pypdf rarely emits blank lines, so a paragraph ends when (a) a blank line
    appears, (b) the next line is a heading, or (c) a line ends a sentence and
    is visibly shorter than the page's typical line width.
    """
    lines = text.split("\n")
    widths = sorted(len(l) for l in lines if l.strip())
    typical = widths[int(len(widths) * 0.8)] if widths else 80
    blocks: list[tuple[str, str | None]] = []
    buf: list[str] = []
    heading: str | None = None

    def flush() -> None:
        if buf:
            blocks.append((" ".join(buf).strip(), heading))
            buf.clear()

    for line in lines:
        line = line.strip()
        if not line:
            flush()
            continue
        if _is_heading(line):
            flush()
            heading = line
            buf.append(line)
            continue
        buf.append(line)
        if _SENTENCE_END.search(line) and len(line) < 0.8 * typical:
            flush()
    flush()
    return blocks


def _split_long(paragraph: str, max_size: int) -> list[str]:
    """Split an oversized paragraph on sentence boundaries (hard cut as last resort)."""
    sentences = re.split(r"(?<=[.!?])\s+", paragraph)
    out, cur = [], ""
    for s in sentences:
        while len(s) > max_size:  # a single huge "sentence" (e.g. code listing)
            if cur:
                out.append(cur)
                cur = ""
            out.append(s[:max_size])
            s = s[max_size:]
        if cur and len(cur) + 1 + len(s) > max_size:
            out.append(cur)
            cur = s
        else:
            cur = f"{cur} {s}".strip()
    if cur:
        out.append(cur)
    return out


def by_paragraph(page: DocumentPage, max_size: int, min_size: int = 200,
                 strategy: str = "B") -> list[Chunk]:
    """Group consecutive paragraphs up to max_size; start a new chunk at headings
    once the current chunk has at least min_size characters."""
    groups: list[tuple[str, str | None]] = []  # (text, heading) before IDs are assigned
    buf, buf_heading = "", None

    def flush() -> None:
        nonlocal buf
        if buf.strip():
            groups.append((buf.strip(), buf_heading))
        buf = ""

    for para, heading in _paragraphs(page.text):
        starts_block = heading is not None and para.startswith(heading)
        if starts_block and len(buf) >= min_size:
            flush()
        for piece in _split_long(para, max_size):
            if buf and len(buf) + 1 + len(piece) > max_size:
                flush()
            if not buf:
                buf_heading = heading
            buf = f"{buf}\n{piece}" if buf else piece
    flush()

    # Merge fragments shorter than min_size (e.g. a lone "TRY IT" box label at the
    # bottom of a page) into the previous chunk, allowing up to 20% over max_size.
    merged: list[tuple[str, str | None]] = []
    for text, heading in groups:
        if merged and len(text) < min_size and len(merged[-1][0]) + 1 + len(text) <= int(max_size * 1.2):
            merged[-1] = (f"{merged[-1][0]}\n{text}", merged[-1][1])
        elif merged and len(merged[-1][0]) < min_size and len(merged[-1][0]) + 1 + len(text) <= int(max_size * 1.2):
            merged[-1] = (f"{merged[-1][0]}\n{text}", merged[-1][1])
        else:
            merged.append((text, heading))
    return [_make(strategy, page, i, text, heading=h) for i, (text, h) in enumerate(merged)]


def chunk_pages(pages: list[DocumentPage], strategy: str, params: dict) -> list[Chunk]:
    out: list[Chunk] = []
    for p in pages:
        if params["chunking"] == "fixed":
            out += fixed_size(p, params["size"], params["overlap"], strategy)
        elif params["chunking"] == "paragraph":
            out += by_paragraph(p, params["max_size"], params["min_size"], strategy)
        else:
            raise ValueError(f"unknown chunking {params['chunking']}")
    return out
