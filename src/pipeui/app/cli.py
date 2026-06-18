import sys
import json
from pathlib import Path

CONFIG_PATH = Path("pipeui.config.json")
DEFAULT_DB = Path("pipeui.db")


def cmd_init():
    created = []
    if not CONFIG_PATH.exists():
        from pipeui.backend.data.base.settings import AppSettings  # lazy: only when scaffolding a missing config
        settings = AppSettings()
        CONFIG_PATH.write_text(settings.model_dump_json(indent=2))
        created.append(str(CONFIG_PATH))
    if not DEFAULT_DB.exists():
        import duckdb  # lazy: only when creating a missing DB file
        conn = duckdb.connect(str(DEFAULT_DB))
        conn.close()
        created.append(str(DEFAULT_DB))
    if created:
        print(f"Initialised: {', '.join(created)}")
    else:
        print("Already initialised — nothing to do.")


def cmd_start():
    import uvicorn  # lazy: heavy server dep — deferred until 'start' runs
    config = {}
    if CONFIG_PATH.exists():
        config = json.loads(CONFIG_PATH.read_text())
    host = config.get("host", "127.0.0.1")
    port = config.get("port", 8000)
    uvicorn.run("pipeui.app.main:app", host=host, port=port, reload=True)


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("init", "start"):
        print("Usage: pipeui <init|start>")
        sys.exit(1)
    {"init": cmd_init, "start": cmd_start}[sys.argv[1]]()
