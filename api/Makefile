include .env

# Migrations run with a privileged role (schema owner);
# the API uses DB_USER (myexp_app, no SUPERUSER/BYPASSRLS).
MIGRATE_DB_USER ?= $(DB_USER)
MIGRATE_DB_PASSWORD ?= $(DB_PASSWORD)

create_database:
	docker exec -it postgres bash -c "psql -U postgres -c \"CREATE DATABASE ${DB_NAME};\""

drop_database:
	docker exec -it postgres bash -c "psql -U postgres -c \"DROP DATABASE ${DB_NAME};\""

# API (development, with reload)
run_api:
	cd app && fastapi dev main.py

# Unit tests (no database). Integration: see README.
test:
	python -m pytest -q

# API contract for the frontend (types). Usage: make openapi [OPENAPI_OUT=...]
OPENAPI_OUT ?= ../../web/openapi.json
PYTHON ?= python
openapi:
	cd app && "$(PYTHON)" -c "import json, sys, main; open(sys.argv[1], 'w', encoding='utf-8').write(json.dumps(main.app.openapi(), ensure_ascii=False, indent=2))" $(OPENAPI_OUT)

# Ingestion worker (requires WORKER_DB_USER/WORKER_DB_PASSWORD in .env)
run_worker:
	cd app && python -m worker

# Usage: make create_migration name=add_categories
create_migration:
	migrate create -ext sql -dir migrations -seq $(name)

migrate_up:
	migrate -database "postgres://$(MIGRATE_DB_USER):$(MIGRATE_DB_PASSWORD)@${DB_HOST}:${DB_PORT}/${DB_NAME}?sslmode=disable" -path migrations -verbose up

migrate_down:
	migrate -database "postgres://$(MIGRATE_DB_USER):$(MIGRATE_DB_PASSWORD)@${DB_HOST}:${DB_PORT}/${DB_NAME}?sslmode=disable" -path migrations -verbose down
