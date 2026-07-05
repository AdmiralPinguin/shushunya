#!/usr/bin/env python3
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from EyeOfTerror.Pictorium.Moriana.forge_runtime import config
from EyeOfTerror.Pictorium.Moriana.forge_runtime.queue import ForgeQueue
from EyeOfTerror.Pictorium.Moriana.forge_runtime.storage import ForgeStore


def main() -> None:
    config.force_cpu_runtime()
    config.ensure_dirs()
    worker = ForgeQueue(ForgeStore(), start_worker=False)
    completed = 0
    while True:
        did_work = worker.run_pending_once()
        if did_work:
            completed += 1
            if config.WORKER_MAX_JOBS > 0 and completed >= config.WORKER_MAX_JOBS:
                return
        else:
            time.sleep(1.0)


if __name__ == "__main__":
    main()
