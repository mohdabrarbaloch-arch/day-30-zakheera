"""Turn retrieved chunks into an answer with verifiable citations.

Two modes, and the difference matters:

**Extractive (default).** The answer is assembled from sentences that already
exist in the corpus, quoted verbatim, each tagged with a marker. Nothing is
paraphrased, so nothing can be invented. The citation carries the exact
character offsets of the quoted sentence in the source document, which means
a reader can check it rather than trust it.

**LLM (optional).** If ``OPENAI_API_KEY`` is set, a model writes the prose
from the same retrieved chunks. The citations are computed identically — the
model is given the chunks and told to cite them, but the offsets come from
our own sentence matching, not from the model's output. A model that
hallucinates a quote cannot produce a citation that points at real text.

Sentence selection is IDF-weighted overlap against the question, so a
sentence containing "refund" and "window" outranks one that merely contains
"the".
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.rag.index import SearchIndex
from app.rag.retriever import ScoredChunk
from app.rag.tokenizer import split_sentences, tokenize

# How many sentences to quote at most. Beyond about four, an answer stops
# being an answer and becomes a document dump.
MAX_SENTENCES = 4

# A sentence shorter than this is usually a heading fragment or a stray
# number, and quoting it wastes a citation slot.
MIN_SENTENCE_CHARS = 40

# A sentence must score at least this much to be quoted. Without a floor,
# any sentence sharing a single common word with the question qualifies, and
# the answer fills up with irrelevant text that happens to contain "leave"
# or "incident".
MIN_SENTENCE_SCORE = 0.1

# A sentence must cover at least this share of the question's core terms.
# One shared word out of four is coincidence, not relevance.
MIN_CORE_COVERAGE = 0.5

# Sentences that are mostly boilerplate add noise to an answer.
_BOILERPLATE_RE = re.compile(
    r"^\s*(page\s+\d+|table of contents|confidential|all rights reserved|copyright)\b",
    re.IGNORECASE,
)


@dataclass(slots=True)
class Sentence:
    text: str
    start_char: int
    end_char: int
    chunk: ScoredChunk
    score: float


@dataclass(slots=True)
class Citation:
    marker: int
    document_id: int
    document_title: str
    filename: str
    chunk_ordinal: int
    start_char: int
    end_char: int
    quote: str
    score: float


@dataclass(slots=True)
class Synthesis:
    answer: str
    citations: list[Citation]
    mode: str


def _score_sentence(sentence: str, query_terms: set[str], index: SearchIndex) -> float:
    """IDF-weighted overlap between a sentence and the question."""
    if not query_terms:
        return 0.0

    sentence_terms = set(tokenize(sentence))
    if not sentence_terms:
        return 0.0

    matched = sentence_terms & query_terms
    if not matched:
        return 0.0

    # Weight by IDF so a shared rare term counts for more than a shared
    # common one, then normalise by the question's total weight so that a
    # long question is not automatically worth more.
    total_weight = sum(index.idf(term) for term in query_terms)
    if total_weight == 0.0:
        return 0.0

    matched_weight = sum(index.idf(term) for term in matched)
    return matched_weight / total_weight


def _core_terms(query_terms: set[str], index: SearchIndex) -> set[str]:
    """The question's terms that a relevant sentence must actually contain.

    Two exclusions, both learned from watching this fail:

    * **Terms absent from the corpus are dropped.** An earlier version kept
      the *rarest* terms, which meant a term with zero occurrences (highest
      possible IDF) became the mandatory filter — and no sentence could ever
      satisfy it. A missing term is already handled by the abstention check;
      it must not also veto every candidate sentence.
    * **Pure numbers are dropped.** "100,000" tokenises to "100" and "000",
      which are not concepts. Requiring a sentence to contain "100" rejects
      the very table that answers the question.
    """
    return {term for term in query_terms if term in index.postings and not term.isdigit()}


def _candidate_sentences(
    scored_chunks: list[ScoredChunk], query_terms: set[str], index: SearchIndex
) -> list[Sentence]:
    """Extract and score every usable sentence from the retrieved chunks."""
    candidates: list[Sentence] = []
    core_terms = _core_terms(query_terms, index)

    if not core_terms:
        return []

    for scored in scored_chunks:
        chunk = scored.chunk
        for rel_start, rel_end in split_sentences(chunk.text):
            raw = chunk.text[rel_start:rel_end]
            cleaned = raw.strip()
            if len(cleaned) < MIN_SENTENCE_CHARS:
                continue
            if _BOILERPLATE_RE.match(cleaned):
                continue

            # A chunk that begins mid-sentence (because of overlap) yields a
            # fragment like "unused annual leave days may be carried into the
            # following year." Quoting that as a citation looks like a typo.
            # The first sentence of a chunk is only trusted when the chunk
            # starts at the beginning of the document or right after a
            # sentence terminator.
            if rel_start == 0 and chunk.start_char > 0:
                preceding = chunk.text[:1]
                if preceding and preceding not in ".!?\u06d4\n":
                    continue

            sentence_terms = set(tokenize(cleaned))

            # A sentence must cover at least half the question's core terms.
            #
            # Matching just one is not enough: "Expenses must be submitted
            # within thirty days" shares "days" with "how many annual leave
            # days do I get", and on a single shared term it scored high
            # enough to be quoted. Requiring half the core terms rejects it
            # while keeping the sentence that actually answers the question.
            matched = sentence_terms & core_terms
            if len(matched) / len(core_terms) < MIN_CORE_COVERAGE:
                continue

            # Offsets are absolute in the source document, not in the chunk.
            # chunk.text is exactly content[start_char:end_char], so the
            # relative offset maps straight through.
            leading = len(raw) - len(raw.lstrip())
            start = chunk.start_char + rel_start + leading
            end = start + len(cleaned)

            score = _score_sentence(cleaned, query_terms, index)
            if score < MIN_SENTENCE_SCORE:
                continue

            candidates.append(
                Sentence(
                    text=cleaned,
                    start_char=start,
                    end_char=end,
                    chunk=scored,
                    score=score,
                )
            )

    return candidates


def _dedupe(sentences: list[Sentence]) -> list[Sentence]:
    """Drop near-identical sentences.

    Overlapping chunks mean the same sentence can appear twice. Quoting it
    twice would look like two independent sources agreeing, which is
    misleading.
    """
    seen: set[str] = set()
    unique: list[Sentence] = []
    for sentence in sentences:
        fingerprint = " ".join(sentence.text.lower().split())
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        unique.append(sentence)
    return unique


def synthesise(
    question: str,
    scored_chunks: list[ScoredChunk],
    index: SearchIndex,
    *,
    max_sentences: int = MAX_SENTENCES,
) -> Synthesis:
    """Build an extractive answer with citations.

    Returns an empty answer and no citations when nothing in the retrieved
    chunks actually matches the question — the caller decides whether that
    means abstaining.
    """
    query_terms = set(tokenize(question))
    candidates = _candidate_sentences(scored_chunks, query_terms, index)

    if not candidates:
        return Synthesis(answer="", citations=[], mode="extractive")

    # Best sentence per chunk first, so the answer is not four sentences from
    # one document when three documents each have something to say.
    by_chunk: dict[int, Sentence] = {}
    for sentence in candidates:
        key = sentence.chunk.chunk.chunk_id
        current = by_chunk.get(key)
        if current is None or sentence.score > current.score:
            by_chunk[key] = sentence

    selected = sorted(by_chunk.values(), key=lambda s: -s.score)
    selected = _dedupe(selected)[:max_sentences]

    # Order the answer by document then position, so it reads in the order a
    # person would read the sources rather than by score.
    selected.sort(key=lambda s: (s.chunk.chunk.document_id, s.start_char))

    citations: list[Citation] = []
    parts: list[str] = []
    for marker, sentence in enumerate(selected, start=1):
        citations.append(
            Citation(
                marker=marker,
                document_id=sentence.chunk.chunk.document_id,
                document_title=sentence.chunk.chunk.document_title,
                filename=sentence.chunk.chunk.filename,
                chunk_ordinal=sentence.chunk.chunk.ordinal,
                start_char=sentence.start_char,
                end_char=sentence.end_char,
                quote=sentence.text,
                score=round(sentence.score, 4),
            )
        )
        parts.append(f"{sentence.text} [{marker}]")

    return Synthesis(answer=" ".join(parts), citations=citations, mode="extractive")
