"""Tests for the chunker.

The property that matters most is that every chunk is an *exact slice* of the
source. Citations are character offsets into the original document, so if a
chunk's text is not literally ``source[start:end]``, every citation built
from it points at the wrong place.
"""

from __future__ import annotations

from itertools import pairwise

import pytest

from app.rag.chunker import chunk_text


def _paragraphs(count: int, words: int = 30) -> str:
    return "\n\n".join(
        f"Paragraph {i} " + " ".join(f"word{i}x{j}" for j in range(words)) for i in range(count)
    )


class TestChunkText:
    def test_empty_input_returns_no_chunks(self):
        assert chunk_text("") == []
        assert chunk_text("   \n\n  \t ") == []

    def test_every_chunk_is_an_exact_slice(self):
        text = _paragraphs(8)
        for chunk in chunk_text(text, target_chars=400, overlap_chars=80):
            assert text[chunk.start_char : chunk.end_char] == chunk.text

    def test_ordinals_are_sequential_from_zero(self):
        chunks = chunk_text(_paragraphs(6), target_chars=300, overlap_chars=60)
        assert [c.ordinal for c in chunks] == list(range(len(chunks)))

    def test_chunks_do_not_exceed_target_by_much(self):
        text = _paragraphs(10)
        for chunk in chunk_text(text, target_chars=500, overlap_chars=100):
            # A single atomic unit can exceed the target, but nothing should
            # be wildly over it.
            assert chunk.char_length <= 500 * 2

    def test_overlap_actually_occurs(self):
        """Consecutive chunks must share characters, or the overlap is a lie."""
        chunks = chunk_text(_paragraphs(8), target_chars=400, overlap_chars=100)
        assert len(chunks) > 1
        for previous, following in pairwise(chunks):
            assert following.start_char < previous.end_char, "no overlap between chunks"

    def test_overlap_is_roughly_the_requested_size(self):
        chunks = chunk_text(_paragraphs(8), target_chars=400, overlap_chars=100)
        for previous, following in pairwise(chunks):
            overlap = previous.end_char - following.start_char
            # Snapping to a word boundary can shrink it, but it should be in
            # the right neighbourhood, not zero and not the whole chunk.
            assert 0 < overlap <= 100

    def test_offsets_are_monotonic(self):
        chunks = chunk_text(_paragraphs(10), target_chars=350, overlap_chars=70)
        for previous, following in pairwise(chunks):
            assert following.start_char > previous.start_char
            assert following.end_char > previous.end_char

    def test_covers_the_whole_document(self):
        text = _paragraphs(6)
        chunks = chunk_text(text, target_chars=400, overlap_chars=80)
        assert chunks[0].start_char == 0
        assert chunks[-1].end_char == len(text)

    def test_oversized_single_paragraph_is_split(self):
        """One paragraph far larger than the target must still be chunked."""
        text = " ".join(f"token{i}" for i in range(2000))
        chunks = chunk_text(text, target_chars=300, overlap_chars=50)
        assert len(chunks) > 1
        for chunk in chunks:
            assert text[chunk.start_char : chunk.end_char] == chunk.text

    def test_no_overlap_when_disabled(self):
        chunks = chunk_text(_paragraphs(6), target_chars=300, overlap_chars=0)
        for previous, following in pairwise(chunks):
            assert following.start_char >= previous.end_char

    def test_short_text_is_one_chunk(self):
        chunks = chunk_text("A short document.", target_chars=900, overlap_chars=150)
        assert len(chunks) == 1
        assert chunks[0].text == "A short document."

    def test_terminates_on_pathological_input(self):
        """A long run of identical characters must not loop forever."""
        chunks = chunk_text("x" * 5000, target_chars=200, overlap_chars=50)
        assert len(chunks) > 1
        assert all(c.char_length > 0 for c in chunks)

    @pytest.mark.parametrize(
        ("target", "overlap"),
        [(0, 0), (-1, 0), (100, 100), (100, 150)],
    )
    def test_invalid_parameters_raise(self, target, overlap):
        with pytest.raises(ValueError):
            chunk_text("some text here", target_chars=target, overlap_chars=overlap)
