from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse

from .agenda import Agenda
from .authority import Authority
from .commitments import Commitments
from .config import Settings
from .decide import DecisionEngine
from .identity import Identity
from .ledger import IdempotencyConflict, InvariantViolation, Ledger, LedgerError
from .organs import Organs
from .preferences import Preferences
from .relationship import Relationship
from .schema import AgendaRequest, PreferenceEvidence, TurnEnvelope
from .situation import SituationAssembler
from .steward import Steward


LOG = logging.getLogger("shushunya.core")
logging.basicConfig(
    level=os.environ.get("SHUSHUNYA_CORE_LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)


class SingletonLease:
    def __init__(self, path: Path):
        self.path = path
        self._handle = None

    def acquire(self) -> None:
        import fcntl

        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self._handle.close()
            self._handle = None
            raise RuntimeError(f"another ShushunyaCore owns {self.path}") from exc
        self._handle.seek(0)
        self._handle.truncate()
        self._handle.write(str(os.getpid()))
        self._handle.flush()

    def release(self) -> None:
        if self._handle is None:
            return
        import fcntl

        fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        self._handle.close()
        self._handle = None


class CoreRuntime:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.lease = SingletonLease(settings.lock_path)
        self.ledger = Ledger(settings.db_path)
        self.identity = Identity(self.ledger)
        self.relationship = Relationship(self.ledger)
        self.preferences = Preferences(self.ledger)
        self.authority = Authority(self.preferences)
        self.organs = Organs(settings)
        self.commitments = Commitments(self.ledger, self.organs)
        self.agenda = Agenda(self.ledger)
        self.situation = SituationAssembler(
            settings,
            self.ledger,
            self.identity,
            self.relationship,
            self.preferences,
            self.organs,
        )
        self.engine = DecisionEngine(settings, self.ledger, self.situation, self.authority)
        self.steward = Steward(settings, self.ledger, self.organs, self.commitments)
        self.startup_error = ""
        self.recovery: dict[str, Any] = {}

    def start(self) -> None:
        self.lease.acquire()
        try:
            self.ledger.initialize()
            if not self.ledger.ready:
                raise InvariantViolation(self.ledger.integrity_error or "database quick_check failed")
            self.recovery = self.ledger.recover_after_restart()
            self.identity.seed()
            self.relationship.seed()
        except Exception as exc:
            self.startup_error = f"{type(exc).__name__}: {exc}"
            self.ledger.ready = False
            self.ledger.integrity_error = self.startup_error
            self.lease.release()
            LOG.exception("Core startup failed")
            raise

    async def verify_startup_dependencies(self) -> None:
        health = await self.organs.refresh_health(force=True)
        llm = health.get("llm_dispatcher") if isinstance(health.get("llm_dispatcher"), dict) else {}
        models = (llm.get("detail") or {}).get("data") if isinstance(llm.get("detail"), dict) else []
        model_ids = {str(item.get("id") or "") for item in models if isinstance(item, dict)}
        if llm.get("ready") is not True or self.settings.llm_model not in model_ids:
            raise RuntimeError(
                f"LLM dispatcher is not ready with required model {self.settings.llm_model}; available={sorted(model_ids)}"
            )

    def ready(self) -> bool:
        health = self.organs.health_snapshot()
        llm = health.get("llm_dispatcher") if isinstance(health.get("llm_dispatcher"), dict) else {}
        models = (llm.get("detail") or {}).get("data") if isinstance(llm.get("detail"), dict) else []
        model_ids = {str(item.get("id") or "") for item in models if isinstance(item, dict)}
        return (
            self.ledger.ready
            and not self.startup_error
            and llm.get("ready") is True
            and self.settings.llm_model in model_ids
        )

    async def stop(self) -> None:
        await self.steward.stop()
        self.lease.release()

    def status(self) -> dict[str, Any]:
        try:
            ledger = self.ledger.status()
        except Exception as exc:
            ledger = {"ready": False, "error": str(exc)}
        return {
            "service": "shushunya-core",
            "version": 1,
            "ready": self.ready(),
            "startup_error": self.startup_error,
            "ledger": ledger,
            "recovery": self.recovery,
            "steward": self.steward.last_cycle,
            "organs": self.organs.health_snapshot(),
        }


RUNTIME: CoreRuntime | None = None


def runtime() -> CoreRuntime:
    if RUNTIME is None:
        raise HTTPException(status_code=503, detail="Core runtime is not initialized")
    return RUNTIME


def require_ready() -> CoreRuntime:
    value = runtime()
    if not value.ready():
        raise HTTPException(status_code=503, detail={"error": "core_not_ready", "status": value.status()})
    return value


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global RUNTIME
    settings = Settings.from_env()
    RUNTIME = CoreRuntime(settings)
    RUNTIME.start()
    try:
        await RUNTIME.verify_startup_dependencies()
        RUNTIME.steward.start()
        yield
    finally:
        await RUNTIME.stop()
        RUNTIME = None


app = FastAPI(
    title="ShushunyaCore",
    version="0.1.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
)


@app.exception_handler(IdempotencyConflict)
async def idempotency_conflict(_request, exc: IdempotencyConflict):
    return JSONResponse(status_code=409, content={"ok": False, "error": "idempotency_conflict", "explanation": str(exc)})


@app.exception_handler(LedgerError)
async def ledger_failure(_request, exc: Exception):
    return JSONResponse(status_code=500, content={"ok": False, "error": type(exc).__name__, "explanation": str(exc)})


@app.exception_handler(sqlite3.Error)
async def sqlite_failure(_request, exc: sqlite3.Error):
    LOG.exception("SQLite request failure", exc_info=exc)
    return JSONResponse(
        status_code=503,
        content={"ok": False, "error": type(exc).__name__, "explanation": str(exc)[:2_000]},
    )


@app.exception_handler(Exception)
async def unhandled_failure(_request, exc: Exception):
    LOG.exception("Unhandled Core request failure", exc_info=exc)
    return JSONResponse(
        status_code=500,
        content={"ok": False, "error": type(exc).__name__, "explanation": str(exc)[:2_000]},
    )


@app.get("/health")
async def health():
    return runtime().status()


@app.get("/health/ready")
async def health_ready():
    value = runtime()
    if not value.ready():
        return JSONResponse(status_code=503, content=value.status())
    return value.status()


@app.post("/v1/turns/resolve")
async def resolve_turn(envelope: TurnEnvelope):
    value = require_ready()
    return await value.engine.resolve(envelope)


@app.post("/v1/effects/{effect_id}/dispatch")
async def dispatch_effect(effect_id: str):
    value = require_ready()
    effect = value.ledger.get_effect(effect_id)
    if not effect:
        raise HTTPException(status_code=404, detail="effect not found")
    if effect["destination"] not in {
        "abaddon",
        "archive_adapter",
        "archive_notification_adapter",
        "archive_artifact_adapter",
    }:
        raise HTTPException(status_code=409, detail="effect destination has no registered dispatcher")
    dispatched = await value.steward.dispatch_effect(effect_id)
    return {"ok": bool(dispatched and dispatched.get("state") == "delivered"), "effect": dispatched}


@app.get("/v1/commitments")
async def commitments(include_terminal: bool = Query(True), limit: int = Query(100, ge=1, le=500)):
    return {"ok": True, "commitments": require_ready().ledger.list_commitments(include_terminal, limit)}


@app.get("/v1/events")
async def events(after: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500)):
    return {"ok": True, "events": require_ready().ledger.list_events(after, limit)}


@app.get("/v1/self")
async def self_state():
    value = require_ready()
    return {
        "ok": True,
        "identity": value.identity.snapshot(),
        "relationship": value.relationship.snapshot(),
        "identity_proposals": value.identity.list_proposals(),
        "preference_candidates": value.preferences.candidates(),
    }


@app.post("/v1/preferences/evidence")
async def preference_evidence(item: PreferenceEvidence):
    return {"ok": True, **require_ready().preferences.record(item)}


@app.post("/v1/identity/proposals")
async def propose_identity(body: dict[str, Any] = Body(...)):
    key = str(body.get("key") or "").strip()
    rationale = str(body.get("rationale") or "").strip()
    if not key or "value" not in body or not rationale:
        raise HTTPException(status_code=400, detail="key, value and rationale are required")
    evidence = body.get("evidence") if isinstance(body.get("evidence"), list) else []
    return {"ok": True, "proposal": require_ready().identity.propose(key, body["value"], rationale, evidence)}


@app.post("/v1/identity/proposals/{proposal_id}/decision")
async def decide_identity(proposal_id: str, body: dict[str, Any] = Body(...)):
    if not isinstance(body.get("approved"), bool):
        raise HTTPException(status_code=400, detail="approved boolean is required")
    try:
        result = require_ready().identity.decide_proposal(proposal_id, body["approved"])
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True, "proposal": result}


@app.post("/v1/relationship/{key}")
async def correct_relationship(key: str, body: dict[str, Any] = Body(...)):
    if "value" not in body:
        raise HTTPException(status_code=400, detail="value is required")
    return {"ok": True, "projection": require_ready().relationship.correct(key, body["value"])}


@app.post("/v1/agenda")
async def add_agenda(item: AgendaRequest):
    return {"ok": True, "item": require_ready().agenda.add(item)}


@app.get("/v1/agenda")
async def list_agenda(limit: int = Query(100, ge=1, le=500)):
    value = require_ready()
    return {"ok": True, "items": value.agenda.list(limit), "next_useful": value.agenda.next_useful()}


@app.post("/v1/steward/cycle")
async def steward_cycle():
    return {"ok": True, "cycle": await require_ready().steward.cycle()}


def main() -> None:
    settings = Settings.from_env()
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        workers=1,
        log_level=os.environ.get("SHUSHUNYA_CORE_UVICORN_LOG_LEVEL", "info"),
    )


if __name__ == "__main__":
    main()
