"""Attested live-model dependency probe for ResearchWarband shadow profiles."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
from typing import Any

from .integration.loopback_http import LoopbackJSONClient


_GEMMA_TOKENIZER_CANARY_MESSAGES = [
    {"role": "system", "content": "ResearchWarband tokenizer canary v1."},
    {
        "role": "user",
        "content": "Unicode: Москва 서울 café 😀. Return exactly OK.",
    },
]


class RuntimeDependencyError(RuntimeError):
    pass


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate runtime-contract key: {key}")
        value[key] = item
    return value


def _regular_contract(path: Path) -> Path:
    if not path.is_absolute():
        raise RuntimeDependencyError("model runtime contract path must be absolute")
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current /= component
        try:
            metadata = os.lstat(current)
        except FileNotFoundError as exc:
            raise RuntimeDependencyError("model runtime contract is missing") from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise RuntimeDependencyError(
                f"model runtime contract path contains a symlink: {current}"
            )
    if not stat.S_ISREG(os.lstat(path).st_mode):
        raise RuntimeDependencyError("model runtime contract must be a regular file")
    return path.resolve(strict=True)


def load_runtime_contract(path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    selected = str(path or os.environ.get("RESEARCH_WARBAND_MODEL_RUNTIME_CONTRACT", "")).strip()
    if not selected:
        raise RuntimeDependencyError("RESEARCH_WARBAND_MODEL_RUNTIME_CONTRACT is required")
    target = _regular_contract(Path(selected).expanduser())
    try:
        raw = target.read_bytes()
        if len(raw) > 65_536:
            raise ValueError("runtime contract exceeds 64 KiB")
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=lambda token: (_ for _ in ()).throw(ValueError(token)),
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise RuntimeDependencyError(f"model runtime contract is invalid: {exc}") from exc
    required = {"version", "dispatcher", "gemma", "qwen", "operator_profile"}
    if not isinstance(value, dict) or set(value) != required or value.get("version") != 1:
        raise RuntimeDependencyError("model runtime contract has missing or unknown fields")
    dispatcher = value.get("dispatcher")
    if not isinstance(dispatcher, dict) or set(dispatcher) != {
        "base_url",
        "service_version",
        "routes",
    }:
        raise RuntimeDependencyError("runtime dispatcher contract is malformed")
    routes = dispatcher.get("routes")
    if not isinstance(routes, dict) or set(routes) != {"gemma", "qwen"}:
        raise RuntimeDependencyError("runtime dispatcher routes are malformed")
    for route_name in ("gemma", "qwen"):
        route = routes.get(route_name)
        if not isinstance(route, dict) or set(route) != {
            "model",
            "upstream",
            "advertised_capacity",
            "upstream_timeout_sec",
            "queue_timeout_sec",
        }:
            raise RuntimeDependencyError(f"runtime {route_name} route is malformed")
        if type(route.get("advertised_capacity")) is not int or route["advertised_capacity"] < 1:
            raise RuntimeDependencyError(f"runtime {route_name} capacity is invalid")
        if (
            type(route.get("upstream_timeout_sec")) is not int
            or route["upstream_timeout_sec"] < 1
            or type(route.get("queue_timeout_sec")) is not int
            or route["queue_timeout_sec"] < 0
        ):
            raise RuntimeDependencyError(f"runtime {route_name} timeout contract is invalid")
    gemma = value.get("gemma")
    if not isinstance(gemma, dict) or set(gemma) != {
        "base_url",
        "model_id",
        "canonical_model_id",
        "root",
        "owned_by",
        "max_model_len",
        "tokenizer_canary_version",
        "tokenizer_canary_count",
        "tokenizer_canary_max_model_len",
        "tokenizer_canary_token_ids_sha256",
    }:
        raise RuntimeDependencyError("runtime Gemma contract is malformed")
    qwen = value.get("qwen")
    if not isinstance(qwen, dict) or set(qwen) != {
        "base_url",
        "model_id",
        "model_path",
        "owned_by",
        "n_ctx",
        "chat_template_sha256",
        "build_info",
        "chat_format",
    }:
        raise RuntimeDependencyError("runtime Qwen contract is malformed")
    operator = value.get("operator_profile")
    if not isinstance(operator, dict) or set(operator) != {
        "gemma_max_num_seqs",
        "research_max_active",
        "gemma_max_tokens",
        "gemma_max_context_chars",
        "qwen_max_tokens",
        "qwen_max_context_chars",
        "gemma_timeout_sec",
        "qwen_timeout_sec",
        "reader_chunk_chars",
        "tensor_parallel_size",
        "modality",
    }:
        raise RuntimeDependencyError("runtime operator profile is malformed")
    integer_fields = (
        (gemma, "max_model_len"),
        (gemma, "tokenizer_canary_version"),
        (gemma, "tokenizer_canary_count"),
        (gemma, "tokenizer_canary_max_model_len"),
        (qwen, "n_ctx"),
        (operator, "gemma_max_num_seqs"),
        (operator, "research_max_active"),
        (operator, "gemma_max_tokens"),
        (operator, "gemma_max_context_chars"),
        (operator, "qwen_max_tokens"),
        (operator, "qwen_max_context_chars"),
        (operator, "gemma_timeout_sec"),
        (operator, "qwen_timeout_sec"),
        (operator, "reader_chunk_chars"),
        (operator, "tensor_parallel_size"),
    )
    if any(type(obj.get(field)) is not int or obj[field] < 1 for obj, field in integer_fields):
        raise RuntimeDependencyError("runtime contract contains an invalid integer")
    if operator["qwen_timeout_sec"] >= routes["qwen"]["upstream_timeout_sec"]:
        raise RuntimeDependencyError(
            "Qwen runner timeout must be strictly below dispatcher upstream timeout"
        )
    if operator.get("modality") != "text_only":
        raise RuntimeDependencyError("current model runtime must remain explicitly text-only")
    for field in ("base_url", "model_id", "canonical_model_id", "root", "owned_by"):
        if field in gemma and (type(gemma[field]) is not str or not gemma[field]):
            raise RuntimeDependencyError(f"runtime Gemma {field} is invalid")
    gemma_token_digest = gemma.get("tokenizer_canary_token_ids_sha256")
    try:
        gemma_digest_bytes = bytes.fromhex(gemma_token_digest)
    except (TypeError, ValueError) as exc:
        raise RuntimeDependencyError("runtime Gemma tokenizer-canary digest is invalid") from exc
    if len(gemma_token_digest) != 64 or len(gemma_digest_bytes) != 32:
        raise RuntimeDependencyError("runtime Gemma tokenizer-canary digest is invalid")
    if gemma["tokenizer_canary_version"] != 1:
        raise RuntimeDependencyError("runtime Gemma tokenizer-canary version is unsupported")
    if gemma["tokenizer_canary_max_model_len"] != gemma["max_model_len"]:
        raise RuntimeDependencyError("runtime Gemma tokenizer-canary context is inconsistent")
    for field in (
        "base_url",
        "model_id",
        "model_path",
        "owned_by",
        "chat_template_sha256",
        "build_info",
        "chat_format",
    ):
        if type(qwen.get(field)) is not str or not qwen[field]:
            raise RuntimeDependencyError(f"runtime Qwen {field} is invalid")
    template_digest = qwen["chat_template_sha256"]
    try:
        digest_bytes = bytes.fromhex(template_digest)
    except ValueError as exc:
        raise RuntimeDependencyError("runtime Qwen chat-template digest is invalid") from exc
    if len(template_digest) != 64 or len(digest_bytes) != 32:
        raise RuntimeDependencyError("runtime Qwen chat-template digest is invalid")
    return value


def _matching_model(payload: dict[str, Any], model_id: str, label: str) -> dict[str, Any]:
    data = payload.get("data")
    if type(data) is not list:
        raise RuntimeDependencyError(f"{label} /v1/models omitted its data array")
    matches = [item for item in data if isinstance(item, dict) and item.get("id") == model_id]
    if len(matches) != 1:
        raise RuntimeDependencyError(f"{label} model identity is missing or ambiguous")
    return matches[0]


def validate_runtime_dependencies(
    contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    expected = load_runtime_contract() if contract is None else contract
    dispatcher_contract = expected["dispatcher"]
    dispatcher_client = LoopbackJSONClient(
        dispatcher_contract["base_url"], expected_port=8079, max_response_bytes=2_000_000
    )
    dispatcher = dispatcher_client.request_json(
        "GET", "/dispatcher/health", timeout_sec=5
    )
    if (
        dispatcher.get("ok") is not True
        or dispatcher.get("service") != "llm-priority-dispatcher"
        or dispatcher.get("version") != dispatcher_contract["service_version"]
    ):
        raise RuntimeDependencyError("dispatcher health identity changed")
    observed_routes = dispatcher.get("routes")
    if not isinstance(observed_routes, dict):
        raise RuntimeDependencyError("dispatcher health omitted route facts")
    stable_routes: dict[str, Any] = {}
    for route_name in ("gemma", "qwen"):
        wanted = dispatcher_contract["routes"][route_name]
        observed = observed_routes.get(route_name)
        if not isinstance(observed, dict):
            raise RuntimeDependencyError(f"dispatcher omitted {route_name} route")
        for field, minimum in (
            ("upstream_timeout_sec", 1.0),
            ("queue_timeout_sec", 0.0),
        ):
            observed_value = observed.get(field)
            if type(observed_value) not in (int, float) or observed_value < minimum:
                raise RuntimeDependencyError(
                    f"dispatcher {route_name} timeout facts are malformed"
                )
        stable = {
            "model": observed.get("model"),
            "upstream": observed.get("upstream"),
            "advertised_capacity": observed.get("capacity"),
            "upstream_timeout_sec": observed.get("upstream_timeout_sec"),
            "queue_timeout_sec": observed.get("queue_timeout_sec"),
        }
        if stable != wanted:
            raise RuntimeDependencyError(f"dispatcher {route_name} route changed")
        stable_routes[route_name] = stable

    gemma_contract = expected["gemma"]
    gemma_client = LoopbackJSONClient(
        gemma_contract["base_url"], expected_port=8080, max_response_bytes=2_000_000
    )
    gemma_payload = gemma_client.request_json("GET", "/v1/models", timeout_sec=5)
    gemma_model = _matching_model(gemma_payload, gemma_contract["model_id"], "Gemma")
    gemma_stable = {
        "model_id": gemma_model.get("id"),
        "root": gemma_model.get("root"),
        "owned_by": gemma_model.get("owned_by"),
        "max_model_len": gemma_model.get("max_model_len"),
    }
    if gemma_stable != {
        key: gemma_contract[key]
        for key in ("model_id", "root", "owned_by", "max_model_len")
    }:
        raise RuntimeDependencyError("Gemma upstream root/owner/context changed")
    _matching_model(
        gemma_payload, gemma_contract["canonical_model_id"], "canonical Gemma"
    )
    canary_payload = gemma_client.request_json(
        "POST",
        "/tokenize",
        payload={
            "model": gemma_contract["model_id"],
            "messages": _GEMMA_TOKENIZER_CANARY_MESSAGES,
            "add_special_tokens": True,
            "add_generation_prompt": True,
            "chat_template_kwargs": {"enable_thinking": False},
        },
        timeout_sec=30,
    )
    canary_keys = set(canary_payload)
    if canary_keys == {"count", "max_model_len", "tokens", "token_strs"}:
        if canary_payload.get("token_strs") is not None:
            raise RuntimeDependencyError("Gemma tokenizer canary token_strs must be null")
    elif canary_keys != {"count", "max_model_len", "tokens"}:
        raise RuntimeDependencyError("Gemma tokenizer canary response fields changed")
    canary_tokens = canary_payload.get("tokens")
    if (
        type(canary_payload.get("count")) is not int
        or type(canary_payload.get("max_model_len")) is not int
        or type(canary_tokens) is not list
        or any(type(item) is not int for item in canary_tokens)
        or len(canary_tokens) != canary_payload["count"]
    ):
        raise RuntimeDependencyError("Gemma tokenizer canary response is malformed")
    canary_stable = {
        "version": 1,
        "count": canary_payload["count"],
        "max_model_len": canary_payload["max_model_len"],
        "token_ids_sha256": hashlib.sha256(
            json.dumps(canary_tokens, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
    }
    expected_canary = {
        "version": gemma_contract["tokenizer_canary_version"],
        "count": gemma_contract["tokenizer_canary_count"],
        "max_model_len": gemma_contract["tokenizer_canary_max_model_len"],
        "token_ids_sha256": gemma_contract["tokenizer_canary_token_ids_sha256"],
    }
    if canary_stable != expected_canary:
        raise RuntimeDependencyError("Gemma tokenizer/template canary changed")
    gemma_stable["tokenizer_canary"] = canary_stable

    qwen_contract = expected["qwen"]
    qwen_client = LoopbackJSONClient(
        qwen_contract["base_url"], expected_port=8081, max_response_bytes=2_000_000
    )
    qwen_payload = qwen_client.request_json("GET", "/v1/models", timeout_sec=5)
    qwen_model = _matching_model(qwen_payload, qwen_contract["model_id"], "Qwen")
    meta = qwen_model.get("meta")
    qwen_model_stable = {
        "model_id": qwen_model.get("id"),
        "owned_by": qwen_model.get("owned_by"),
        "n_ctx": meta.get("n_ctx") if isinstance(meta, dict) else None,
    }
    if qwen_model_stable != {
        key: qwen_contract[key] for key in ("model_id", "owned_by", "n_ctx")
    }:
        raise RuntimeDependencyError("Qwen upstream owner/context changed")
    qwen_props = qwen_client.request_json("GET", "/props", timeout_sec=5)
    generation = qwen_props.get("default_generation_settings")
    params = generation.get("params") if isinstance(generation, dict) else None
    template = qwen_props.get("chat_template")
    template_sha256 = (
        hashlib.sha256(template.encode("utf-8")).hexdigest()
        if isinstance(template, str)
        else None
    )
    qwen_props_stable = {
        "model_id": qwen_props.get("model_alias"),
        "model_path": qwen_props.get("model_path"),
        "n_ctx": generation.get("n_ctx") if isinstance(generation, dict) else None,
        "chat_template_sha256": template_sha256,
        "build_info": qwen_props.get("build_info"),
        "chat_format": params.get("chat_format") if isinstance(params, dict) else None,
    }
    if qwen_props_stable != {
        key: qwen_contract[key]
        for key in (
            "model_id",
            "model_path",
            "n_ctx",
            "chat_template_sha256",
            "build_info",
            "chat_format",
        )
    }:
        raise RuntimeDependencyError(
            "Qwen upstream path/context/template/build facts changed"
        )
    qwen_stable = {**qwen_model_stable, **qwen_props_stable}

    stable = {
        "contract_version": expected["version"],
        "dispatcher": {
            "service_version": dispatcher["version"],
            "routes": stable_routes,
        },
        "gemma": gemma_stable,
        "qwen": qwen_stable,
    }
    canonical = json.dumps(
        stable,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return {
        **stable,
        "attestation_sha256": hashlib.sha256(canonical).hexdigest(),
    }


__all__ = [
    "RuntimeDependencyError",
    "load_runtime_contract",
    "validate_runtime_dependencies",
]
