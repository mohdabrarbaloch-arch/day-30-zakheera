"""Tests for tokenisation and stemming.

The stemmer is the component most likely to be quietly wrong: an over-eager
rule merges unrelated words and destroys precision without ever raising an
error. These tests pin the specific cases that were wrong during development.
"""

from __future__ import annotations

import pytest

from app.rag.tokenizer import (
    normalise,
    split_sentences,
    stem,
    term_frequencies,
    tokenize,
)


class TestStemming:
    @pytest.mark.parametrize(
        ("word", "expected"),
        [
            # Plurals
            ("policies", "policy"),
            ("expenses", "expense"),
            ("days", "day"),
            ("documents", "document"),
            # Verb forms, including silent-e restoration
            ("trading", "trade"),
            ("reported", "report"),
            ("approved", "approve"),
            ("running", "run"),
            ("submitted", "submit"),
            # Already a root
            ("leave", "leave"),
            ("security", "security"),
            ("process", "process"),
        ],
    )
    def test_stem_expected_roots(self, word, expected):
        assert stem(word) == expected

    def test_stem_is_idempotent(self):
        """Stemming an already-stemmed word must not shorten it further."""
        for word in ["policy", "expense", "trade", "report", "run", "submit"]:
            assert stem(stem(word)) == stem(word)

    def test_stem_does_not_corrupt_urdu(self):
        """Urdu has no English morphology; the stemmer must leave it alone."""
        for word in ["چھٹی", "ملازم", "پاس", "رسید"]:
            assert stem(word) == word

    def test_stem_never_returns_empty(self):
        for word in ["a", "is", "s", "ed", "ing", "ies"]:
            assert stem(word) != ""

    def test_short_words_untouched(self):
        assert stem("cat") == "cat"
        assert stem("run") == "run"


class TestTokenize:
    def test_lowercases_and_splits(self):
        assert tokenize("The Quick Brown Fox") == ["quick", "brown", "fox"]

    def test_removes_stopwords(self):
        tokens = tokenize("what is the policy for the leave")
        assert "the" not in tokens
        assert "is" not in tokens
        assert "policy" in tokens

    def test_keeps_urdu_content_words(self):
        tokens = tokenize("چھٹی کتنے دن ملتی ہے")
        assert "چھٹی" in tokens
        assert "دن" in tokens
        # Urdu stopwords are removed
        assert "ہے" not in tokens

    def test_splits_snake_case(self):
        assert tokenize("user_id") == ["user", "id"]

    def test_keeps_hyphenated_and_apostrophes(self):
        assert "state-of-the-art" in tokenize("a state-of-the-art system")
        # The apostrophe survives tokenising; stemming then reduces the
        # possessive to its base noun, which is what we want for matching.
        assert "company's" in tokenize("the company's policy", apply_stemming=False)
        assert "company" in tokenize("the company's policy")

    def test_numbers_are_kept(self):
        assert "15" in tokenize("up to 15 days")

    def test_stopwords_can_be_kept(self):
        tokens = tokenize("the policy", remove_stopwords=False)
        assert "the" in tokens

    def test_stemming_can_be_disabled(self):
        assert "policies" in tokenize("policies", apply_stemming=False)
        assert "policy" in tokenize("policies", apply_stemming=True)

    def test_empty_input(self):
        assert tokenize("") == []
        assert tokenize("   \n\t  ") == []


class TestNormalise:
    def test_folds_smart_quotes(self):
        assert normalise("it\u2019s") == "it's"

    def test_strips_urdu_diacritics(self):
        # Same word with and without harakat must normalise identically.
        assert normalise("کِتاب") == normalise("کتاب")

    def test_collapses_nbsp(self):
        assert normalise("a\u00a0b") == "a b"


class TestTermFrequencies:
    def test_counts_occurrences(self):
        assert term_frequencies(["a", "b", "a", "a"]) == {"a": 3, "b": 1}

    def test_empty(self):
        assert term_frequencies([]) == {}


class TestSplitSentences:
    def test_splits_on_terminators(self):
        text = "First sentence here. Second one follows! Third?"
        spans = split_sentences(text)
        assert len(spans) == 3
        assert text[spans[0][0] : spans[0][1]].strip() == "First sentence here."

    def test_offsets_are_exact(self):
        text = "Alpha one. Beta two. Gamma three."
        for start, end in split_sentences(text):
            assert text[start:end].strip() in {"Alpha one.", "Beta two.", "Gamma three."}

    def test_splits_on_urdu_full_stop(self):
        text = "پہلا جملہ۔ دوسرا جملہ۔"
        assert len(split_sentences(text)) == 2

    def test_blank_line_is_a_boundary(self):
        text = "A heading\n\nA paragraph that follows."
        assert len(split_sentences(text)) == 2

    def test_empty_text(self):
        assert split_sentences("") == []
