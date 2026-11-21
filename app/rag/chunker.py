"""Split a document into overlapping chunks with exact character offsets.

Why offsets matter: a citation that says "this came from document 4" is
unfalsifiable. A citation that says "characters 1204–1498 of document 4"
can be checked by anyone, and the UI can highlight the exact span. So every
chunk carries the offsets it was cut from, and the text is never rebuilt
from tokens — it is always a slice of the original string.

The packing strategy is paragraph-first, sentence-second:

1. Split on blank lines into paragraphs.
2. Accumulate paragraphs until the target size is reached.
3. If a single paragraph is larger than the target, split it on sentence
   boundaries instead.
4. If a single sentence is still too large, hard-split it on whitespace.

Overlap is applied by starting the next chunk a little before the end of the
previous one, so an answer spanning a boundary is not cut in half.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.rag.tokenizer import split_sentences


@dataclass(frozen=True, slots=True)
class ChunkSpan:
    """A chunk and the exact slice of the source it came from."""

    ordinal: int
    text: str
    start_char: int
    end_char: int

    @property
    def char_length(self) -> int:
        return self.end_char - self.start_char


def _paragraph_spans(text: str) -> list[tuple[int, int]]:
    """Offsets of paragraphs, split on blank lines."""
    spans: list[tuple[int, int]] = []
    start = 0
    index = 0
    length = len(text)

    while index < length:
        if text[index] == "\n":
            # Consume the whole run of newlines; two or more ends a paragraph.
            run_start = index
            while index < length and text[index] == "\n":
                index += 1
            if index - run_start >= 2:
                if text[start:run_start].strip():
                    spans.append((start, run_start))
                start = index
            continue
        index += 1

    if start < length and text[start:].strip():
        spans.append((start, length))

    return spans


def _split_oversized(text: str, start: int, end: int, target: int) -> list[tuple[int, int]]:
    """Break a span that exceeds ``target`` into sentence-sized pieces."""
    pieces: list[tuple[int, int]] = []
    for rel_start, rel_end in split_sentences(text[start:end]):
        abs_start, abs_end = start + rel_start, start + rel_end
        if abs_end - abs_start <= target:
            pieces.append((abs_start, abs_end))
            continue

        # A single sentence longer than the target: fall back to whitespace.
        cursor = abs_start
        while cursor < abs_end:
            limit = min(cursor + target, abs_end)
            if limit < abs_end:
                space = text.rfind(" ", cursor, limit)
                if space > cursor:
                    limit = space
            if text[cursor:limit].strip():
                pieces.append((cursor, limit))
            cursor = limit
    return pieces


def chunk_text(
    text: str,
    *,
    target_chars: int = 900,
    overlap_chars: int = 150,
) -> list[ChunkSpan]:
    """Split ``text`` into overlapping chunks.

    Returns an empty list for text that is empty or whitespace only, so
    callers never have to special-case a document with no content.
    """
    if not text or not text.strip():
        return []

    if target_chars <= 0:
        raise ValueError("target_chars must be positive")
    if overlap_chars < 0:
        raise ValueError("overlap_chars must not be negative")
    if overlap_chars >= target_chars:
        # Otherwise the next chunk would start at or before the previous one
        # and the loop would never advance.
        raise ValueError("overlap_chars must be smaller than target_chars")

    # Build the atomic units first: paragraphs, with oversized ones already
    # broken down.
    units: list[tuple[int, int]] = []
    for para_start, para_end in _paragraph_spans(text):
        if para_end - para_start <= target_chars:
            units.append((para_start, para_end))
        else:
            units.extend(_split_oversized(text, para_start, para_end, target_chars))

    if not units:
        return []

    chunks: list[ChunkSpan] = []
    ordinal = 0
    cursor = 0
    unit_index = 0

    while unit_index < len(units):
        # Skip units that end before the cursor — they are already covered.
        while unit_index < len(units) and units[unit_index][1] <= cursor:
            unit_index += 1
        if unit_index >= len(units):
            break

        # The chunk starts at the cursor, which may be inside the previous
        # chunk. That is what overlap means: the tail of one chunk is
        # repeated at the head of the next, so an answer spanning the
        # boundary is not cut in half.
        start = cursor
        end = start
        probe = unit_index

        while probe < len(units) and units[probe][1] - start <= target_chars:
            end = units[probe][1]
            probe += 1

        if end <= start:
            # A single unit is longer than the target. It was already split
            # into sentence-sized pieces above, so take the next one whole
            # rather than emitting an empty chunk.
            end = units[unit_index][1]
            probe = unit_index + 1

        body = text[start:end]
        if body.strip():
            chunks.append(
                ChunkSpan(
                    ordinal=ordinal,
                    text=body,
                    start_char=start,
                    end_char=end,
                )
            )
            ordinal += 1

        if probe >= len(units):
            break

        # Next chunk begins ``overlap_chars`` before this one ended, snapped
        # forward to a word boundary so it does not start mid-word.
        next_start = max(end - overlap_chars, start + 1)
        space = text.find(" ", next_start, end)
        if space != -1:
            next_start = space + 1

        # Guarantee forward progress: the cursor must advance past the start
        # of this chunk, or the loop would emit the same span forever.
        cursor = max(next_start, start + 1)
        unit_index = probe

    return chunks
