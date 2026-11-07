"""Turn PDFs, Markdown and plain text into a document of readable sections.

Section text is stored as the Markdown subset in `richtext`: chunks separated
by one blank line, where prose is collapsed onto one line but lists, code
blocks and display maths keep their structure. Paragraph indices (used when a
student taps a paragraph on the Kindle) are `richtext.paragraphs()` indices.
"""

from __future__ import annotations

import io
import re
import unicodedata
from typing import Any, Iterable

from . import richtext
from .store import now

TARGET_WORDS = 550   # aim for sections a student can read in ~3 minutes
MAX_WORDS = 900
MIN_WORDS = 120

_HEADING_RE = re.compile(
    r"^(?:(?:chapter|part|unit|lesson|section)\s+[\dIVXLC]+[.:]?\s*.*"
    r"|\d+(?:\.\d+){0,2}\.?\s+[A-Z][^.!?]{2,70})$",
    re.IGNORECASE,
)


def slugify(text: str, max_len: int = 40) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:max_len].strip("-") or "doc"


_MD_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
# Glyph bullets are often glued to the text ("•Understand"); dashes need a space.
_PDF_BULLET = re.compile(rf"^(?:[{richtext.BULLET_GLYPHS}\u25e6]\s*|[\u2013*-]\s+)(.*)$")
# Several bullets extracted onto one line: split before each glyph.
_PDF_MIDLINE_BULLET = re.compile(rf"(?<=\S)\s+(?=[{richtext.BULLET_GLYPHS}])")


def normalise_paragraphs(paragraphs: Iterable[str]) -> list[str]:
    out = []
    for p in paragraphs:
        p = richtext.normalise(p)
        if p:
            out.append(p)
    return out


def _words(paragraphs: list[str]) -> int:
    return sum(len(p.split()) for p in paragraphs)


def _chunk(title: str, paragraphs: list[str]) -> list[tuple[str, list[str]]]:
    """Split an over-long section at paragraph boundaries."""
    if _words(paragraphs) <= MAX_WORDS:
        return [(title, paragraphs)]
    parts: list[list[str]] = [[]]
    for p in paragraphs:
        if parts[-1] and _words(parts[-1]) + len(p.split()) > TARGET_WORDS:
            parts.append([])
        parts[-1].append(p)
    if len(parts) > 1 and _words(parts[-1]) < MIN_WORDS:
        parts[-2].extend(parts.pop())
    if len(parts) == 1:
        return [(title, parts[0])]
    return [(f"{title} ({i}/{len(parts)})", ps) for i, ps in enumerate(parts, 1)]


def build_document(
    title: str,
    raw_sections: list[tuple[str, str, list[str]]],
    source: str,
    existing_ids: Iterable[str] = (),
    doc_id: str | None = None,
) -> dict[str, Any]:
    """raw_sections: (chapter, section title, paragraphs). Pass doc_id to
    rebuild an existing document in place."""
    if doc_id is None:
        base = slugify(title)
        doc_id, n = base, 2
        taken = set(existing_ids)
        while doc_id in taken:
            doc_id, n = f"{base}-{n}", n + 1

    sections = []
    for chapter, stitle, paras in raw_sections:
        paras = normalise_paragraphs(paras)
        if not paras:
            continue
        for t, ps in _chunk(stitle, paras):
            idx = len(sections)
            sections.append({
                "id": f"{doc_id}.{idx + 1}",
                "index": idx,
                "chapter": chapter,
                "title": t,
                "text": "\n\n".join(ps),
                "concepts": [],
            })
    if not sections:
        raise ValueError("document has no readable text")
    return {
        "id": doc_id,
        "title": title.strip() or "Untitled",
        "source": source,
        "created": now(),
        "sections": sections,
        "concepts": {},
        "concepts_ready": False,
    }


# -- plain text / markdown -------------------------------------------------


def split_text(text: str, fallback_title: str) -> list[tuple[str, str, list[str]]]:
    """Markdown headings (#, ##, ###) start sections; everything else (lists,
    code, maths, prose) is kept as Markdown chunks."""
    sections: list[tuple[str, str, list[str]]] = []
    chapter, title, chunks = "", fallback_title, []
    titled = False

    def flush() -> None:
        if chunks:
            sections.append((chapter, title, list(chunks)))
            chunks.clear()

    for chunk in richtext.paragraphs(text):
        lines = chunk.split("\n")
        m = _MD_HEADING.match(lines[0]) if richtext.kind_of(chunk) == "structured" else None
        if not m or len(m.group(1)) > 3:
            chunks.append(chunk)
            continue
        level, heading = len(m.group(1)), m.group(2).strip()
        if level == 1 and not titled and not sections and not chunks and not chapter:
            title = heading  # a leading H1 is the document title, not a chapter
            titled = True
        else:
            flush()
            if level == 1:
                chapter = heading
            title = heading
        rest = "\n".join(lines[1:]).strip()
        if rest:
            chunks.append(rest)
    flush()
    return sections


# -- PDF ---------------------------------------------------------------------


def _pdf_paragraphs(page_text: str) -> list[tuple[bool, str]]:
    """Rebuild paragraphs from PDF lines. Yields (is_heading, text)."""
    lines = [l.rstrip() for l in page_text.split("\n")]
    widths = sorted(len(l) for l in lines if l.strip())
    full = widths[int(len(widths) * 0.8)] if widths else 80

    out: list[tuple[bool, str]] = []
    buf: list[str] = []
    last = ""  # the previous line, to tell a wrapped list item from one that ended

    def flush():
        if buf:
            out.append((False, " ".join(buf)))
            buf.clear()

    segments = []
    for line in lines:
        segments.extend(_PDF_MIDLINE_BULLET.split(line.strip()) if line.strip() else [""])
    for s in segments:
        if not s:
            flush()
            continue
        if re.fullmatch(r"\d{1,4}", s):  # page number
            continue
        if bullet := _PDF_BULLET.match(s):
            flush()
            buf.append("- " + bullet.group(1))  # becomes a Markdown list item
            last = s
            continue
        if buf and buf[0].startswith("- ") and s[:1].isupper() and len(last) < 0.75 * full:
            flush()  # a short item line, then a capital: the list item has ended
        last = s
        if len(s) < 80 and _HEADING_RE.match(s) and not s.endswith((".", ",")):
            flush()
            out.append((True, s))
            continue
        if buf and buf[-1].endswith("-") and s[:1].islower():
            buf[-1] = buf[-1][:-1] + s  # de-hyphenate
        else:
            buf.append(s)
        if s.endswith((".", "?", "!", ":")) and len(s) < 0.75 * full:
            flush()
    flush()
    return out


def split_pdf(data: bytes, fallback_title: str) -> tuple[str, list[tuple[str, str, list[str]]]]:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    title = fallback_title
    meta_title = (reader.metadata.title if reader.metadata else None) or ""
    meta_title = meta_title.strip()
    # Many tools stamp the source filename ("notes.docx") as the title; skip those.
    if meta_title and len(meta_title) < 120 and not re.search(r"\.[A-Za-z]{2,4}$", meta_title):
        title = meta_title

    sections: list[tuple[str, str, list[str]]] = []
    chapter, stitle, paras = "", title, []
    for page in reader.pages:
        for is_heading, text in _pdf_paragraphs(page.extract_text() or ""):
            if is_heading:
                if paras:
                    sections.append((chapter, stitle, paras))
                    paras = []
                if re.match(r"^(chapter|part|unit)\b", text, re.I):
                    chapter = text
                stitle = text
            else:
                paras.append(text)
    if paras:
        sections.append((chapter, stitle, paras))

    # Merge sections too small to quiz on into their predecessor.
    merged: list[tuple[str, str, list[str]]] = []
    for sec in sections:
        if merged and _words(sec[2]) < MIN_WORDS // 2:
            merged[-1][2].extend(sec[2])
        else:
            merged.append(sec)
    return title, merged


def ingest(filename: str, data: bytes, existing_ids: Iterable[str] = ()) -> dict[str, Any]:
    stem = re.sub(r"\.[^.]+$", "", filename).replace("_", " ").strip() or "Untitled"
    if filename.lower().endswith(".pdf") or data[:5] == b"%PDF-":
        title, sections = split_pdf(data, stem)
        return build_document(title, sections, "pdf", existing_ids)
    text = data.decode("utf-8", errors="replace")
    sections = split_text(text, stem)
    title = stem
    m = re.match(r"\s*#\s+(.+)", text)
    if m:
        title = m.group(1).strip()
    return build_document(title, sections, "text", existing_ids)
