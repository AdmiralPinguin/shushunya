from __future__ import annotations

"""Repository survey role implementation."""

from common.codewright_core import *  # noqa: F403 - role modules use the shared Codewright helper surface.


def python_file_summary(repo_root: Path, path: Path) -> dict[str, Any]:
    rel = str(path.relative_to(repo_root))
    try:
        if path.stat().st_size > MAX_SYMBOL_SCAN_BYTES:
            return {"path": rel, "skipped": "file_too_large_for_symbol_scan"}
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
    except (OSError, SyntaxError, UnicodeDecodeError) as exc:
        return {"path": rel, "skipped": f"python_parse_failed: {exc.__class__.__name__}"}
    functions: list[str] = []
    classes: list[str] = []
    imports: list[str] = []
    calls: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.append(node.name)
        elif isinstance(node, ast.ClassDef):
            classes.append(node.name)
        elif isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            imports.extend(f"{module}.{alias.name}" if module else alias.name for alias in node.names)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        if isinstance(target, ast.Name):
            calls.append(target.id)
        elif isinstance(target, ast.Attribute):
            calls.append(target.attr)
    return {
        "path": rel,
        "module": python_module_name(rel),
        "functions": functions[:40],
        "classes": classes[:40],
        "imports": imports[:40],
        "calls": sorted(set(calls))[:80],
    }


def import_dependency_graph(python_symbols: list[dict[str, Any]]) -> dict[str, Any]:
    modules_by_name = {
        str(item.get("module") or ""): str(item.get("path") or "")
        for item in python_symbols
        if isinstance(item, dict) and item.get("module") and item.get("path")
    }
    edges: list[dict[str, str]] = []
    reverse: dict[str, list[str]] = {}
    for item in python_symbols:
        if not isinstance(item, dict):
            continue
        source_path = str(item.get("path") or "")
        imports = item.get("imports") if isinstance(item.get("imports"), list) else []
        for imported in imports:
            text = str(imported)
            candidate_modules = [text, text.rsplit(".", 1)[0] if "." in text else text]
            target_path = next((modules_by_name[module] for module in candidate_modules if module in modules_by_name), "")
            if not target_path or target_path == source_path:
                continue
            edge = {"from": source_path, "to": target_path, "import": text}
            if edge not in edges:
                edges.append(edge)
                reverse.setdefault(target_path, [])
                if source_path not in reverse[target_path]:
                    reverse[target_path].append(source_path)
    return {
        "edges": edges[:200],
        "reverse_dependents": {key: value[:20] for key, value in sorted(reverse.items())[:80]},
        "edge_count": len(edges),
    }


def call_graph_summary(python_symbols: list[dict[str, Any]]) -> dict[str, Any]:
    function_defs: dict[str, str] = {}
    for item in python_symbols:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "")
        for name in item.get("functions", []) if isinstance(item.get("functions"), list) else []:
            function_defs.setdefault(str(name), path)
    edges: list[dict[str, str]] = []
    for item in python_symbols:
        if not isinstance(item, dict):
            continue
        source_path = str(item.get("path") or "")
        for call in item.get("calls", []) if isinstance(item.get("calls"), list) else []:
            target_path = function_defs.get(str(call), "")
            if target_path and target_path != source_path:
                edge = {"from": source_path, "to": target_path, "call": str(call)}
                if edge not in edges:
                    edges.append(edge)
    return {
        "edges": edges[:200],
        "edge_count": len(edges),
        "known_function_count": len(function_defs),
    }


def targeted_reading_plan(repo_map: dict[str, Any], dependency_graph: dict[str, Any]) -> list[dict[str, Any]]:
    read_order = repo_map.get("recommended_read_order") if isinstance(repo_map.get("recommended_read_order"), list) else []
    reverse = dependency_graph.get("reverse_dependents") if isinstance(dependency_graph.get("reverse_dependents"), dict) else {}
    plan: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in read_order[:20]:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "")
        if not path or path in seen:
            continue
        seen.add(path)
        dependents = [str(value) for value in reverse.get(path, [])] if isinstance(reverse.get(path), list) else []
        plan.append(
            {
                "path": path,
                "phase": item.get("phase", ""),
                "reason": item.get("reason", ""),
                "dependent_count": len(dependents),
                "sample_dependents": dependents[:5],
                "question": "What contract does this file expose, and what tests or dependents would break if it changes?",
            }
        )
    return plan[:20]


def engineering_hypotheses(goal: str, repo_map: dict[str, Any], dependency_graph: dict[str, Any]) -> list[dict[str, Any]]:
    ranked = repo_map.get("ranked_files") if isinstance(repo_map.get("ranked_files"), list) else []
    reverse = dependency_graph.get("reverse_dependents") if isinstance(dependency_graph.get("reverse_dependents"), dict) else {}
    hypotheses: list[dict[str, Any]] = []
    for item in ranked[:8]:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "")
        if not path:
            continue
        reasons = item.get("reasons") if isinstance(item.get("reasons"), list) else []
        dependents = reverse.get(path, []) if isinstance(reverse.get(path), list) else []
        hypotheses.append(
            {
                "hypothesis": f"{path} is likely relevant to the requested code change.",
                "confidence": "high" if int(item.get("score") or 0) >= 8 else "medium",
                "evidence": reasons[:5],
                "risk": "public behavior may affect dependents" if dependents else "local change risk appears limited",
                "next_read": path,
            }
        )
    if not hypotheses:
        hypotheses.append(
            {
                "hypothesis": "No strong source candidate was found from filenames, symbols, or tests.",
                "confidence": "low",
                "evidence": ["repository map has no high-signal ranked files"],
                "risk": "manual task clarification or broader survey may be required",
                "next_read": "",
            }
        )
    return hypotheses


def suggested_verification_commands(test_files: list[str]) -> list[str]:
    commands: list[str] = []
    py_tests = [item for item in test_files if item.endswith(".py")]
    if py_tests:
        commands.append("python -m unittest discover")
        commands.extend(f"python -m unittest {item[:-3].replace('/', '.')}" for item in py_tests[:5])
    return commands[:8]


def engineering_readiness_model(goal: str, repo_map: dict[str, Any], dependency_graph: dict[str, Any], test_files: list[str]) -> dict[str, Any]:
    ranked_files = repo_map.get("ranked_files") if isinstance(repo_map.get("ranked_files"), list) else []
    links = repo_map.get("test_source_links") if isinstance(repo_map.get("test_source_links"), list) else []
    reverse = dependency_graph.get("reverse_dependents") if isinstance(dependency_graph.get("reverse_dependents"), dict) else {}
    linked_tests_by_source: dict[str, list[str]] = {}
    for link in links:
        if not isinstance(link, dict):
            continue
        test_path = str(link.get("test_path") or "")
        for source_path in link.get("source_paths", []) if isinstance(link.get("source_paths"), list) else []:
            linked_tests_by_source.setdefault(str(source_path), [])
            if test_path and test_path not in linked_tests_by_source[str(source_path)]:
                linked_tests_by_source[str(source_path)].append(test_path)
    impact_matrix: list[dict[str, Any]] = []
    for item in ranked_files[:12]:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "")
        if not path:
            continue
        dependents = [str(value) for value in reverse.get(path, [])] if isinstance(reverse.get(path), list) else []
        linked_tests = linked_tests_by_source.get(path, [])
        score = int(item.get("score") or 0)
        if dependents and not linked_tests:
            impact_level = "high"
        elif dependents or score >= 8:
            impact_level = "medium"
        else:
            impact_level = "low"
        impact_matrix.append(
            {
                "path": path,
                "impact_level": impact_level,
                "rank_score": score,
                "dependent_count": len(dependents),
                "linked_tests": linked_tests[:8],
                "reason": "public dependency surface" if dependents else "ranked task relevance",
            }
        )
    risk_register: list[dict[str, Any]] = []
    if not ranked_files:
        risk_register.append(
            {
                "risk": "no_ranked_source_candidate",
                "severity": "high",
                "mitigation": "block broad source mutation until a focused file or failing test identifies the target",
            }
        )
    uncovered_public = [item for item in impact_matrix if item.get("dependent_count", 0) and not item.get("linked_tests")]
    if uncovered_public:
        risk_register.append(
            {
                "risk": "public_surface_without_static_test_link",
                "severity": "medium",
                "affected_paths": [str(item.get("path")) for item in uncovered_public[:8]],
                "mitigation": "run broader verification or require manual coverage review before approval",
            }
        )
    if not test_files:
        risk_register.append(
            {
                "risk": "no_test_surface_detected",
                "severity": "medium",
                "mitigation": "require syntax checks and task-specific verification commands",
            }
        )
    acceptance_criteria = [
        {"criterion": "requested_behavior_addressed", "verification": "patch candidate selected from explicit contract, task text, or test evidence"},
        {"criterion": "source_scope_is_explained", "verification": "changed files map back to repo survey or review warns about drift"},
        {"criterion": "changed_python_compiles", "verification": "py_compile runs for changed Python files"},
        {"criterion": "task_verification_passes", "verification": "requested or inferred verification commands return zero"},
        {"criterion": "review_has_no_blockers", "verification": "code_review decision record approves final package"},
    ]
    test_strategy = {
        "primary_commands": suggested_verification_commands(test_files),
        "linked_test_targets": sorted({test for tests in linked_tests_by_source.values() for test in tests})[:12],
        "fallback_checks": ["python -m py_compile <changed .py files>", "git diff --check"],
        "coverage_note": "Prefer linked tests for changed sources; use broader discovery when public dependents are present.",
    }
    return {
        "impact_matrix": impact_matrix,
        "risk_register": risk_register,
        "acceptance_criteria": acceptance_criteria,
        "test_strategy": test_strategy,
        "readiness_checks": {
            "has_ranked_sources": bool(ranked_files),
            "has_acceptance_criteria": bool(acceptance_criteria),
            "has_test_strategy": bool(test_strategy.get("primary_commands") or test_strategy.get("fallback_checks")),
            "high_risk_count": sum(1 for item in risk_register if item.get("severity") == "high"),
        },
    }


def repo_survey(repo_root: Path, goal: str) -> dict[str, Any]:
    extension_counts: Counter[str] = Counter()
    candidate_files: list[str] = []
    test_files: list[str] = []
    config_files: list[str] = []
    python_symbols: list[dict[str, Any]] = []
    total_files = 0
    for path in sorted(repo_root.rglob("*")):
        if any(part in EXCLUDED_DIRS for part in path.relative_to(repo_root).parts):
            continue
        if not path.is_file():
            continue
        rel = str(path.relative_to(repo_root))
        if rel.endswith((".pyc", ".sqlite3", ".gguf", ".safetensors", ".bin", ".apk")):
            continue
        total_files += 1
        suffix = path.suffix.lower() or "[no_ext]"
        extension_counts[suffix] += 1
        lowered = rel.lower()
        if any(marker in lowered for marker in ("test", "self_test", "spec")):
            test_files.append(rel)
        if path.suffix == ".py" and len(python_symbols) < 80:
            python_symbols.append(python_file_summary(repo_root, path))
        if path.name in {"pyproject.toml", "package.json", "build.gradle", "settings.gradle", "gradlew", "requirements.txt"}:
            config_files.append(rel)
        goal_tokens = {token for token in goal.lower().replace("/", " ").replace("_", " ").split() if len(token) > 3}
        rel_tokens = set(lowered.replace("/", " ").replace("_", " ").replace("-", " ").split())
        if goal_tokens & rel_tokens:
            candidate_files.append(rel)
    dominant_extensions = [{"extension": ext, "count": count} for ext, count in extension_counts.most_common(12)]
    repo_map = build_repo_map(goal, candidate_files[:80], test_files[:80], python_symbols)
    dependency_graph = import_dependency_graph(python_symbols)
    call_graph = call_graph_summary(python_symbols)
    reading_plan = targeted_reading_plan(repo_map, dependency_graph)
    hypotheses = engineering_hypotheses(goal, repo_map, dependency_graph)
    readiness_model = engineering_readiness_model(goal, repo_map, dependency_graph, test_files[:80])
    return {
        "repo_root": str(repo_root),
        "goal": goal,
        "total_files_scanned": total_files,
        "dominant_extensions": dominant_extensions,
        "candidate_files": candidate_files[:80],
        "test_files": test_files[:80],
        "python_symbols": python_symbols,
        "suggested_verification_commands": suggested_verification_commands(test_files),
        "repo_map": repo_map,
        "engineering_investigation": {
            "dependency_graph": dependency_graph,
            "call_graph": call_graph,
            "targeted_reading_plan": reading_plan,
            "hypotheses": hypotheses,
            "design_decision_seed": [
                "Prefer the smallest patch that satisfies the failing test or explicit user contract.",
                "Inspect dependents before changing public functions or modules with reverse dependencies.",
                "If no high-confidence source candidate exists, block with a focused clarification instead of broad mutation.",
            ],
        },
        "engineering_readiness": readiness_model,
        "config_files": config_files[:40],
        "excluded_dirs": sorted(EXCLUDED_DIRS),
        "summary": f"Surveyed {total_files} files; found {len(test_files)} test-like files and {len(candidate_files)} goal-matching candidates.",
    }

def run_repository_survey(request: dict[str, Any], workspace_root: Path, output_path: str) -> dict[str, Any]:
    goal = request_goal(request)
    survey = repo_survey(target_repo_root(request), goal)
    model_guidance = code_model_guidance(request, "repository survey, source prioritization, and risk discovery")
    survey["role_policy"] = role_policy_from_request(request)
    survey["task_profile"] = task_profile_from_request(request)
    survey["worker_brief"] = worker_brief_from_request(request)
    survey["model_guidance"] = model_guidance
    survey["engineering_investigation"]["model_guidance"] = model_guidance
    if model_guidance.get("risk_markers"):
        survey["engineering_readiness"].setdefault("risk_register", [])
        survey["engineering_readiness"]["risk_register"].append(
            {
                "risk": "model_guidance_requires_attention",
                "severity": "medium",
                "markers": model_guidance.get("risk_markers", []),
                "mitigation": "Downstream planning and review must account for the model guidance before approval.",
            }
        )
    write_json(workspace_root, output_path, survey)
    return {
        "ok": True,
        "worker": worker_name(),
        "task_id": request.get("task_id"),
        "status": "completed",
        "summary": survey["summary"],
        "artifacts": [output_path],
        "confidence": "medium",
        "model_guidance": model_guidance,
    }
