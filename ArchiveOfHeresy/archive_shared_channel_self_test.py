#!/usr/bin/env python3
from __future__ import annotations

from archive_config import LEGACY_SHARED_MEMORY_NAMESPACES, SHARED_CHAT_SESSION_ID, SHARED_MEMORY_NAMESPACE
from archive_util import shared_chat_session_id, shared_memory_namespace


def main() -> int:
    expected_session = shared_chat_session_id(SHARED_CHAT_SESSION_ID)
    if expected_session != "shushunya-main":
        raise AssertionError(f"unexpected shared chat session: {expected_session}")
    expected_namespace = shared_memory_namespace(SHARED_MEMORY_NAMESPACE)
    if expected_namespace != "shushunya":
        raise AssertionError(f"unexpected shared memory namespace: {expected_namespace}")
    for session in ("default", "mobile", "telegram", "agent", "warmaster", "random-client"):
        if shared_chat_session_id(session) != expected_session:
            raise AssertionError(f"session {session!r} did not map to shared chat session")
    for namespace in LEGACY_SHARED_MEMORY_NAMESPACES | {"default", "mobile", "telegram", "agent", "warmaster", "shushunya"}:
        if shared_memory_namespace(namespace) != expected_namespace:
            raise AssertionError(f"namespace {namespace!r} did not map to shared memory namespace")
    if shared_memory_namespace("demonsforge") != "demonsforge":
        raise AssertionError("specialized non-chat namespace should remain separate")
    print("[ok] Archive shared channel")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
