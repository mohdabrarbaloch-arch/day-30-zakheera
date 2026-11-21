# Setup

## Requirements

- Python 3.11 or newer
- pip
- Optional: Docker and Docker Compose, if you would rather not install Python

No Node, no build step, no external services. The frontend is plain HTML, CSS
and JavaScript served by the same process as the API.

---

## Local setup

### 1. Clone and enter the project

```bash
git clone https://github.com/mohdabrarbaloch-arch/day-30-zakheera.git
cd day-30-zakheera
```

### 2. Create a virtual environment

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure the environment

```bash
cp .env.example .env
```

Generate a secret key and append it:

```bash
python -c "import secrets; print('SECRET_KEY=' + secrets.token_urlsafe(48))" >> .env
```

The app will not start in production mode without a `SECRET_KEY`. In
development it falls back to a placeholder and prints a warning.

### 5. Seed the demo data

```bash
python -m scripts.seed
```

This creates a demo account and a five-document corpus — a fictional company
handbook covering leave, expenses, security, on-call, and an Urdu summary. It
is idempotent: running it twice will not duplicate anything.

```
Sign in with:
  email:    abraz@example.com
  password: zakheera-demo-2026
```

### 6. Run it

```bash
uvicorn app.main:app --reload
```

Open **http://127.0.0.1:8000**.

---

## Docker setup

```bash
export SECRET_KEY=$(python -c "import secrets; print(secrets.token_urlsafe(48))")
docker compose up --build
```

This starts PostgreSQL and the app. The app waits for the database healthcheck
before starting, so the first boot takes a few seconds longer.

To seed inside the container:

```bash
docker compose exec app python -m scripts.seed
```

Then open **http://127.0.0.1:8000**.

To stop and remove the database volume:

```bash
docker compose down -v
```

---

## Verifying the install

```bash
curl http://127.0.0.1:8000/api/health
# {"status":"ok","version":"1.0.0"}
```

Then sign in and ask a question:

```bash
TOKEN=$(curl -s -X POST http://127.0.0.1:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"abraz@example.com","password":"zakheera-demo-2026"}' \
  | python -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

curl -s -X POST http://127.0.0.1:8000/api/workspaces/1/query \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"question":"How many annual leave days do I get?"}' \
  | python -m json.tool
```

You should get `"answered": true` with citations. Now try one it cannot answer:

```bash
curl -s -X POST http://127.0.0.1:8000/api/workspaces/1/query \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"question":"What is the policy on cryptocurrency trading?"}' \
  | python -m json.tool
```

You should get `"answered": false` with `missing_terms` naming what it could
not find. That is the feature working.

---

## Running the tests

```bash
pip install -r requirements-dev.txt
pytest
```

186 tests, about 15 seconds, no database and no network required. Each test
gets a fresh in-memory SQLite database.

```bash
pytest -v                          # verbose
pytest tests/test_synthesizer.py   # one file
pytest -k abstain                  # by name
```

Linting and formatting:

```bash
ruff check .
ruff format --check .
```

---

## Troubleshooting

**`SECRET_KEY must be set` on startup**
You are running with `ENVIRONMENT=production` and no secret. Either set one in
`.env` or run with `ENVIRONMENT=development`.

**`ModuleNotFoundError: No module named 'app'`**
Run commands from the project root, not from inside `app/`.

**Port 8000 already in use**
```bash
uvicorn app.main:app --reload --port 8001
```

**A PDF uploads but returns no text**
It is probably a scan with no text layer. Zakheera says so in the response
rather than indexing nothing. Run it through OCR first (e.g. `ocrmypdf`).

**A question I know the answer to gets abstained**
The retriever is lexical — it matches words, not meanings. If your question
uses different vocabulary from the document, it will abstain rather than guess.
Rephrase using words that appear in the document, or lower
`ABSTAIN_COVERAGE_THRESHOLD` in `.env` (at the cost of more wrong answers).

**Docker: the app exits immediately**
Check that `SECRET_KEY` is exported in the shell you ran `docker compose` from.
`docker compose logs app` will show the reason.

---

## Project layout

```
app/
  main.py            FastAPI app, middleware, error handlers
  config.py          Settings from the environment
  database.py        Engine, session, base
  models.py          SQLAlchemy models
  schemas.py         Request/response models
  security.py        bcrypt + JWT
  deps.py            Auth, ownership, rate limiting
  services.py        Ingest, query, index cache, stats
  rag/
    tokenizer.py     Normalisation, tokenising, stemming
    chunker.py       Overlapping chunks with exact offsets
    embedder.py      TF-IDF vectors, cosine similarity
    index.py         Inverted index, BM25, coverage
    retriever.py     Hybrid retrieval, RRF fusion
    synthesizer.py   Extractive answers with citations
    extractors.py    txt / md / pdf / docx to text
  routers/           auth, workspaces, documents, query, admin
  static/            index.html, styles.css, app.js
scripts/seed.py      Demo account and corpus
tests/               186 tests
docs/                Setup, API reference, screenshots
```
