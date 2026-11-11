# Evaluations with the real AI (evals)

Kept out of `pytest`: they use the free providers' quota and take minutes.
Run them after changing the assistant, the extraction prompt or the model lists.

## 1. Assistant (`evals/assistant`)

37 questions (in Portuguese, like the product) over a **fixed, fictitious** set of
11 documents (`dataset.py`, shaped like real receipts: supermarket with a discount,
pharmacy, water, energy, abbreviated item names such as `CSCOX LAR C/DORSO` and
`LTE INT ITALAC`). Each document goes through the worker's real processor; only
the vision AI reading is replaced. "Today" in the evaluation is 2026-11-09. Expected
answers live in `cases.py` and are checked by number, cited sources and planner intent.

It needs the disposable Postgres (the same one used by the integration tests) and a local Qdrant:

```bash
docker run --rm -d --name myexp-test-pg -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=myexp_test -p 127.0.0.1:55432:5432 postgres:17-alpine
migrate -database "postgres://postgres:postgres@127.0.0.1:55432/myexp_test?sslmode=disable" -path migrations up
docker exec myexp-test-pg psql -U postgres -d myexp_test -c "ALTER ROLE myexp_app WITH PASSWORD 'app_test_pw';"

export EVAL_DATABASE_URL=postgresql+psycopg://myexp_app:app_test_pw@127.0.0.1:55432/myexp_test
python evals/assistant/run.py                     # everything (~10 min, ~90 text calls)
python evals/assistant/run.py --tag produto       # a single topic
python evals/assistant/run.py --only farmacia,luz-vencida
```

- Runs inside a transaction rolled back at the end; the script refuses a non-local database.
- Documents are indexed in the `myexp_eval` Qdrant collection, cleaned at the end.
- Rate limit errors (429) in the logs are expected: the router moves on to the next model.

## 2. Hard photos (`evals/photos`)

Your own receipt photos in 6 versions (original, small/compressed, rotated 90°, tilted 7°,
dark, blurred), read by the vision AI and compared with the verified reading (type, total,
item count and items sum).

```bash
# once: downloads the photos and the current reading through the local API (development login)
python evals/photos/run.py fetch --email you@example.com
# check the readings in the app: they become the reference (evals/data/photos/expected.json)

python evals/photos/run.py run                              # N photos x 6 versions
python evals/photos/run.py run --variants original,blurred --limit 3
```

`evals/data/` (personal photos) and `evals/results/` (detailed JSON answers) are not committed.

## When something changes

- A new kind of question the assistant gets wrong → add a case to `cases.py`.
- A document in `dataset.py` changed → recompute the cases that depend on it.
- A new hard real receipt → upload it in the app, check the reading and run `fetch` again.
