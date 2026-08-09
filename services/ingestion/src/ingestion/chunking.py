"""§11.1 — semantic/recursive chunking, header-aware `section_path`. Tables
and other structured content get "strategi khusus" per §11.1 — not
implemented here; the seed corpus is prose-only and a table-aware splitter
would be untested dead code until a document that needs it actually exists.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_HEADER_RE = re.compile(r"^(#{1,6})\s+(.*)$")


@dataclass
class Chunk:
    content: str
    section_path: str | None


def chunk_markdown(text: str, *, target_chars: int, overlap_chars: int) -> list[Chunk]:
    blocks = _blocks_by_section(text)

    chunks: list[Chunk] = []
    current_section: str | None = None
    current_text = ""
    for section_path, block_text in blocks:
        candidate = f"{current_text}\n\n{block_text}" if current_text else block_text
        if len(candidate) <= target_chars or not current_text:
            current_text = candidate
            current_section = section_path
        else:
            chunks.append(Chunk(content=current_text.strip(), section_path=current_section))
            overlap = current_text[-overlap_chars:] if overlap_chars else ""
            current_text = f"{overlap}\n\n{block_text}".strip()
            current_section = section_path
    if current_text.strip():
        chunks.append(Chunk(content=current_text.strip(), section_path=current_section))
    return chunks


def _blocks_by_section(text: str) -> list[tuple[str | None, str]]:
    """Splits on headers, tracking a `H1 > H2 > ...` breadcrumb, and
    collapses each section's paragraphs into one block — the unit
    `chunk_markdown` packs into target-sized chunks."""
    blocks: list[tuple[str | None, str]] = []
    section_stack: list[str] = []
    buffer: list[str] = []

    def flush() -> None:
        content = "\n".join(buffer).strip()
        if content:
            blocks.append((" > ".join(section_stack) or None, content))
        buffer.clear()

    for line in text.splitlines():
        header = _HEADER_RE.match(line)
        if header:
            flush()
            level = len(header.group(1))
            title = header.group(2).strip()
            section_stack[level - 1 :] = [title]
        else:
            buffer.append(line)
    flush()
    return blocks
