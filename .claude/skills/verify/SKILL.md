---
name: verify
description: Build/launch/drive recipe for verifying pipeui changes end-to-end (server + real browser). Use when a change needs runtime observation rather than tests.
---

# Verifying pipeui at its runtime surfaces

## Launch the server

`import pipeui` fails in this checkout (editable install broken on the iCloud
space-path) — always run with `PYTHONPATH=src` and the repo venv:

```bash
# from any working dir; DB path comes from pipeui.config.json in the CWD
PYTHONPATH="<repo>/src" "<repo>/.venv/bin/python" -m uvicorn pipeui.app.main:app \
  --host 127.0.0.1 --port 8765
```

`DB_PATH` is frozen at import from `pipeui.config.json` in the **current
working directory** — to use a scratch DB, run the server from a scratch dir
containing `{"db_path": "live.db", ...}`.

## Seed a scratch DB (no UI clicking needed)

```python
import duckdb
from pipeui.backend.data.base.db import create_schema
from pipeui.backend.domain.sources.create import create_source
from pipeui.backend.domain.sources.ingestion import ingest_source
from pipeui.backend.data.runner.staging import write_staging_table  # staging table = transform output

conn = duckdb.connect("live.db"); create_schema(conn)
sid, _ = create_source(conn=conn, file_path="x.csv", source_name="sales live",
                       primary_key="id", ingestion_method="upsert")
ingest_source(conn=conn, source_id=sid, file_path="x.csv")
write_staging_table(conn, sid, df, int(time.time()))  # simulates a transform run
conn.close()  # close before starting the server — DuckDB is single-writer
```

Seed data with NaN/None cells — clean synthetic data has hidden real bugs
before (#258/#262).

## Drive the surfaces

- **API**: `curl -sD -` the routes; error bodies are FastAPI `{"detail": ...}`.
- **Browser mount** (the seam vitest cannot see — CDN + Babel-standalone):
  `SMOKE_CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" npm run smoke`
  (playwright's bundled browsers are typically not installed here; local
  Chrome via `SMOKE_CHROME` / `executablePath` works).
- **Browser behavior against the live server**: playwright resolves only from
  the repo root — run scripts as
  `node --input-type=module --eval "$(cat script.mjs)"` from the repo root,
  and pass `{ executablePath: process.env.SMOKE_CHROME }` to `chromium.launch`.
  `page.waitForFunction(() => document.getElementById("root").children.length > 0)`
  is the mount signal; `page.waitForEvent("download")` catches
  Content-Disposition downloads.

## Gotchas

- Full pytest: `PYTHONPATH=src .venv/bin/python -m pytest tests/ -q`.
  `tests/test_worker.py::test_oom_worker_killed_by_setrlimit_returns_failed_entry`
  fails on macOS (setrlimit memory caps don't enforce; CI is Linux) — deselect
  it locally, it is not a regression signal.
- vitest cold start on this iCloud path can take 10+ minutes (environment
  setup); the tests themselves run in milliseconds. Don't assume a hang.
