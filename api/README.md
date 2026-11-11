# My Exp API

Backend of the personal/family expense assistant: it receives Brazilian receipts
(NFC-e), invoices (NF-e), utility bills and other documents, extracts the financial
entries and answers questions about spending, citing the original documents.

FastAPI · SQLAlchemy 2 · PostgreSQL (RLS) · Azure Blob · Qdrant · LangChain · free LLMs (Gemini, Groq, Cloudflare, OpenRouter)

## Architecture

```
Router → Schema → Service → Repository → Model → PostgreSQL
                     │
                     ├─ Azure Blob (files, private container, temporary SAS URLs)
                     ├─ Qdrant (semantic search, always filtered by account_id)
                     └─ LLM router (free models from several providers, with fallback)

worker (python -m worker) ← ingestion_jobs queue (PostgreSQL, FOR UPDATE SKIP LOCKED)
```

- **Multi-tenant:** each `account` is a tenant. Every domain table has RLS
  (`FORCE`); the API runs as `myexp_app` and the worker as `myexp_worker`, neither with
  `BYPASSRLS`. Non-members of an account get **404**.
- **Ingestion:** upload → blob + document + `ingest` job → the worker extracts
  (NF-e/NFC-e from XML; PDF/image through an LLM) → entries and items → `index` job
  → Qdrant.
- **Assistant:** the LLM only picks a tool and its parameters; the numbers
  come from fixed SQL. `[D#]` citations are verified by code.
- **AI from several free providers** (`services/llm_providers.py`): Gemini, Groq and
  Cloudflare Workers AI (OpenAI-compatible API) and OpenRouter. Two lists in `.env`,
  `provider:model` in order of preference: `LLM_VISION_MODELS` (worker) and
  `LLM_TEXT_MODELS` (assistant). Per-minute limit → short wait; daily limit →
  pause until the reset (the whole provider when the quota is account-wide: OpenRouter, Cloudflare).
  An inconsistent extraction (items sum ≠ total) is redone by the next model.

## Running locally

Requirements: Python 3.13, PostgreSQL 16+, Qdrant, [golang-migrate](https://github.com/golang-migrate/migrate).

```bash
python -m venv .venv && .venv/Scripts/activate   # Linux/macOS: source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env                              # fill in the values

make migrate_up                                   # migration role (MIGRATE_DB_*)
# once, with the migration role:
#   ALTER ROLE myexp_app    WITH PASSWORD '...';
#   ALTER ROLE myexp_worker WITH PASSWORD '...';

make run_api      # http://127.0.0.1:8000/docs (in development)
make run_worker   # another terminal
```

The migration role must own the database objects and bypass RLS (`BYPASSRLS` or
superuser): the worker's `SECURITY DEFINER` functions need to see jobs from every account.

Production: `ENVIRONMENT=production` (the default), behind a reverse proxy with HTTPS.
Also configure the body size limit on the proxy (e.g. nginx `client_max_body_size 11m`).

## Authentication (for the frontend)

1. The frontend signs in with **Google Identity Services** and gets the ID token (`credential`).
2. `POST /auth/google` `{"id_token": "..."}` →
   ```json
   {"access_token": "mxa_...", "refresh_token": "mxr_...", "token_type": "bearer",
    "expires_in": 900, "refresh_expires_in": 2592000, "user": {...}}
   ```
3. Requests: `Authorization: Bearer <access_token>`.
4. On **401**, call `POST /auth/refresh` `{"refresh_token": "..."}` once and
   retry the request. **Every refresh returns a new pair; keep the new refresh
   token.** If the previous refresh token is presented again within 30 s (parallel
   requests), the answer is **409**: use the newest pair. After that, presenting it again
   revokes the session. If the refresh returns 401, go back to login.
5. `POST /auth/logout` ends the session (`?everywhere=true`: all of them).

Tokens are opaque (random, 256 bits) and only their SHA-256 is stored, so logout and
revocation take effect immediately — no JWT signing secret to protect.

The first login creates the user. Invitations (`POST /accounts/{id}/members/` with an
e-mail that has no account yet) stay `pending: true` and are linked on the first
Google login with that e-mail.

**Who can sign in (closed platform):**
- administrators (`AUTH_ADMIN_EMAILS` in `.env`);
- e-mails they allowed (`/admin/access`, the Admin screen in the frontend);
- people invited to an account — but invited users **cannot invite** anyone (403).

Whoever loses access is dropped on the next session refresh (within 15 min).

With `ENVIRONMENT=development`, without Google:
- `POST /auth/dev-login` `{"email": "...", "name": "..."}` returns the **same tokens**
  as `/auth/google` (use it in the frontend during development);
- or `POST /users/` + the `X-Dev-User-Id: <uuid>` header (quick tests/Swagger).

Neither proves identity, and both answer 404 in production.

## Endpoints

| Area | Routes |
|---|---|
| Admin | `GET/POST /admin/access`, `DELETE /admin/access/{id}` (administrators only) |
| Auth | `POST /auth/google`, `POST /auth/refresh`, `POST /auth/logout`, `POST /auth/dev-login` (dev) |
| Users | `GET /users/me`, `GET /users/`, `GET /users/{id}` |
| Accounts | `POST /accounts/`, `GET /accounts/me`, `GET /accounts/{id}` |
| Members | `GET/POST /accounts/{id}/members/`, `PATCH/DELETE /accounts/{id}/members/{user_id}` |
| Categories | `GET/POST /accounts/{id}/categories/` |
| Documents | `POST/GET /accounts/{id}/documents/`, `GET/DELETE .../{doc_id}`, `POST .../{doc_id}/reprocess`, `GET .../{doc_id}/download` |
| Entries | `GET /accounts/{id}/entries/` (filters + pagination), `GET/PATCH .../entries/{entry_id}` (category) |
| Dashboard | `GET /accounts/{id}/reports/summary?start=&end=` |
| Prices | `GET /accounts/{id}/items/?q=leite&order=cheapest\|latest` |
| Search | `GET /accounts/{id}/search/?q=` |
| Assistant | `POST/GET /accounts/{id}/conversations/`, `GET/POST .../{conversation_id}/messages` |
| Health | `GET /health` |

Details and schemas: `/docs` (Swagger) or `/openapi.json`.

**Errors:** `{"detail": "message"}` (422 carries Pydantic's error list).
Monetary values arrive as a **decimal string** (`"63.43"`), with no floating point error.
`make openapi` exports the contract to `../web/openapi.json`.
**429** = rate limited, with `Retry-After` in seconds:

| Route | Limit |
|---|---|
| `POST /auth/google` | 10/min per IP |
| `POST /auth/refresh` | 30/min per IP |
| Assistant questions | 20/min per user |
| Semantic search | 30/min per user |
| Upload | 30/min per user |

## Tests

```bash
python -m pytest -q          # unit tests; integration tests are skipped without a database
```

Integration (a **disposable** database; some tests `TRUNCATE`):

```bash
docker run --rm -d --name myexp-test-pg -e POSTGRES_PASSWORD=postgres \
  -e POSTGRES_DB=myexp_test -p 127.0.0.1:55432:5432 postgres:17-alpine
migrate -database "postgres://postgres:postgres@127.0.0.1:55432/myexp_test?sslmode=disable" -path migrations up
docker exec myexp-test-pg psql -U postgres -d myexp_test -c \
  "ALTER ROLE myexp_app PASSWORD 'app_test_pw'; ALTER ROLE myexp_worker PASSWORD 'worker_test_pw';"

export TEST_DATABASE_URL=postgresql+psycopg://myexp_app:app_test_pw@127.0.0.1:55432/myexp_test
export TEST_ADMIN_DATABASE_URL=postgresql+psycopg://postgres:postgres@127.0.0.1:55432/myexp_test
export TEST_WORKER_DATABASE_URL=postgresql+psycopg://myexp_worker:worker_test_pw@127.0.0.1:55432/myexp_test
export TEST_QDRANT=1 QDRANT_URL=http://127.0.0.1:6333   # Qdrant tests (optional)
python -m pytest -q
```

Run with `ENVIRONMENT=production` to reproduce CI (no `.env`, production defaults).

**Evaluations with the real AI** (assistant questions and degraded photos): see [`evals/README.md`](evals/README.md).

## Migrations

Plain SQL with golang-migrate in `migrations/`. `000001_init.up.sql` creates the whole
schema: tables, RLS policies, the worker's `SECURITY DEFINER` functions and the runtime roles.
