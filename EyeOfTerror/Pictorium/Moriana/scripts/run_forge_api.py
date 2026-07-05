#!/usr/bin/env python3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from EyeOfTerror.Pictorium.Moriana.forge_runtime.config import DEFAULT_HOST, DEFAULT_PORT


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "EyeOfTerror.Pictorium.Moriana.forge_runtime.server:app",
        host=DEFAULT_HOST,
        port=DEFAULT_PORT,
        reload=False,
        workers=1,
    )
