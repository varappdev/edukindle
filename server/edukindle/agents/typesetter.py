"""Restores the layout of text extracted from PDFs.

PDF text extraction keeps the words but loses the structure: lines break
mid-sentence, bullets run into paragraphs, formulas are flattened into plain
characters (or Unicode maths letters no Kindle font has) and code merges into
prose. The typesetter asks the model to rewrite a section as the Markdown +
LaTeX subset the Kindle renders, then checks that every word survived before
accepting it, so a summarising or hallucinating model can't replace the
student's textbook.
"""

from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from collections import Counter
from typing import Any

from .. import richtext
from ..llm import LLM, STR, obj

log = logging.getLogger("edukindle.typesetter")

# Bump when the prompt changes so sections are typeset again.
VERSION = 1
# Rewriting a long section takes longer than an ordinary call.
TIMEOUT_S = 150

SYSTEM = """You restore the layout of textbook text that was extracted from a PDF, for \
an E-ink reader. You are a careful typesetter: you never change the content, only how \
it is laid out.

The reader renders this Markdown subset and nothing else:
- paragraphs separated by a blank line
- "- " bullet lists and "1. " numbered lists (indent two spaces to nest)
- "### " sub-headings
- **bold**, *italic*, `inline code`
- fenced code blocks with a language: ```python ... ```
- LaTeX maths: $...$ inline, and $$...$$ on lines of their own for display maths
- "> " for a highlighted note"""

SCHEMA = obj({"markdown": STR})

_WORD = re.compile(r"[A-Za-z]{3,}")


def _words(text: str) -> Counter:
    text = unicodedata.normalize("NFKC", text)  # 𝑛 -> n, ﬁ -> fi
    text = re.sub(r"\\[A-Za-z]+", " ", text)   # LaTeX command names aren't content
    return Counter(w.lower() for w in _WORD.findall(text))


def faithful(raw: str, markdown: str) -> bool:
    """At least 85% of the original words are still there, and the result is
    neither much shorter (summarised) nor much longer (padded)."""
    before, after = _words(raw), _words(markdown)
    total = sum(before.values())
    if total < 20:
        return bool(markdown.strip())
    kept = sum(min(n, after[w]) for w, n in before.items())
    ratio = sum(after.values()) / total
    return kept / total >= 0.85 and 0.7 <= ratio <= 1.5


def _tidy(md: str) -> str:
    md = md.replace("\r\n", "\n").strip()
    md = re.sub(r"^#{1,2}\s+", "### ", md, flags=re.M)  # the section title is shown already
    md = re.sub(r"^#{4,6}\s+", "### ", md, flags=re.M)
    return richtext.join([richtext.normalise(c) for c in richtext.paragraphs(md)])


class Typesetter:
    def __init__(self, llm: LLM):
        self.llm = llm
        # Long sections take a while; leave LLM capacity for quizzes and explanations.
        self._sem = asyncio.Semaphore(2)

    async def typeset(self, doc: dict[str, Any], section: dict[str, Any]) -> str | None:
        """Markdown for the section, or None if the result failed the
        faithfulness check. Raises LLMError if the model call fails."""
        raw = section.get("raw") or section["text"]
        user = f"""Section "{section['title']}" of "{doc['title']}". The text below was \
extracted from a PDF, so its layout is damaged: lines broken mid-sentence, list bullets \
run into paragraphs, mathematics flattened into plain characters, code merged into prose.

Rewrite it as clean Markdown, faithfully:
- Keep every sentence, word, number and example, in the original order. Do not \
summarise, explain, shorten, add or correct anything.
- Join lines that were broken mid-sentence; separate paragraphs with a blank line.
- Restore lists: every bullet (•, ◦, –, *) or numbered point becomes its own "- " or \
"1. " item.
- Write all mathematics in LaTeX: $...$ inline, and $$...$$ on lines of their own for \
formulas, equations and derivation steps that stood on separate lines. Convert Unicode \
maths (𝑛, ∑, ⌊ ⌋, x², ≤, ⊢) to LaTeX.
- Put source code, pseudocode and program output in fenced code blocks.
- Use "### " only for sub-headings inside the section; don't repeat its title.
- Drop page numbers, running headers and footers, and stray symbols left by the \
extraction (such as fragments of large brackets).

Extracted text:
\"\"\"
{raw}
\"\"\""""
        async with self._sem:
            data = await self.llm.json(system=SYSTEM, user=user, name="typeset",
                                       schema=SCHEMA,
                                       max_tokens=min(20000, 4000 + len(raw)),
                                       timeout=TIMEOUT_S)
        markdown = _tidy(data.get("markdown", ""))
        if not faithful(raw, markdown):
            log.warning("typeset of %s rejected: content changed too much", section["id"])
            return None
        return markdown
