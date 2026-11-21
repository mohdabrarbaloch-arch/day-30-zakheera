# API Reference

Base URL: `http://127.0.0.1:8000`

All endpoints return JSON. Authenticated endpoints expect:

```
Authorization: Bearer <access_token>
```

Interactive documentation is available at `/docs` (Swagger) and `/redoc` while
the server is running.

---

## Errors

Errors use a consistent shape:

```json
{
  "detail": "Human-readable message",
  "errors": [{"field": "password", "message": "String should have at least 8 characters"}]
}
```

`errors` is present only for validation failures.

| Status | Meaning |
|---|---|
| `400` | Malformed request |
| `401` | Missing, invalid or expired token; wrong credentials |
| `403` | Authenticated but not permitted (admin routes) |
| `404` | Not found — **or** it exists but belongs to someone else |
| `409` | Conflict (duplicate email) |
| `413` | File too large |
| `415` | Unsupported file type |
| `422` | Validation failed, or the file contained no text |
| `429` | Rate limit exceeded |

**On 404 vs 403:** a workspace or document belonging to another user returns
`404`, not `403`. Returning `403` would confirm the record exists, which is an
information leak. This is deliberate.

---

## Health

### `GET /api/health`

```json
{"status": "ok", "version": "1.0.0"}
```

---

## Authentication

### `POST /api/auth/register`

Creates an account. **The first registered user becomes an admin.**

```json
{
  "email": "you@example.com",
  "password": "at-least-8-characters",
  "display_name": "Your Name"
}
```

`201 Created`

```json
{
  "access_token": "eyJhbGciOi...",
  "token_type": "bearer",
  "user": {
    "id": 1,
    "email": "you@example.com",
    "display_name": "Your Name",
    "is_admin": true,
    "created_at": "2026-11-21T19:00:00"
  }
}
```

| Status | When |
|---|---|
| `409` | Email already registered |
| `422` | Invalid email, or password under 8 characters |

Passwords longer than 72 bytes are rejected — bcrypt silently truncates past
that, so accepting a longer one would mean ignoring part of it.

### `POST /api/auth/login`

```json
{"email": "you@example.com", "password": "your-password"}
```

`200 OK` — same body as register.

Returns `401` for both a wrong password and an unknown email, so the endpoint
cannot be used to discover which accounts exist.

### `GET /api/auth/me`

`200 OK` — the current user object.

---

## Workspaces

A workspace is a named document collection. Keep them focused: one topic per
workspace retrieves better than one large pile.

### `GET /api/workspaces`

`200 OK`

```json
[
  {
    "id": 1,
    "name": "Company Handbook",
    "slug": "company-handbook",
    "description": "HR, finance and security policies",
    "created_at": "2026-11-21T19:00:00"
  }
]
```

### `POST /api/workspaces`

```json
{"name": "Company Handbook", "description": "Optional"}
```

`201 Created` — the workspace object.

Slugs are generated from the name and deduplicated per user: a second
"Handbook" becomes `handbook-2`.

### `GET /api/workspaces/{workspace_id}`

`200 OK` — the workspace. `404` if it does not exist or is not yours.

### `DELETE /api/workspaces/{workspace_id}`

`204 No Content`. Deletes the workspace, its documents, its chunks and its
question history.

### `GET /api/workspaces/{workspace_id}/stats`

`200 OK`

```json
{
  "workspace": {"id": 1, "name": "Company Handbook", "slug": "company-handbook"},
  "document_count": 5,
  "chunk_count": 14,
  "total_chars": 8492,
  "question_count": 6,
  "answered_count": 5,
  "abstained_count": 1,
  "answer_rate": 0.8333,
  "median_latency_ms": 1
}
```

`answer_rate` is `answered_count / question_count`, or `0.0` when no questions
have been asked.

---

## Documents

### `POST /api/workspaces/{workspace_id}/documents/upload`

`multipart/form-data`

| Field | Type | Notes |
|---|---|---|
| `file` | file | Required. `.txt`, `.md`, `.markdown`, `.pdf`, `.docx` |
| `title` | string | Optional. Defaults to the filename |

`201 Created`

```json
{
  "id": 1,
  "title": "Employee Handbook — Leave and Attendance",
  "filename": "leave.md",
  "char_count": 2017,
  "chunk_count": 3,
  "size_bytes": 2048,
  "created_at": "2026-11-21T19:00:00"
}
```

| Status | When |
|---|---|
| `413` | File exceeds `MAX_UPLOAD_BYTES` (default 5 MB) |
| `415` | Extension not supported |
| `422` | File contained no extractable text (e.g. a scanned PDF) |

### `POST /api/workspaces/{workspace_id}/documents/text`

Ingest pasted text.

```json
{"title": "Meeting notes — Q4 planning", "content": "The full text..."}
```

`201 Created` — same shape as upload.

### `GET /api/workspaces/{workspace_id}/documents`

`200 OK` — a list of document objects.

### `GET /api/workspaces/{workspace_id}/documents/{document_id}`

`200 OK` — the document plus a `preview` field containing the first 2000
characters of extracted text.

### `DELETE /api/workspaces/{workspace_id}/documents/{document_id}`

`204 No Content`. Removes the document, its chunks, and invalidates the index.

---

## Query

### `POST /api/workspaces/{workspace_id}/query`

The main endpoint.

```json
{
  "question": "How many annual leave days do I get?",
  "top_k": 8
}
```

| Field | Type | Default | Notes |
|---|---|---|---|
| `question` | string | — | Required, 3–1000 characters |
| `top_k` | integer | 8 | 1–20. Passages to retrieve |

`200 OK` — **answered:**

```json
{
  "answered": true,
  "answer": "Every full-time employee is entitled to twenty-four paid annual leave days per calendar year. [1]",
  "citations": [
    {
      "marker": 1,
      "document_id": 1,
      "document_title": "Employee Handbook — Leave and Attendance",
      "start_char": 48,
      "end_char": 141,
      "quote": "Every full-time employee is entitled to twenty-four paid annual leave days per calendar year."
    }
  ],
  "coverage": 1.0,
  "coverage_threshold": 0.5,
  "missing_terms": [],
  "retrieved": [
    {
      "chunk_id": 1,
      "document_id": 1,
      "document_title": "Employee Handbook — Leave and Attendance",
      "chunk_ordinal": 0,
      "start_char": 0,
      "end_char": 512,
      "text": "…",
      "bm25_score": 4.21,
      "vector_score": 0.63,
      "fused_score": 0.0328
    }
  ],
  "latency_ms": 6,
  "synthesis": "extractive"
}
```

`200 OK` — **abstained:**

```json
{
  "answered": false,
  "answer": "",
  "citations": [],
  "coverage": 0.35,
  "coverage_threshold": 0.5,
  "missing_terms": ["cryptocurrency", "trade"],
  "abstain_reason": "Only 35% of this question's meaningful terms appear anywhere in the corpus (the threshold is 50%). Not found: 'cryptocurrency', 'trade'. This knowledge base does not appear to cover it.",
  "retrieved": [],
  "latency_ms": 0,
  "synthesis": "extractive"
}
```

**Abstention returns `200`, not an error.** It is a successful answer to the
question "does this corpus cover this?" — the answer is no.

#### The citation guarantee

For every citation:

```python
document_text[citation["start_char"] : citation["end_char"]] == citation["quote"]
```

This is asserted in `tests/test_synthesizer.py` and
`tests/test_api.py::TestQuery::test_citation_offsets_point_at_the_quote`. If it
ever fails, the product's central claim is false.

#### Reading the scores

| Field | Meaning |
|---|---|
| `bm25_score` | Lexical score. High when the chunk contains rare query terms |
| `vector_score` | TF-IDF cosine similarity, 0–1. High when vocabulary overlaps |
| `fused_score` | RRF score, `Σ 1/(60 + rank)`. Comparable across queries |

`fused_score` is what determines the order. It is built from *ranks*, not raw
scores, because BM25 and cosine similarity live on different scales.

### `GET /api/workspaces/{workspace_id}/questions`

`200 OK` — question history, newest first.

```json
[
  {
    "id": 6,
    "question": "How many annual leave days do I get?",
    "answered": true,
    "coverage": 1.0,
    "latency_ms": 6,
    "created_at": "2026-11-21T19:05:00"
  }
]
```

---

## Admin

### `GET /api/admin/stats`

Requires an admin account. `403` otherwise.

```json
{
  "user_count": 1,
  "workspace_count": 1,
  "document_count": 5,
  "chunk_count": 14,
  "question_count": 6,
  "answered_count": 5,
  "answer_rate": 0.8333,
  "top_questions": [
    {"question": "How many annual leave days do I get?", "count": 2}
  ]
}
```

---

## Rate limits

A sliding window per client IP, default 60 requests per minute
(`RATE_LIMIT_PER_MINUTE`). Exceeding it returns `429`.

The limiter is per-process, so N workers allow N× the limit. Redis is the fix
for a multi-worker deployment.

---

## Configuration that affects the API

| Variable | Default | Effect |
|---|---|---|
| `ABSTAIN_COVERAGE_THRESHOLD` | `0.5` | Lower answers more questions and risks more wrong answers |
| `MAX_UPLOAD_BYTES` | `5242880` | Upload size limit |
| `CHUNK_TARGET_CHARS` | `900` | Larger chunks give more context, less precision |
| `CHUNK_OVERLAP_CHARS` | `150` | Overlap between consecutive chunks |
| `DEFAULT_TOP_K` | `8` | Passages retrieved when `top_k` is omitted |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `1440` | Token lifetime |
