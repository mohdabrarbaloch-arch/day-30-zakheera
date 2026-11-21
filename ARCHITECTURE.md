# Architecture

## What this is

Zakheera is a retrieval-augmented question answering system over a private
document collection. You upload documents, ask questions in plain English or
Urdu, and get answers assembled from the documents themselves — with the exact
character range each sentence came from.

The design goal that shapes everything else: **the system must be able to say
"I don't know."** A knowledge base that confidently invents an answer is worse
than no knowledge base at all, because you cannot tell the difference between
a right answer and a plausible one. So abstention is a first-class feature, not
an error path.

## System diagram

```
┌──────────────────────────────────────────────────────────────────────┐
│  Browser — single-page app, no build step                            │
│  ┌────────────┬──────────────┬───────────────┬──────────────────┐    │
│  │ Auth       │ Workspaces   │ Ask + answer  │ Admin overview   │    │
│  └────────────┴──────────────┴───────────────┴──────────────────┘    │
│  vanilla JS · fetch · localStorage token · escaped rendering         │
└───────────────────────────────┬──────────────────────────────────────┘
                                │ JSON over HTTP
┌───────────────────────────────▼──────────────────────────────────────┐
│  FastAPI                                                             │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │ Routers: auth · workspaces · documents · query · admin         │  │
│  ├────────────────────────────────────────────────────────────────┤  │
│  │ Dependencies: JWT auth · ownership checks · rate limiting      │  │
│  ├────────────────────────────────────────────────────────────────┤  │
│  │ Services: ingest · query · index cache · stats                 │  │
│  └────────────────────────────────────────────────────────────────┘  │
└──────┬──────────────────────────────────────────┬────────────────────┘
       │ SQLAlchemy                               │ in-process
┌──────▼──────────────┐              ┌────────────▼────────────────────┐
│  PostgreSQL         │              │  RAG engine (pure functions)    │
│  (SQLite fallback)  │              │                                 │
│                     │              │  tokenizer → chunker → embedder │
│  users              │              │      ↓                          │
│  workspaces         │              │  SearchIndex                    │
│  documents          │              │   ├─ inverted index (BM25)      │
│  chunks             │              │   └─ TF-IDF vectors             │
│  questions          │              │      ↓                          │
└─────────────────────┘              │  retriever (RRF fusion)         │
                                     │      ↓                          │
                                     │  synthesizer (extractive)       │
                                     └─────────────────────────────────┘
```

## The retrieval pipeline

### 1. Extraction

`app/rag/extractors.py` turns an uploaded file into plain text. Supported:
`.txt`, `.md`, `.pdf` (via pypdf), `.docx` (via python-docx, including tables).
Each extractor returns text plus an optional warning — a scanned PDF with no
text layer returns an empty string *and* a warning saying OCR is needed, rather
than silently indexing nothing.

### 2. Tokenisation

`app/rag/tokenizer.py` normalises and tokenises. Two things make this
non-trivial:

- **The corpus is bilingual.** Urdu has no English morphology, so the stemmer
  only touches Latin-script words. Running an English suffix-stripper over Urdu
  would corrupt it.
- **Over-stemming is silent and destructive.** An early version stripped any
  suffix in a list, turning "trading" into "trad" — a string that then matched
  nothing, including "trade". The current rules are narrow and only fire when
  the result is a plausible word: plurals, verb forms with silent-e restoration
  ("trading" → "trade"), and doubled consonants ("running" → "run").

### 3. Chunking

`app/rag/chunker.py` splits text into overlapping chunks. The invariant that
matters: **every chunk is an exact slice of the source**, so
`source[start_char:end_char] == chunk.text`. Citations are character offsets,
so if this ever breaks, every citation points at the wrong place.

Overlap is applied in characters, snapped forward to a word boundary. An
earlier version rewound by whole paragraph units, which meant the overlap was
either a full paragraph or — when the last unit was long — nothing at all,
because the rewind condition could never be satisfied.

### 4. Indexing

`app/rag/index.py` builds two structures over the chunks:

- **An inverted index** for BM25. BM25 is a lexical ranker: it rewards rare
  terms and saturates on repetition, which is exactly what you want for
  questions containing specific identifiers, amounts or names.
- **TF-IDF vectors**, L2-normalised, for cosine similarity. This catches
  documents that share vocabulary with the question even when the exact terms
  differ in frequency.

Both are needed. BM25 is precise but brittle to phrasing; vectors are fuzzier
but more forgiving. Neither alone is good enough.

### 5. Retrieval

`app/rag/retriever.py` runs both rankers and fuses them with **Reciprocal Rank
Fusion**:

```
score(chunk) = Σ  1 / (k + rank_i(chunk))     k = 60
             rankers
```

RRF combines *ranks*, not scores. That matters because BM25 scores and cosine
similarities live on completely different scales — adding them directly would
let whichever happens to have the larger range dominate. Ranks are comparable
by construction.

### 6. Synthesis

`app/rag/synthesizer.py` produces the answer. It is **extractive**: it selects
sentences from the retrieved chunks and quotes them verbatim, with a marker
`[1]`, `[2]` pointing at a citation carrying the document, the exact character
range, and the quote.

A sentence is only quoted if it:

- is at least 40 characters (shorter ones are headings or stray numbers),
- contains at least **half** the question's core terms,
- scores above a relevance floor, and
- does not begin mid-sentence (which happens at chunk boundaries).

That third condition was added after watching "Expenses must be submitted
within thirty days" get quoted in answer to "how many annual leave days do I
get" — it shares the word "days" and nothing else.

## Abstention

This is the part worth reading carefully.

Before answering, the system computes **corpus coverage**: the IDF-weighted
share of the question's terms that appear *anywhere in the corpus*. If coverage
is below the threshold (default 0.5), it abstains and names the missing terms.

Two subtleties, both learned by watching it fail:

**Coverage is measured against the whole corpus, not the retrieved chunks.**
If a term is absent from the corpus, no amount of retrieval will find it. The
honest answer is "this knowledge base does not discuss that."

**Absent terms are capped at the highest IDF actually observed.** Without the
cap, a term with zero occurrences gets the theoretical maximum IDF and can
dominate the score on its own. That made "how long do customers have to request
a refund" abstain, because "long" happened to be missing while every meaningful
term was present. We cannot know how rare an unseen term is, so the honest
assumption is "as rare as the rarest thing we have seen", not "infinitely rare".

### The known limitation

This is a **lexical** retriever. It matches words, not meanings. Ask "how long
is the refund window?" against a policy that says "within thirty days of
delivery" and it will abstain, because "window" never appears. A dense
embedding model would handle that; TF-IDF cannot.

The trade-off is deliberate: a lexical retriever's failures are *visible* —
it tells you which term it could not find — whereas a dense model's failures
are confident and wrong. For a tool whose promise is "check the citation
yourself", visible failure is the better failure. `tests/test_api.py` pins this
behaviour so it cannot regress silently.

## Data model

```
users ──┬── workspaces ──┬── documents ── chunks
        │                │
        └── questions ───┘
```

- **users** — email, bcrypt password hash, display name, admin flag. The first
  registered user becomes admin.
- **workspaces** — a named document collection, owned by one user. Slugs are
  unique per user, deduplicated with a numeric suffix.
- **documents** — the extracted text, its character count, and the original
  filename and size.
- **chunks** — the chunk text with its `start_char`/`end_char` offsets into the
  parent document. Persisted so the index can be rebuilt without re-extracting.
- **questions** — every question asked, whether it was answered, the coverage
  score, and the latency. This is what powers the admin view and the answer
  rate metric.

## Index caching

Building the index is O(corpus). It is cached in memory per workspace and
invalidated by a version counter that increments on every ingest or delete.
The cache is per-process, so a multi-worker deployment rebuilds it once per
worker — acceptable at this scale, and the obvious place to add Redis.

## Security

- **Passwords** — bcrypt, with a per-hash salt. Passwords longer than 72 bytes
  are rejected rather than silently truncated, because bcrypt ignores the rest.
- **Tokens** — HS256 JWT with an expiry. A tampered or foreign-signed token is
  rejected.
- **Ownership** — every workspace and document lookup is scoped to the
  requesting user. A resource belonging to someone else returns **404, not
  403**, so the API never confirms that a record exists to someone who should
  not see it.
- **Account enumeration** — a login for an unknown email returns the same 401
  as a wrong password.
- **Validation errors** — FastAPI's default error body echoes the raw input,
  which would put a submitted password in the response. A custom handler strips
  it.
- **Rate limiting** — a sliding window per client IP on the write and query
  endpoints.
- **Rendering** — the frontend escapes everything before it reaches the DOM.
  Document content and questions are user-supplied; a knowledge base that
  renders a colleague's uploaded file as HTML is a stored-XSS hole.
- **Secrets** — only from the environment. The app refuses to start in
  production without a `SECRET_KEY`.

## Scaling notes

What is already fine:

- The RAG engine is pure functions over dataclasses with no database or I/O, so
  it is trivially testable and horizontally scalable.
- The index is rebuilt from persisted chunks, so a new worker needs no shared
  state beyond the database.

What would need work first:

| Concern | Current | Next step |
|---|---|---|
| Index cache | per-process | Redis, or a shared vector store |
| Rate limiting | per-process | Redis-backed limiter |
| Schema changes | `create_all()` | Alembic migrations |
| Retrieval quality | lexical only | hybrid lexical + dense embeddings |
| Large corpora | in-memory index | pgvector or a dedicated vector DB |
| Ingest | synchronous | background queue for large files |

The retrieval interface is the seam. `retrieve()` takes an index and a question
and returns ranked chunks; swapping TF-IDF for a dense encoder means
implementing the same two methods (`vector_scores`, `corpus_coverage`) and
nothing else changes.
