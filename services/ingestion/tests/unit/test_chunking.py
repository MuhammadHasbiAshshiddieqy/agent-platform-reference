"""Pure-logic test — no DB, no embeddings."""

from __future__ import annotations

from ingestion.chunking import chunk_markdown


def test_short_document_becomes_a_single_chunk() -> None:
    text = "# Title\n\nJust one short paragraph."
    chunks = chunk_markdown(text, target_chars=2048, overlap_chars=256)
    assert len(chunks) == 1
    assert "Just one short paragraph." in chunks[0].content


def test_long_document_splits_into_multiple_chunks() -> None:
    # Three sections, each well over target_chars on its own.
    section = "Paragraf panjang. " * 50  # ~900 chars
    text = f"# A\n\n{section}\n\n# B\n\n{section}\n\n# C\n\n{section}"
    chunks = chunk_markdown(text, target_chars=1000, overlap_chars=100)
    assert len(chunks) > 1
    for chunk in chunks:
        # Overlap can push a chunk slightly over target; it must never be
        # *wildly* over (i.e. the packer is actually splitting, not just
        # accumulating everything into one chunk).
        assert len(chunk.content) < 2000


def test_consecutive_chunks_share_an_overlap_tail() -> None:
    section = "Paragraf panjang. " * 50
    text = f"# A\n\n{section}\n\n# B\n\n{section}"
    chunks = chunk_markdown(text, target_chars=500, overlap_chars=100)
    assert len(chunks) >= 2
    tail_of_first = chunks[0].content[-50:]
    assert tail_of_first in chunks[1].content


def test_section_path_tracks_markdown_headers() -> None:
    text = "# Kebijakan\n\n## Cuti\n\nIsi kebijakan cuti."
    chunks = chunk_markdown(text, target_chars=2048, overlap_chars=256)
    assert chunks[0].section_path == "Kebijakan > Cuti"


def test_empty_document_produces_no_chunks() -> None:
    assert chunk_markdown("   \n\n  ", target_chars=2048, overlap_chars=256) == []
