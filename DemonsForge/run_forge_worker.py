#!/usr/bin/env python3
import time

from forge_service import config
from forge_service.queue import ForgeQueue
from forge_service.storage import ForgeStore


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
