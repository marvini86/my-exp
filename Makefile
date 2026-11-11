# My Exp — monorepo shortcuts. Run each service in its own terminal:
#   make api      FastAPI at http://127.0.0.1:8000 (docs at /docs)
#   make worker   processes uploaded documents (extraction + indexing)
#   make web      Next.js at http://localhost:3100
#
# Configuration: api/.env (see api/.env.example). For the frontend, the variables
# below have development defaults; a web/.env.local, if present, also applies.

PY := $(CURDIR)/api/.venv/Scripts/python.exe
WEB_PORT ?= 3100

# Exported by make itself: works in cmd, PowerShell and bash
# (the "VAR=value command" syntax only exists in bash).
export API_URL ?= http://127.0.0.1:8000
export DEV_LOGIN_ENABLED ?= true

.PHONY: api worker web types test eval-assistant eval-photos

api:
	cd api/app && "$(PY)" -m uvicorn main:app --reload --host 127.0.0.1 --port 8000

worker:
	cd api/app && "$(PY)" -m worker

web:
	cd web && npx next dev --port $(WEB_PORT)

# API contract → frontend types (run after changing API routes/schemas).
types:
	$(MAKE) -C api openapi OPENAPI_OUT=../../web/openapi.json PYTHON="$(PY)"
	cd web && npm run generate-api

test:
	cd api && "$(PY)" -m pytest -q
	cd web && npm run typecheck && npm run lint && npm test

# Evaluations with the real AI (they use quota). See api/evals/README.md.
#   make eval-assistant   (requires EVAL_DATABASE_URL: disposable local Postgres)
#   make eval-photos      (requires downloaded photos: evals/photos/run.py fetch --email ...)
eval-assistant:
	cd api && "$(PY)" evals/assistant/run.py $(ARGS)

eval-photos:
	cd api && "$(PY)" evals/photos/run.py run $(ARGS)
