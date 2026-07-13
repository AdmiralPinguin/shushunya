#!/usr/bin/env python3
"""Trusted local publisher for Archive artifacts; never exposed over HTTP."""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from archive_config import ARTIFACT_TRUSTED_ROOTS, SHARED_CHAT_SESSION_ID
from artifact_store import ArtifactError, trusted_import_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Snapshot one local file into Archive's artifact CAS.")
    parser.add_argument("path", type=Path)
    parser.add_argument("--name", default="", help="download filename; defaults to the source basename")
    parser.add_argument("--media-type", default="", help="MIME type; defaults to extension-based detection")
    parser.add_argument("--source", default="operator", help="trusted producer id")
    parser.add_argument("--session-id", default=SHARED_CHAT_SESSION_ID)
    parser.add_argument("--audience-source", default="*", help="client source allowed to see it, or *")
    parser.add_argument("--task-id", default="")
    parser.add_argument("--mission-id", default="")
    parser.add_argument("--logical-path", default="")
    parser.add_argument("--dedupe-key", default="")
    parser.add_argument(
        "--root",
        action="append",
        type=Path,
        default=[],
        help="allowed source root; repeatable. Services should configure explicit roots.",
    )
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    # Invoking this local CLI is itself operator authority. Automated producers
    # must use --root or ARCHIVE_ARTIFACT_TRUSTED_ROOTS instead of deriving one.
    roots = tuple(args.root) or ARTIFACT_TRUSTED_ROOTS or (args.path.expanduser().absolute().parent,)
    try:
        artifact = trusted_import_path(
            args.path,
            allowed_roots=roots,
            filename=args.name or None,
            media_type=args.media_type or None,
            source=args.source,
            session_id=args.session_id,
            audience_source=args.audience_source,
            task_id=args.task_id or None,
            mission_id=args.mission_id or None,
            logical_path=args.logical_path or None,
            dedupe_key=args.dedupe_key or None,
            metadata={"publisher": "artifact_publish.py"},
        )
    except (ArtifactError, OSError, sqlite3.Error) as exc:
        print(f"artifact publish failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"ok": True, "artifact": artifact}, ensure_ascii=False, sort_keys=True))
    return 0
if __name__ == "__main__":
    raise SystemExit(main())
