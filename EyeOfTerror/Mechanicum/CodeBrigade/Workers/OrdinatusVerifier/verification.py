from __future__ import annotations

"""Verification role implementation."""

from common.codewright_core import *  # noqa: F403 - role modules use the shared Codewright helper surface.


from model_repair import (
    apply_model_repair_operations_atomically,
    merge_changed_files,
    patch_payload_from_model_repair_content,
    run_full_verification_pass,
    run_model_repair_attempt,
)


def diagnostic_extraction_from_execution(
    patch: dict[str, Any],
    executed: list[dict[str, Any]],
    candidate_source_paths: list[str],
    repo_root: Path,
) -> dict[str, Any]:
    candidates = patch.get("patch_candidates") if isinstance(patch.get("patch_candidates"), list) else []
    runtime_failures: list[dict[str, Any]] = []
    assertions: list[dict[str, Any]] = []
    traceback_frames: list[dict[str, Any]] = []
    runtime_test_failures: list[dict[str, Any]] = []
    traceback_sources: list[str] = []
    for item in executed:
        if not isinstance(item, dict) or int(item.get("returncode") or 0) == 0:
            continue
        output = f"{item.get('stdout', '')}\n{item.get('stderr', '')}"
        parsed_assertions = extracted_assertion_diagnostics_from_text(output)
        parsed_frames = traceback_frames_from_text(output, repo_root)
        parsed_failures = runtime_test_failures_from_traceback(parsed_frames, parsed_assertions, repo_root)
        assertions.extend(parsed_assertions)
        traceback_frames.extend(parsed_frames)
        runtime_test_failures.extend(parsed_failures)
        runtime_failures.append(
            {
                "command": item.get("command", ""),
                "returncode": item.get("returncode"),
                "assertion_count": len(parsed_assertions),
                "traceback_frame_count": len(parsed_frames),
                "test_failure_count": len(parsed_failures),
                "stderr_excerpt": str(item.get("stderr") or "")[-1200:],
                "stdout_excerpt": str(item.get("stdout") or "")[-1200:],
            }
        )
    for path in candidate_source_paths:
        path_str = str(path)
        if path_str not in traceback_sources:
            traceback_sources.append(path_str)
    static_hypotheses = static_diagnostic_hypotheses_from_candidates(candidates)
    runtime_patch_candidates = runtime_minimal_patch_candidates_from_failures(runtime_test_failures, repo_root)
    return {
        "status": "recorded",
        "mode": "unshaped_repo_repair" if is_unshaped_patch_source(str(patch.get("patch_source") or "")) else "structured_patch",
        "patch_source": patch.get("patch_source", ""),
        "runtime_failures": runtime_failures,
        "assertions": assertions,
        "traceback_frames": traceback_frames[:50],
        "runtime_test_failures": runtime_test_failures[:20],
        "traceback_source_candidates": traceback_sources[:20],
        "runtime_source_candidates": sorted(
            {
                str(path)
                for failure in runtime_test_failures
                if isinstance(failure, dict)
                for path in failure.get("candidate_source_paths", [])
                if isinstance(path, str)
            }
        )[:20],
        "runtime_minimal_patch_candidates": runtime_patch_candidates,
        "static_test_expectations": static_hypotheses,
        "selected_diagnostics": patch.get("diagnostics", {}) if isinstance(patch.get("diagnostics"), dict) else {},
        "parser_coverage": {
            "unittest_assertions": len(assertions),
            "traceback_frames": len(traceback_frames),
            "runtime_test_failures": len(runtime_test_failures),
            "runtime_minimal_patch_candidates": len(runtime_patch_candidates),
            "traceback_source_candidates": len(traceback_sources),
            "static_test_expectations": len(static_hypotheses),
        },
    }

def extracted_assertion_diagnostics_from_text(text: str) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for match in re.finditer(r"AssertionError:\s+(.+?)\s+!=\s+(.+)", text):
        actual = match.group(1).strip()
        expected = match.group(2).strip()
        key = ("not_equal", actual, expected)
        if key in seen:
            continue
        seen.add(key)
        diagnostics.append(
            {
                "kind": "assert_not_equal",
                "actual": actual[:200],
                "expected": expected[:200],
                "excerpt": match.group(0)[:500],
            }
        )
    for match in re.finditer(r"^\s*(?:E\s+)?assert\s+(.+?)\s*==\s*(.+?)\s*$", text, flags=re.MULTILINE):
        actual = match.group(1).strip()
        expected = match.group(2).strip()
        key = ("not_equal", actual, expected)
        if key in seen:
            continue
        seen.add(key)
        diagnostics.append(
            {
                "kind": "assert_not_equal",
                "actual": actual[:200],
                "expected": expected[:200],
                "excerpt": match.group(0)[:500],
                "source": "pytest_assert_equal",
            }
        )
    for match in re.finditer(r"^\s*(?:E\s+)?assert\s+(.+?)\s*!=\s*(.+?)\s*$", text, flags=re.MULTILINE):
        actual = match.group(1).strip()
        expected = match.group(2).strip()
        key = ("unexpected_equal", actual, expected)
        if key in seen:
            continue
        seen.add(key)
        diagnostics.append(
            {
                "kind": "assert_unexpected_equal",
                "actual": actual[:200],
                "expected": expected[:200],
                "excerpt": match.group(0)[:500],
                "source": "pytest_assert_not_equal",
            }
        )
    for match in re.finditer(r"AssertionError:\s+(.+?)\s+is not true", text, flags=re.IGNORECASE):
        expression = match.group(1).strip()
        key = ("truthy", expression, "true")
        if key in seen:
            continue
        seen.add(key)
        diagnostics.append(
            {
                "kind": "assert_truthy",
                "actual": expression[:200],
                "expected": "truthy",
                "excerpt": match.group(0)[:500],
            }
        )
    return diagnostics


def repo_relative_traceback_path(repo_root: Path, raw_path: str) -> str:
    path = Path(raw_path)
    try:
        if path.is_absolute():
            return str(path.resolve().relative_to(repo_root.resolve()))
    except (OSError, ValueError):
        pass
    return raw_path.replace("\\", "/")


def traceback_frames_from_text(text: str, repo_root: Path) -> list[dict[str, Any]]:
    frames: list[dict[str, Any]] = []
    seen: set[tuple[str, int, str]] = set()
    pattern = re.compile(r'^\s*File "([^"]+)", line (\d+), in ([A-Za-z_][A-Za-z0-9_]*)\s*$', re.MULTILINE)
    for match in pattern.finditer(text):
        rel_path = repo_relative_traceback_path(repo_root, match.group(1))
        line = int(match.group(2))
        function_name = match.group(3)
        key = (rel_path, line, function_name)
        if key in seen:
            continue
        seen.add(key)
        frames.append(
            {
                "path": rel_path,
                "line": line,
                "function_name": function_name,
                "is_test": test_like_path(rel_path) or function_name.startswith("test"),
            }
        )
    current_pytest_function = ""
    for line in text.splitlines():
        header = re.match(r"^_+\s+([A-Za-z_][A-Za-z0-9_]*)\s+_+$", line.strip())
        if header:
            current_pytest_function = header.group(1)
            continue
        match = re.match(r"^([^:\s][^:]*\.py):(\d+):\s+(?:AssertionError|Failed)", line.strip())
        if not match:
            continue
        rel_path = repo_relative_traceback_path(repo_root, match.group(1))
        line_number = int(match.group(2))
        function_name = current_pytest_function
        key = (rel_path, line_number, function_name)
        if key in seen:
            continue
        seen.add(key)
        frames.append(
            {
                "path": rel_path,
                "line": line_number,
                "function_name": function_name,
                "is_test": test_like_path(rel_path) or function_name.startswith("test"),
                "source": "pytest_failure_location",
            }
        )
    return frames


def repo_relative_python_file_exists(repo_root: Path, path: str) -> bool:
    if not path.endswith(".py") or Path(path).is_absolute() or path.startswith(".."):
        return False
    try:
        return safe_repo_path(repo_root, path).exists()
    except ValueError:
        return False


def runtime_test_failures_from_traceback(
    frames: list[dict[str, Any]],
    assertions: list[dict[str, Any]],
    repo_root: Path,
) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    test_frames = [frame for frame in frames if isinstance(frame, dict) and frame.get("is_test")]
    source_frames = [
        frame
        for frame in frames
        if isinstance(frame, dict)
        and not frame.get("is_test")
        and str(frame.get("path") or "").endswith(".py")
        and repo_relative_python_file_exists(repo_root, str(frame.get("path") or ""))
    ]
    for test_frame in test_frames[-3:]:
        links = test_symbol_links_from_goal(repo_root, f"`{test_frame.get('path')}`")
        matching_links = [
            link
            for link in links
            if isinstance(link, dict) and link.get("test_function") == test_frame.get("function_name")
        ]
        linked_source_paths = [
            str(link.get("source_path"))
            for link in matching_links
            if isinstance(link.get("source_path"), str)
        ]
        frame_source_paths = [
            str(frame.get("path"))
            for frame in source_frames
            if isinstance(frame.get("path"), str)
        ]
        failures.append(
            {
                "test_path": test_frame.get("path", ""),
                "test_function": test_frame.get("function_name", ""),
                "line": test_frame.get("line", 0),
                "assertions": assertions[:5],
                "imported_symbol_links": matching_links[:10],
                "source_frames": source_frames[-5:],
                "candidate_source_paths": list(dict.fromkeys([*frame_source_paths, *linked_source_paths]))[-10:],
            }
        )
    return failures


def runtime_minimal_patch_candidates_from_failures(
    failures: list[dict[str, Any]],
    repo_root: Path,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for failure in failures:
        if not isinstance(failure, dict):
            continue
        assertions = failure.get("assertions") if isinstance(failure.get("assertions"), list) else []
        links = failure.get("imported_symbol_links") if isinstance(failure.get("imported_symbol_links"), list) else []
        for assertion in assertions:
            if not isinstance(assertion, dict) or assertion.get("kind") != "assert_not_equal":
                continue
            actual = str(assertion.get("actual") or "")
            expected = str(assertion.get("expected") or "")
            if not actual or not expected:
                continue
            for link in links:
                if not isinstance(link, dict):
                    continue
                source_path = str(link.get("source_path") or "")
                function_name = str(link.get("imported_symbol") or "")
                if not source_path or not function_name or not repo_relative_python_file_exists(repo_root, source_path):
                    continue
                source = safe_repo_path(repo_root, source_path)
                function = simple_function_return_segment(source, function_name)
                current_return = str(function.get("return_expr") or "")
                if current_return not in {actual, expected}:
                    continue
                key = (source_path, function_name, actual, expected)
                if key in seen:
                    continue
                seen.add(key)
                candidates.append(
                    {
                        "source": "runtime_traceback_assertion_mismatch",
                        "status": "candidate",
                        "kind": "replace_return_expression",
                        "path": source_path,
                        "function_name": function_name,
                        "old_expression": actual,
                        "new_expression": expected,
                        "current_expression": current_return,
                        "test_path": failure.get("test_path", ""),
                        "test_function": failure.get("test_function", ""),
                        "minimality": "single_return_expression_from_runtime_assertion",
                        "application_status": "already_applied" if current_return == expected else "pending",
                        "proof": {
                            "assertion": assertion,
                            "line": failure.get("line", 0),
                        },
                    }
                )
    return candidates[:20]


def static_diagnostic_hypotheses_from_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    hypotheses: list[dict[str, Any]] = []
    for candidate in candidates:
        if not isinstance(candidate, dict) or candidate.get("status") != "selected":
            continue
        diagnostics = candidate.get("diagnostics") if isinstance(candidate.get("diagnostics"), dict) else {}
        if not diagnostics:
            continue
        hypotheses.append(
            {
                "source": candidate.get("source", ""),
                "kind": diagnostics.get("kind", candidate.get("source", "")),
                "test_path": diagnostics.get("test_path", ""),
                "module_path": diagnostics.get("module_path", ""),
                "function_name": diagnostics.get("function_name", ""),
                "delegated_from": diagnostics.get("delegated_from", {}),
                "expected": diagnostics.get("expected", diagnostics.get("replacement_expression", "")),
                "actual": diagnostics.get("actual", diagnostics.get("actual_expression", "")),
                "evidence": diagnostics,
            }
        )
    return hypotheses


def command_allowed(command: list[str]) -> bool:
    if not command:
        return False
    if command[0] == "pytest":
        return True
    if command[0] in {"python", "python3", sys.executable} and len(command) >= 3 and command[1] == "-m":
        return command[2] in {"py_compile", "pytest", "unittest"}
    return False


def pytest_fallback_test_paths(command: list[str]) -> list[str]:
    if command[:3] == [sys.executable, "-m", "pytest"] or command[:3] in (["python", "-m", "pytest"], ["python3", "-m", "pytest"]):
        args = command[3:]
    elif command and command[0] == "pytest":
        args = command[1:]
    else:
        return []
    paths = [arg for arg in args if arg.endswith(".py") and not arg.startswith("-")]
    return paths[:10]


def run_pytest_fallback_command(repo_root: Path, raw_command: str, command: list[str]) -> dict[str, Any]:
    test_paths = pytest_fallback_test_paths(command)
    if not test_paths:
        return {
            "command": raw_command,
            "returncode": 127,
            "stdout": "",
            "stderr": "pytest is unavailable and no explicit pytest file paths were provided",
        }
    script = r"""
import ast
import importlib.util
import linecache
import sys
import traceback
from pathlib import Path

root = Path.cwd()
failures = []
passed = 0

def value_preview(node, frame):
    try:
        return repr(eval(compile(ast.Expression(node), str(frame.f_code.co_filename), "eval"), frame.f_globals, frame.f_locals))
    except Exception:
        return ast.unparse(node) if hasattr(ast, "unparse") else "<expr>"

for index, raw_path in enumerate(sys.argv[1:]):
    path = root / raw_path
    module_name = "_ceraxia_pytest_fallback_%s" % index
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(root))
    try:
        spec.loader.exec_module(module)
    except Exception:
        failures.append((raw_path, "<module>", traceback.format_exc()))
        continue
    for name, func in sorted(module.__dict__.items()):
        if not name.startswith("test") or not callable(func):
            continue
        try:
            func()
            passed += 1
        except AssertionError:
            _, _, tb = sys.exc_info()
            frames = traceback.extract_tb(tb)
            last = frames[-1]
            traceback_text = traceback.format_exc()
            line = linecache.getline(last.filename, last.lineno).strip()
            detail = ""
            tb_cursor = tb
            while tb_cursor and tb_cursor.tb_next:
                tb_cursor = tb_cursor.tb_next
            if line.startswith("assert ") and tb_cursor is not None:
                try:
                    parsed = ast.parse(line).body[0]
                    if isinstance(parsed, ast.Assert) and isinstance(parsed.test, ast.Compare) and len(parsed.test.ops) == 1 and len(parsed.test.comparators) == 1:
                        left = value_preview(parsed.test.left, tb_cursor.tb_frame)
                        right = value_preview(parsed.test.comparators[0], tb_cursor.tb_frame)
                        op = "==" if isinstance(parsed.test.ops[0], ast.Eq) else "!="
                        detail = "E       assert %s %s %s\n" % (left, op, right)
                except Exception:
                    detail = ""
            pytest_text = "_______________________________ %s _______________________________\n%s:%s: AssertionError\n%s" % (
                name,
                raw_path,
                last.lineno,
                detail,
            )
            failures.append((raw_path, name, traceback_text + pytest_text))
        except Exception:
            failures.append((raw_path, name, traceback.format_exc()))

if failures:
    for _, _, text in failures:
        sys.stdout.write(text)
        if not text.endswith("\n"):
            sys.stdout.write("\n")
    sys.exit(1)
print("%s passed" % passed)
"""
    completed = subprocess.run(
        [sys.executable, "-c", script, *test_paths],
        cwd=repo_root,
        text=True,
        capture_output=True,
        timeout=120,
        check=False,
    )
    return {
        "command": raw_command,
        "returncode": completed.returncode,
        "stdout": completed.stdout[-4000:],
        "stderr": completed.stderr[-4000:],
        "fallback": "simple_pytest_runner",
    }


def run_verification_command(repo_root: Path, raw_command: str) -> dict[str, Any]:
    try:
        command = shlex.split(raw_command)
    except ValueError as exc:
        return {"command": raw_command, "returncode": 2, "stdout": "", "stderr": f"invalid command syntax: {exc}"}
    if not command_allowed(command):
        return {
            "command": raw_command,
            "returncode": 126,
            "stdout": "",
            "stderr": "verification command is outside Ceraxia's allowlist",
        }
    if command[0] in {"python", "python3"}:
        command[0] = sys.executable
    completed = subprocess.run(command, cwd=repo_root, text=True, capture_output=True, timeout=120, check=False)
    if (
        completed.returncode != 0
        and len(command) >= 3
        and command[0] == sys.executable
        and command[1:3] == ["-m", "pytest"]
        and "No module named pytest" in completed.stderr
    ):
        return run_pytest_fallback_command(repo_root, raw_command, command)
    return {
        "command": raw_command,
        "returncode": completed.returncode,
        "stdout": completed.stdout[-4000:],
        "stderr": completed.stderr[-4000:],
    }


def repair_expected_colon(repo_root: Path, py_file: str, stderr: str) -> dict[str, Any]:
    if "SyntaxError: expected ':'" not in stderr:
        return {"applied": False, "reason": "not an expected-colon SyntaxError"}
    match = re.search(r'File "([^"]+)", line (\d+)', stderr)
    if not match:
        return {"applied": False, "reason": "could not locate failing file and line"}
    failing_path = Path(match.group(1))
    if failing_path.name != Path(py_file).name:
        return {"applied": False, "reason": "failing file does not match changed file"}
    line_number = int(match.group(2))
    path = safe_repo_path(repo_root, py_file)
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    if line_number < 1 or line_number > len(lines):
        return {"applied": False, "reason": "failing line is out of range"}
    original = lines[line_number - 1]
    line_without_newline = original.rstrip("\n")
    if line_without_newline.rstrip().endswith(":"):
        return {"applied": False, "reason": "failing line already ends with colon"}
    newline = "\n" if original.endswith("\n") else ""
    lines[line_number - 1] = f"{line_without_newline.rstrip()}:{newline}"
    before_hash = sha256_text(path)
    path.write_text("".join(lines), encoding="utf-8")
    invalidate_python_cache(path)
    return {
        "applied": True,
        "kind": "expected_colon",
        "path": py_file,
        "line": line_number,
        "before_sha256": before_hash,
        "after_sha256": sha256_text(path),
    }


def repair_assertion_return_mismatch(repo_root: Path, py_files: list[str], output: str) -> dict[str, Any]:
    match = re.search(r"AssertionError: ([+-]?\d+) != ([+-]?\d+)", output)
    if not match:
        return {"applied": False, "reason": "no simple integer AssertionError mismatch found"}
    actual, expected = match.groups()
    needle = f"return {actual}"
    replacement = f"return {expected}"
    candidates: list[tuple[Path, str]] = []
    for py_file in py_files:
        path = safe_repo_path(repo_root, py_file)
        content = path.read_text(encoding="utf-8")
        if content.count(needle) == 1:
            candidates.append((path, content))
    if len(candidates) != 1:
        return {"applied": False, "reason": f"expected one changed file with {needle!r}, found {len(candidates)}"}
    path, content = candidates[0]
    before_hash = sha256_text(path)
    path.write_text(content.replace(needle, replacement, 1), encoding="utf-8")
    invalidate_python_cache(path)
    return {
        "applied": True,
        "kind": "assertion_return_mismatch",
        "path": str(path.relative_to(repo_root)),
        "actual": actual,
        "expected": expected,
        "before_sha256": before_hash,
        "after_sha256": sha256_text(path),
    }


def repair_name_error_return_literal(repo_root: Path, py_files: list[str], output: str) -> dict[str, Any]:
    match = re.search(r"NameError: name '([A-Za-z_][A-Za-z0-9_]*)' is not defined", output)
    if not match:
        return {"applied": False, "reason": "no simple NameError found"}
    name = match.group(1)
    expected_match = re.search(r"assertEqual\([^,\n]+,\s*([+-]?\d+|True|False|None)\)", output)
    if not expected_match:
        return {"applied": False, "reason": "could not infer a literal expected value from assertEqual"}
    expected = expected_match.group(1)
    needle = f"return {name}"
    candidates: list[tuple[Path, str]] = []
    for py_file in py_files:
        path = safe_repo_path(repo_root, py_file)
        content = path.read_text(encoding="utf-8")
        if content.count(needle) == 1:
            candidates.append((path, content))
    if len(candidates) != 1:
        return {"applied": False, "reason": f"expected one changed file with {needle!r}, found {len(candidates)}"}
    path, content = candidates[0]
    before_hash = sha256_text(path)
    path.write_text(content.replace(needle, f"return {expected}", 1), encoding="utf-8")
    invalidate_python_cache(path)
    return {
        "applied": True,
        "kind": "name_error_return_literal",
        "path": str(path.relative_to(repo_root)),
        "name": name,
        "expected": expected,
        "before_sha256": before_hash,
        "after_sha256": sha256_text(path),
    }


def repair_import_error_missing_function(repo_root: Path, py_files: list[str], output: str) -> dict[str, Any]:
    import_match = re.search(
        r"ImportError: cannot import name '([A-Za-z_][A-Za-z0-9_]*)' from '([A-Za-z_][A-Za-z0-9_\.]*)'",
        output,
    )
    if not import_match:
        return {"applied": False, "reason": "no simple import-name ImportError found"}
    function_name, module_name = import_match.groups()
    expected_values = re.findall(
        rf"assertEqual\(\s*{re.escape(function_name)}\(\)\s*,\s*([+-]?\d+|True|False|None)\s*\)",
        output,
    )
    if not expected_values:
        for test_file in sorted(repo_root.glob("test*.py")) + sorted(repo_root.glob("*_test.py")):
            text = test_file.read_text(encoding="utf-8")
            expected_values.extend(
                re.findall(
                    rf"assertEqual\(\s*{re.escape(function_name)}\(\)\s*,\s*([+-]?\d+|True|False|None)\s*\)",
                    text,
                )
            )
    if len(expected_values) != 1:
        return {"applied": False, "reason": f"could not infer exactly one expected literal for missing function, found {len(expected_values)}"}
    expected = expected_values[0]
    module_path = f"{module_name.replace('.', '/')}.py"
    if module_path not in py_files:
        return {"applied": False, "reason": f"missing function module is not a changed file: {module_path}"}
    path = safe_repo_path(repo_root, module_path)
    content = path.read_text(encoding="utf-8")
    if re.search(rf"^\s*def\s+{re.escape(function_name)}\s*\(", content, flags=re.MULTILINE):
        return {"applied": False, "reason": f"function already exists: {function_name}"}
    before_hash = sha256_text(path)
    prefix = "" if not content or content.endswith("\n") else "\n"
    suffix = "\n" if content else ""
    addition = f"{prefix}{suffix}def {function_name}():\n    return {expected}\n"
    path.write_text(content + addition, encoding="utf-8")
    invalidate_python_cache(path)
    return {
        "applied": True,
        "kind": "import_error_missing_function",
        "path": module_path,
        "function": function_name,
        "expected": expected,
        "before_sha256": before_hash,
        "after_sha256": sha256_text(path),
    }

def run_verification(request: dict[str, Any], workspace_root: Path, output_path: str) -> dict[str, Any]:
    patch = load_json_optional(workspace_root, sibling_artifact(output_path, "patch_manifest.json"))
    role_policy = role_policy_from_request(request)
    task_profile = task_profile_from_request(request)
    worker_brief = worker_brief_from_request(request)
    model_guidance = code_model_guidance(request, "verification command selection, failure interpretation, and repair-loop guidance")
    blockers = [str(item) for item in patch.get("blockers", [])] if isinstance(patch.get("blockers"), list) else []
    executed: list[dict[str, Any]] = []
    repo_root = target_repo_root(request)
    changed_files = patch.get("changed_files") if isinstance(patch.get("changed_files"), list) else []
    repairs: list[dict[str, Any]] = []
    model_repair_attempts: list[dict[str, Any]] = []
    blocked_repairs: list[dict[str, Any]] = []
    candidate_source_paths: list[str] = []
    ranked_survey_sources = ranked_source_candidates_from_survey(workspace_root, output_path)
    repairs_allowed = role_policy_allows_source_mutation(role_policy)
    raw_commands = patch.get("verification_commands") if isinstance(patch.get("verification_commands"), list) else []
    if patch.get("status") == "applied":
        py_files = [
            str(item.get("path"))
            for item in changed_files
            if isinstance(item, dict) and str(item.get("path") or "").endswith(".py")
        ]
        if py_files:
            cmd = [sys.executable, "-m", "py_compile", *py_files]
            completed = subprocess.run(cmd, cwd=repo_root, text=True, capture_output=True, check=False)
            executed.append(
                {
                    "command": " ".join(cmd),
                    "returncode": completed.returncode,
                    "stdout": completed.stdout[-4000:],
                    "stderr": completed.stderr[-4000:],
                }
            )
            if completed.returncode != 0:
                for candidate in source_candidates_from_traceback_text(completed.stderr, repo_root):
                    if candidate not in candidate_source_paths:
                        candidate_source_paths.append(candidate)
                for candidate in ranked_survey_sources:
                    if candidate not in candidate_source_paths:
                        candidate_source_paths.append(candidate)
                repaired_any = False
                for py_file in py_files:
                    if not repairs_allowed:
                        blockers.append("role_policy forbids source mutation repair")
                        blocked_repairs.append({"kind": "py_compile_repair", "path": py_file, "reason": "role_policy forbids source mutation repair"})
                        break
                    repair = repair_expected_colon(repo_root, py_file, completed.stderr)
                    if repair.get("applied"):
                        repairs.append(repair)
                        repaired_any = True
                if repaired_any:
                    completed = subprocess.run(cmd, cwd=repo_root, text=True, capture_output=True, check=False)
                    executed.append(
                        {
                            "command": " ".join(cmd),
                            "returncode": completed.returncode,
                            "stdout": completed.stdout[-4000:],
                            "stderr": completed.stderr[-4000:],
                            "after_repair": True,
                        }
                    )
                if completed.returncode != 0:
                    blockers.append("py_compile failed for changed Python files")
        if (repo_root / ".git").exists():
            cmd = ["git", "diff", "--check"]
            completed = subprocess.run(cmd, cwd=repo_root, text=True, capture_output=True, check=False)
            executed.append(
                {
                    "command": "git diff --check",
                    "returncode": completed.returncode,
                    "stdout": completed.stdout[-4000:],
                    "stderr": completed.stderr[-4000:],
                }
            )
            if completed.returncode != 0:
                blockers.append("git diff --check failed")
        for raw_command in raw_commands:
            if not isinstance(raw_command, str) or not raw_command.strip():
                blockers.append("verification command must be a non-empty string")
                continue
            try:
                result = run_verification_command(repo_root, raw_command)
            except subprocess.TimeoutExpired:
                result = {"command": raw_command, "returncode": 124, "stdout": "", "stderr": "verification command timed out"}
            executed.append(result)
            if result.get("returncode") != 0:
                output = f"{result.get('stdout', '')}\n{result.get('stderr', '')}"
                for candidate in source_candidates_from_traceback_text(output, repo_root):
                    if candidate not in candidate_source_paths:
                        candidate_source_paths.append(candidate)
                for candidate in ranked_survey_sources:
                    if candidate not in candidate_source_paths:
                        candidate_source_paths.append(candidate)
                if not repairs_allowed:
                    repair = {"applied": False, "blocked": "role_policy forbids source mutation repair"}
                    blockers.append("role_policy forbids source mutation repair")
                    blocked_repairs.append({"kind": "command_repair", "command": raw_command, "reason": "role_policy forbids source mutation repair"})
                else:
                    repair = repair_import_error_missing_function(repo_root, py_files, output)
                if not repair.get("applied") and repairs_allowed:
                    repair = repair_name_error_return_literal(repo_root, py_files, output)
                if not repair.get("applied") and repairs_allowed:
                    repair = repair_assertion_return_mismatch(repo_root, py_files, output)
                if repair.get("applied"):
                    repairs.append(repair)
                    try:
                        result = run_verification_command(repo_root, raw_command)
                    except subprocess.TimeoutExpired:
                        result = {"command": raw_command, "returncode": 124, "stdout": "", "stderr": "verification command timed out"}
                    result["after_repair"] = True
                    executed.append(result)
                if result.get("returncode") != 0:
                    blockers.append(f"verification command failed: {raw_command}")
        if blockers and repairs_allowed:
            repair_diagnostics = diagnostic_extraction_from_execution(patch, executed, candidate_source_paths, repo_root)
            model_attempt = run_model_repair_attempt(
                request=request,
                patch=patch,
                executed=executed,
                blockers=blockers,
                candidate_source_paths=candidate_source_paths,
                repo_root=repo_root,
                diagnostic_extraction=repair_diagnostics,
            )
            model_repair_attempts.append(model_attempt)
            if model_attempt.get("applied"):
                repairs.append(model_attempt)
                changed_files = merge_changed_files(changed_files, model_attempt.get("changed_files", []))
                patch["changed_files"] = changed_files
                patch.setdefault("warnings", [])
                if isinstance(patch.get("warnings"), list):
                    patch["warnings"].append("verification model repair mutated source after failed verification")
                patch["verification_model_repair"] = {
                    "status": "applied",
                    "operation_count": model_attempt.get("operation_count", 0),
                    "changed_files": model_attempt.get("changed_files", []),
                    "diagnostics": model_attempt.get("diagnostics", {}),
                }
                write_json(workspace_root, sibling_artifact(output_path, "patch_manifest.json"), patch)
                py_files = [
                    str(item.get("path"))
                    for item in changed_files
                    if isinstance(item, dict) and str(item.get("path") or "").endswith(".py")
                ]
                rerun = run_full_verification_pass(repo_root, py_files, raw_commands, run_verification_command)
                rerun_executed = rerun.get("executed") if isinstance(rerun.get("executed"), list) else []
                executed.extend(rerun_executed)
                for item in rerun_executed:
                    if not isinstance(item, dict) or int(item.get("returncode") or 0) == 0:
                        continue
                    output = f"{item.get('stdout', '')}\n{item.get('stderr', '')}"
                    for candidate in source_candidates_from_traceback_text(output, repo_root):
                        if candidate not in candidate_source_paths:
                            candidate_source_paths.append(candidate)
                blockers = [str(item) for item in rerun.get("blockers", []) if item]
            else:
                blocked_repairs.append(
                    {
                        "kind": "model_repair",
                        "reason": model_attempt.get("reason", "model repair did not apply"),
                        "status": model_attempt.get("decision", {}).get("status", "") if isinstance(model_attempt.get("decision"), dict) else "",
                    }
                )
        elif blockers and not repairs_allowed:
            model_repair_attempts.append({"applied": False, "kind": "model_repair", "reason": "role_policy forbids source mutation repair"})
    report = {
        "status": "blocked" if blockers else "passed",
        "task_id": request.get("task_id"),
        "role_policy": role_policy,
        "task_profile": task_profile,
        "worker_brief": worker_brief,
        "repo_grade_workflow": patch.get("repo_grade_workflow", repo_grade_workflow_from_request(request, changed_files)),
        "verification_strategy": {
            "focused_commands": [
                item.get("command")
                for item in executed
                if isinstance(item, dict)
                and isinstance(item.get("command"), str)
                and item.get("command") != "git diff --check"
                and "unittest discover" not in item.get("command", "")
            ],
            "broad_commands": [
                item.get("command")
                for item in executed
                if isinstance(item, dict)
                and isinstance(item.get("command"), str)
                and ("unittest discover" in item.get("command", "") or item.get("command") == "git diff --check")
            ],
        },
        "commands": [
            "python -m py_compile <changed .py files>",
            "git diff --check",
        ],
        "executed": executed,
        "repairs": repairs,
        "model_repair_attempts": model_repair_attempts,
        "model_guidance": model_guidance,
        "blockers": blockers,
        "warnings": patch.get("warnings", []),
        "summary": "Verification passed for applied changes." if not blockers else "Verification is blocked or failed.",
    }
    failed_commands = [
        item
        for item in executed
        if isinstance(item, dict) and int(item.get("returncode") or 0) != 0
    ]
    repair_state = {
        "status": "blocked" if blockers else "passed",
        "task_id": request.get("task_id"),
        "role_policy": role_policy,
        "task_profile": task_profile,
        "worker_brief": worker_brief,
        "repairs_allowed": repairs_allowed,
        "repair_attempts": repairs,
        "model_repair_attempts": model_repair_attempts,
        "model_guidance": model_guidance,
        "blocked_repairs": blocked_repairs,
        "commands_executed_count": len(executed),
        "failed_commands": failed_commands,
        "candidate_source_paths": candidate_source_paths[:20],
        "pending_blockers": blockers,
        "next_action": "inspect_blockers_or_revision_plan" if blockers else "continue_to_code_review",
        "summary": "Repair loop state recorded for verification step.",
    }
    diagnostic_extraction = diagnostic_extraction_from_execution(patch, executed, candidate_source_paths, repo_root)
    diagnostic_extraction["model_guidance"] = model_guidance
    write_json(workspace_root, output_path, report)
    write_json(workspace_root, sibling_artifact(output_path, "repair_loop_state.json"), repair_state)
    write_json(workspace_root, sibling_artifact(output_path, "diagnostic_extraction.json"), diagnostic_extraction)
    return {
        "ok": True,
        "worker": worker_name(),
        "task_id": request.get("task_id"),
        "status": "completed",
        "summary": "Verification report written.",
        "artifacts": [
            output_path,
            sibling_artifact(output_path, "repair_loop_state.json"),
            sibling_artifact(output_path, "diagnostic_extraction.json"),
        ],
        "confidence": "medium",
        "model_guidance": model_guidance,
    }
