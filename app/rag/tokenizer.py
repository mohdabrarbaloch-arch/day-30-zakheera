"""Text normalisation and tokenisation.

The corpus this is built for is bilingual — English documents with Urdu
words mixed in, which is how most Pakistani organisations actually write. So
the tokeniser has to handle both scripts without pretending one is the other.

Design notes:

* Urdu is written right-to-left but its letters are still letters, so the
  same ``\\w`` class picks them up. What differs is that Urdu does not use
  the Latin stopword list, and its words are not stemmed by an English
  stemmer — running Porter over Urdu would corrupt it.
* A light suffix stripper handles English plurals and common verb endings.
  It is deliberately conservative: over-stemming merges distinct terms and
  quietly destroys precision, which is worse than missing a match.
"""

from __future__ import annotations

import re
import unicodedata

# Words that carry no retrieval signal. Kept short on purpose — an aggressive
# stoplist removes terms that distinguish one document from another.
ENGLISH_STOPWORDS = frozenset(
    """
    a about above after again against all am an and any are aren't as at be
    because been before being below between both but by can cannot could
    couldn't did didn't do does doesn't doing don't down during each few for
    from further had hadn't has hasn't have haven't having he he'd he'll he's
    her here here's hers herself him himself his how how's i i'd i'll i'm
    i've if in into is isn't it it's its itself let's me more most mustn't my
    myself no nor not of off on once only or other ought our ours ourselves
    out over own same shan't she she'd she'll she's should shouldn't so some
    such than that that's the their theirs them themselves then there there's
    these they they'd they'll they're they've this those through to too under
    until up very was wasn't we we'd we'll we're we've were weren't what
    what's when when's where where's which while who who's whom why why's
    with won't would wouldn't you you'd you'll you're you've your yours
    yourself yourselves many much get got gets getting also just
    """.split()
)

# Urdu function words. These are the equivalents of "the", "of", "is" — they
# appear in nearly every sentence and would otherwise dominate the BM25 score.
URDU_STOPWORDS = frozenset(
    """
    کا کی کے کو میں سے پر اور ہے ہیں تھا تھی تھے نے یہ وہ جو بھی نہیں ہو
    کر کیا گیا گیے ہوا ہوئی ایک اس ان جن تک بھر لیے والا والی والے ساتھ
    اب یہاں وہاں کیسے کیوں کب کہاں کون
    """.split()
)

STOPWORDS = ENGLISH_STOPWORDS | URDU_STOPWORDS

# A token is a run of letters/digits, optionally with internal apostrophes or
# hyphens ("don't", "state-of-the-art"). Underscores are excluded so that
# snake_case identifiers split into their parts.
_TOKEN_RE = re.compile(r"[^\W_]+(?:['\-][^\W_]+)*", re.UNICODE)

# Urdu/Arabic diacritics (harakat) are optional vowel marks. Two spellings of
# the same word differ only by these, so stripping them makes matching work.
_DIACRITICS_RE = re.compile(r"[\u064B-\u0652\u0670\u0640]")

# Characters that look like ASCII but are not, and would otherwise create
# near-duplicate tokens that never match.
_FOLD_MAP = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2013": "-",
        "\u2014": "-",
        "\u00a0": " ",
    }
)

_SUFFIXES = (
    "ational",
    "ization",
    "iveness",
    "fulness",
    "ousness",
    "alities",
    "ations",
    "ements",
)

MIN_STEM_LENGTH = 4

_VOWELS = frozenset("aeiou")


def normalise(text: str) -> str:
    """Fold typographic variants and strip Urdu diacritics.

    Applied to both the corpus and the query, so the two always meet in the
    same space.
    """
    folded = unicodedata.normalize("NFKC", text).translate(_FOLD_MAP)
    return _DIACRITICS_RE.sub("", folded)


def stem(word: str) -> str:
    """Reduce an English word to a common root, conservatively.

    Only Latin-script words are touched — running an English stemmer over
    Urdu would corrupt it, since Urdu has no such morphology.

    The rules are deliberately narrow. An earlier version stripped any
    suffix in a list, which turned "trading" into "trad" — a string that
    then matched nothing, including "trade". Over-stemming silently merges
    unrelated terms and destroys precision, so the rules here only fire when
    the result is a plausible word:

    * plurals: "policies" -> "policy", "expenses" -> "expense"
    * verb forms: "trading" -> "trade", "reported" -> "report",
      "running" -> "run"

    A word is never shortened below ``MIN_STEM_LENGTH`` characters.
    """
    if not word.isascii() or len(word) < MIN_STEM_LENGTH:
        return word

    # --- Plurals ---------------------------------------------------------
    # A possessive is not a plural: stripping the "s" from "company's"
    # leaves a dangling apostrophe that matches nothing.
    if word.endswith("'s") and len(word) >= 4:
        return word[:-2]
    if word.endswith("ies") and len(word) >= 5:
        return word[:-3] + "y"
    if word.endswith("sses"):
        return word[:-2]
    if word.endswith("s") and not word.endswith(("ss", "us", "is", "'")) and len(word) >= 4:
        return word[:-1]

    # --- Verb forms ------------------------------------------------------
    for suffix in ("ing", "ed"):
        if not word.endswith(suffix):
            continue
        base = word[: -len(suffix)]
        if len(base) < 3:
            # "using" -> "us" would collide with a stopword; leave it alone.
            return word
        # Doubled consonant: "running" -> "runn" -> "run".
        if len(base) >= 2 and base[-1] == base[-2] and base[-1] not in _VOWELS:
            return base[:-1]
        # Silent-e restoration: "trading" -> "trad" -> "trade".
        if len(base) >= 4 and base[-1] not in _VOWELS and base[-2] in _VOWELS:
            return base + "e"
        return base

    return word


def tokenize(text: str, *, remove_stopwords: bool = True, apply_stemming: bool = True) -> list[str]:
    """Split text into normalised, lowercased tokens.

    Stopword removal and stemming are both optional because the chunker and
    the citation builder need the raw tokens, while the index needs the
    reduced ones.
    """
    tokens: list[str] = []
    for raw in _TOKEN_RE.findall(normalise(text)):
        token = raw.lower()
        if remove_stopwords and token in STOPWORDS:
            continue
        if apply_stemming:
            token = stem(token)
        if token:
            tokens.append(token)
    return tokens


def term_frequencies(tokens: list[str]) -> dict[str, int]:
    """Count occurrences, preserving first-seen order for stable output."""
    counts: dict[str, int] = {}
    for token in tokens:
        counts[token] = counts.get(token, 0) + 1
    return counts


def split_sentences(text: str) -> list[tuple[int, int]]:
    """Return ``(start, end)`` offsets of sentences in ``text``.

    Offsets are what make citations verifiable, so this never rebuilds the
    string — it only reports where the pieces already are. Splits on
    ``. ! ?`` and the Urdu full stop ``۔``, and treats a blank line as a
    boundary too.
    """
    spans: list[tuple[int, int]] = []
    start = 0
    length = len(text)

    for index, char in enumerate(text):
        is_terminator = char in ".!?\u06d4"
        is_blank_line = char == "\n" and index + 1 < length and text[index + 1] == "\n"

        if is_terminator or is_blank_line:
            end = index + 1
            if text[start:end].strip():
                spans.append((start, end))
            start = end

    if start < length and text[start:].strip():
        spans.append((start, length))

    return spans
