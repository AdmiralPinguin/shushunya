"""Read-only fail-closed preflight for the 7201/7202 systemd profiles."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat
import sys
from urllib.parse import urlsplit

from .production_runner import EVALUATOR_PROFILE, PRODUCTION_PROFILE
from .research_tools import ConfiguredDomainSourceClassifier
from .runtime_dependencies import (
    load_runtime_contract,
    validate_runtime_dependencies,
)


class DeploymentProfileError(RuntimeError):
    pass


_SEARCH_PROVIDERS = "searxng,marginalia,duckduckgo,wikipedia,brave"
_SEARCH_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
_SEARCH_ACCEPT_LANGUAGE = "ru,en;q=0.9"


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise DeploymentProfileError(f"{name} is required")
    return value


def _integer(name: str) -> int:
    try:
        return int(_required(name))
    except ValueError as exc:
        raise DeploymentProfileError(f"{name} must be an integer") from exc


def _present(name: str) -> str:
    if name not in os.environ:
        raise DeploymentProfileError(f"{name} must be explicitly bound")
    return os.environ[name]


def _regular_nonsymlink(path: Path, label: str) -> Path:
    candidate = path.expanduser()
    if not candidate.is_absolute():
        raise DeploymentProfileError(f"{label} must be an absolute path")
    current = Path(candidate.anchor)
    for component in candidate.parts[1:]:
        current /= component
        try:
            metadata = os.lstat(current)
        except FileNotFoundError as exc:
            raise DeploymentProfileError(f"{label} is missing: {candidate}") from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise DeploymentProfileError(f"{label} contains a symlink: {current}")
    if not stat.S_ISREG(os.lstat(candidate).st_mode):
        raise DeploymentProfileError(f"{label} must be a regular file")
    return candidate.resolve(strict=True)


def _absolute_store(name: str) -> Path:
    value = Path(_required(name)).expanduser()
    if not value.is_absolute():
        raise DeploymentProfileError(f"{name} must be absolute")
    current = Path(value.anchor)
    for component in value.parts[1:]:
        current /= component
        try:
            metadata = os.lstat(current)
        except FileNotFoundError:
            # Remaining descendants do not exist yet; the service may create
            # them only below this already-checked real parent chain.
            break
        if stat.S_ISLNK(metadata.st_mode):
            raise DeploymentProfileError(f"{name} contains a symlink: {current}")
        if current != value and not stat.S_ISDIR(metadata.st_mode):
            raise DeploymentProfileError(
                f"{name} has a non-directory parent component: {current}"
            )
    return value.resolve(strict=False)


def _paths_overlap(left: Path, right: Path) -> bool:
    return left == right or left in right.parents or right in left.parents


def _dispatcher(name: str) -> None:
    value = _required(name)
    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise DeploymentProfileError(f"{name} is malformed") from exc
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or parsed.port != 8079
        or parsed.path.rstrip("/") != "/v1"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise DeploymentProfileError(f"{name} must be http://127.0.0.1:8079/v1")


def validate_deployment_profile(expected: str) -> dict[str, object]:
    if expected not in {PRODUCTION_PROFILE, EVALUATOR_PROFILE}:
        raise DeploymentProfileError("unsupported expected profile")
    if sys.pycache_prefix != "/dev/null" or sys.dont_write_bytecode is not True:
        raise DeploymentProfileError(
            "deployed ResearchWarband requires PYTHONPYCACHEPREFIX=/dev/null "
            "and PYTHONDONTWRITEBYTECODE=1"
        )
    if _required("RESEARCH_WARBAND_PROFILE") != expected:
        raise DeploymentProfileError("RESEARCH_WARBAND_PROFILE does not match the unit")
    runner = _required("RESEARCH_WARBAND_RUNNER")
    if not runner.endswith(".ResearchWarband.production_runner:run_mission"):
        raise DeploymentProfileError("RESEARCH_WARBAND_RUNNER is not the attested production adapter")
    if not _required("RESEARCH_WARBAND_READINESS_PROBE").endswith(
        ".ResearchWarband.production_runner:runtime_readiness_probe"
    ):
        raise DeploymentProfileError("readiness probe is not the attested runtime guard")
    production = expected == PRODUCTION_PROFILE
    expected_port = 7201 if production else 7202
    if _integer("RESEARCH_WARBAND_PORT") != expected_port:
        raise DeploymentProfileError(f"profile must bind port {expected_port}")
    if _required("RESEARCH_WARBAND_HOST") != "127.0.0.1":
        raise DeploymentProfileError("ResearchWarband must bind literal loopback")
    expected_standalone = "0" if production else "1"
    if os.environ.get("RESEARCH_WARBAND_STANDALONE_TEST_MODE", "") != expected_standalone:
        raise DeploymentProfileError("standalone mode does not match the service profile")
    token = os.environ.get("RESEARCH_WARBAND_BEARER_TOKEN", "")
    if any(ord(char) < 32 or ord(char) == 127 for char in token):
        raise DeploymentProfileError("bearer token contains an HTTP control character")
    if production and (
        len(token) < 32 or token.startswith("REPLACE_") or len(set(token)) < 8
    ):
        raise DeploymentProfileError("7201 requires a bearer token of at least 32 characters")
    if not production and token:
        raise DeploymentProfileError("7202 evaluator profile must remain tokenless")
    max_active = _integer("RESEARCH_WARBAND_MAX_ACTIVE")
    runtime_contract_path = _regular_nonsymlink(
        Path(_required("RESEARCH_WARBAND_MODEL_RUNTIME_CONTRACT")),
        "model runtime contract",
    )
    runtime_contract = load_runtime_contract(runtime_contract_path)
    operator_profile = runtime_contract["operator_profile"]
    if (
        operator_profile.get("tensor_parallel_size") != 1
        or operator_profile.get("modality") != "text_only"
    ):
        raise DeploymentProfileError("current shadow requires the attested TP1 text-only profile")
    if production and not 1 <= max_active <= min(
        4, int(operator_profile["research_max_active"])
    ):
        raise DeploymentProfileError(
            "7201 max_active exceeds the attested upstream/operator capacity"
        )
    if not production and max_active != 1:
        raise DeploymentProfileError("7202 evaluator must use one isolated mission slot")
    attempt_timeout = _integer("RESEARCH_WARBAND_ATTEMPT_TIMEOUT_SECONDS")
    minimum_attempt = 604_800 if production else 86_400
    if attempt_timeout < minimum_attempt:
        raise DeploymentProfileError("attempt timeout is below the profile contract")
    if _integer("RESEARCH_QWEN_TIMEOUT_SEC") != operator_profile["qwen_timeout_sec"]:
        raise DeploymentProfileError("Qwen timeout differs from the attested operator profile")
    if _integer("RESEARCH_GEMMA_TIMEOUT_SEC") != operator_profile["gemma_timeout_sec"]:
        raise DeploymentProfileError("Gemma timeout differs from the attested operator profile")
    if _integer("RESEARCH_GEMMA_MAX_TOKENS") != operator_profile["gemma_max_tokens"]:
        raise DeploymentProfileError("Gemma max_tokens differs from the attested 31B profile")
    if (
        _integer("RESEARCH_GEMMA_MAX_CONTEXT_CHARS")
        != operator_profile["gemma_max_context_chars"]
    ):
        raise DeploymentProfileError("Gemma context chars differ from the attested 31B profile")
    if _integer("RESEARCH_QWEN_MAX_TOKENS") != operator_profile["qwen_max_tokens"]:
        raise DeploymentProfileError("Qwen max_tokens differs from the attested operator profile")
    if (
        _integer("RESEARCH_QWEN_MAX_CONTEXT_CHARS")
        != operator_profile["qwen_max_context_chars"]
    ):
        raise DeploymentProfileError("Qwen context chars differ from the attested operator profile")
    if _integer("RESEARCH_READER_CHUNK_CHARS") != operator_profile["reader_chunk_chars"]:
        raise DeploymentProfileError("reader chunk size differs from the attested model profile")
    if _integer("SHUSHUNYA_SEARCH_MAX_WEB_BYTES") != 200_000:
        raise DeploymentProfileError("search byte limit differs from the rollout profile")
    brave_key = _present("SHUSHUNYA_SEARCH_BRAVE_API_KEY")
    if len(brave_key) > 4096 or any(ord(char) < 32 or ord(char) == 127 for char in brave_key):
        raise DeploymentProfileError("Brave search credential is malformed")
    if _present("SHUSHUNYA_SEARCH_SEARXNG_URL"):
        raise DeploymentProfileError("current rollout does not attest a SearXNG endpoint")
    if _required("SHUSHUNYA_SEARCH_PROVIDERS") != _SEARCH_PROVIDERS:
        raise DeploymentProfileError("search providers differ from the rollout profile")
    if _required("SHUSHUNYA_SEARCH_WEB_USER_AGENT") != _SEARCH_USER_AGENT:
        raise DeploymentProfileError("search user agent differs from the rollout profile")
    if _required("SHUSHUNYA_SEARCH_WEB_ACCEPT_LANGUAGE") != _SEARCH_ACCEPT_LANGUAGE:
        raise DeploymentProfileError("search language header differs from the rollout profile")
    expected_normalizer = (
        "research-warband-pinned-fetch-v2"
        if production
        else "research-eval-utf8-exact-v1"
    )
    if _required("RESEARCH_WARBAND_NORMALIZER_ID") != expected_normalizer:
        raise DeploymentProfileError(
            "normalizer identity does not match the active fetch boundary"
        )
    _dispatcher("RESEARCH_WARBAND_LLM_BASE_URL")
    _dispatcher("RESEARCH_WARBAND_VERIFIER_BASE_URL")
    if _required("RESEARCH_WARBAND_LLM_MODEL") == _required(
        "RESEARCH_WARBAND_VERIFIER_MODEL"
    ):
        raise DeploymentProfileError("author and reviewer model identities must differ")
    if _required("RESEARCH_WARBAND_LLM_MODEL") != runtime_contract["dispatcher"]["routes"]["gemma"]["model"]:
        raise DeploymentProfileError("Gemma alias differs from the attested dispatcher route")
    if _required("RESEARCH_WARBAND_VERIFIER_MODEL") != runtime_contract["dispatcher"]["routes"]["qwen"]["model"]:
        raise DeploymentProfileError("Qwen alias differs from the attested dispatcher route")
    if {
        item.strip()
        for item in _required("RESEARCH_WARBAND_TRUSTED_REVIEWER_IDS").split(",")
        if item.strip()
    } != {_required("RESEARCH_WARBAND_REVIEWER_AUTHORITY_ID")}:
        raise DeploymentProfileError("trusted reviewer identity is inconsistent")
    mission_root = _absolute_store("RESEARCH_WARBAND_MISSION_ROOT")
    snapshot_root = _absolute_store("RESEARCH_WARBAND_SNAPSHOT_ROOT")
    peer_mission = _absolute_store("RESEARCH_WARBAND_PEER_MISSION_ROOT")
    peer_snapshot = _absolute_store("RESEARCH_WARBAND_PEER_SNAPSHOT_ROOT")
    roots = (mission_root, snapshot_root, peer_mission, peer_snapshot)
    for index, left in enumerate(roots):
        for right in roots[index + 1 :]:
            if _paths_overlap(left, right):
                raise DeploymentProfileError(
                    "production/evaluator mission stores and CAS roots overlap"
                )
    classifier = _regular_nonsymlink(
        Path(_required("RESEARCH_SOURCE_CLASSIFIER_JSON")), "source classifier"
    )
    try:
        raw_classifier = classifier.read_bytes()
        classifier_payload = json.loads(
            raw_classifier.decode("utf-8"),
            object_pairs_hook=lambda pairs: _unique_pairs(pairs, "source classifier"),
            parse_constant=lambda token: (_ for _ in ()).throw(ValueError(token)),
        )
        ConfiguredDomainSourceClassifier(classifier_payload)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise DeploymentProfileError(f"source classifier is invalid: {exc}") from exc
    trusted_paths = {
        _regular_nonsymlink(Path(item), "trusted contract file")
        for item in _required("RESEARCH_WARBAND_TRUSTED_CONTRACT_FILES").split(os.pathsep)
        if item
    }
    if classifier not in trusted_paths:
        raise DeploymentProfileError(
            "source classifier must be included in RESEARCH_WARBAND_TRUSTED_CONTRACT_FILES"
        )
    if runtime_contract_path not in trusted_paths:
        raise DeploymentProfileError(
            "model runtime contract must be included in RESEARCH_WARBAND_TRUSTED_CONTRACT_FILES"
        )
    trusted_sources = tuple(
        _regular_nonsymlink(Path(item), "trusted source file")
        for item in _required("RESEARCH_WARBAND_TRUSTED_SOURCE_FILES").split(os.pathsep)
        if item
    )
    if len(trusted_sources) != 1 or trusted_sources[0].parts[-4:] != (
        "EyeOfTerror",
        "Services",
        "Search",
        "web_tools.py",
    ):
        raise DeploymentProfileError(
            "trusted source files must contain exactly EyeOfTerror/Services/Search/web_tools.py"
        )
    runtime_report = validate_runtime_dependencies(runtime_contract)
    return {
        "ok": True,
        "profile": expected,
        "port": expected_port,
        "max_active": max_active,
        "attempt_timeout_seconds": attempt_timeout,
        "qwen_timeout_seconds": _integer("RESEARCH_QWEN_TIMEOUT_SEC"),
        "mission_root": str(mission_root),
        "snapshot_root": str(snapshot_root),
        "source_classifier": str(classifier),
        "model_runtime_contract": str(runtime_contract_path),
        "model_runtime_attestation_sha256": runtime_report["attestation_sha256"],
        "trusted_contract_file_count": len(trusted_paths),
        "trusted_source_file_count": len(trusted_sources),
    }


def _unique_pairs(pairs: list[tuple[str, object]], context: str) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"{context} has duplicate key: {key}")
        result[key] = value
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expect", choices=[PRODUCTION_PROFILE, EVALUATOR_PROFILE], required=True)
    args = parser.parse_args(argv)
    try:
        report = validate_deployment_profile(args.expect)
    except DeploymentProfileError as exc:
        parser.error(str(exc))
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["DeploymentProfileError", "validate_deployment_profile"]
