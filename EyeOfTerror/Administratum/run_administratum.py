from __future__ import annotations

import argparse
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

from .ashur_kai_service import make_handler
from .heartbeat import loop
from .schema import DEFAULT_PORT
from .storage import init_db


def main() -> int:
    parser = argparse.ArgumentParser(description="Run AshurKai API and Administratum heartbeat together.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--db", default="")
    parser.add_argument("--interval-sec", type=float, default=60.0)
    args = parser.parse_args()
    db_path = Path(args.db) if args.db else Path(__file__).resolve().parent / "runtime" / "administratum.sqlite3"
    init_db(db_path)
    server = ThreadingHTTPServer((args.host, args.port), make_handler(db_path))
    thread = threading.Thread(target=server.serve_forever, name="ashur-kai-api", daemon=True)
    thread.start()
    print(f"AshurKai listening on http://{args.host}:{args.port}", flush=True)
    try:
        loop(db_path, interval_sec=args.interval_sec)
    finally:
        server.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
