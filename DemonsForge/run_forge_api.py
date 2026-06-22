#!/usr/bin/env python3
from forge_service.config import DEFAULT_HOST, DEFAULT_PORT


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "forge_service.server:app",
        host=DEFAULT_HOST,
        port=DEFAULT_PORT,
        reload=False,
        workers=1,
    )
