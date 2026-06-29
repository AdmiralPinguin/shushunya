# Ceraxia

Ceraxia is the Inner Circle governor for code tasks.

She owns code-task decomposition, repository survey, scoped implementation
planning, patch manifest handoff, verification planning, code review, and final
handoff packaging.

## Default Worker Pipeline

```text
LogisRepository(repository_survey)
  -> MagosStrategos(change_planning)
  -> FerrumPatchwright(implementation)
  -> OrdinatusVerifier(verification)
  -> JudicatorCodicis(code_review)
  -> SealwrightFinalis(finalize)
```

## Current Boundary

The named workers currently share the same execution core, which keeps the
protocol stable while their internals are split into stronger specialized
implementations.

`FerrumPatchwright` can apply explicit patch operations embedded in the task:

```text
CERAXIA_TARGET_REPO: /absolute/path/to/repo
CERAXIA_PATCH:
{
  "operations": [
    {"type": "replace", "path": "module.py", "old": "return 1", "new": "return 2"},
    {"type": "write_file", "path": "new_file.py", "content": "..."}
  ],
  "verification_commands": ["python -m py_compile module.py"]
}
```

Without explicit patch operations, Ceraxia writes a blocked handoff package
instead of claiming the code task is complete.

Verification commands run without a shell and must match Ceraxia's allowlist:
`pytest`, `python -m pytest`, `python -m unittest`, or
`python -m py_compile ...`.

For simple tasks, Ceraxia can synthesize the patch spec from markers:

```text
CERAXIA_CREATE_FILE: generated.py
CERAXIA_FILE_CONTENT:
def generated_value():
    return 42

CERAXIA_VERIFY: python -m py_compile generated.py
```

or:

```text
CERAXIA_REPLACE_IN_FILE: module.py
CERAXIA_OLD:
return 1
CERAXIA_NEW:
return 2
CERAXIA_VERIFY: python -m py_compile module.py
```

The first verifier repair loop is intentionally narrow: when `py_compile`
reports `SyntaxError: expected ':'` for a changed Python file, Ceraxia can add
the missing colon to the failing line, rerun verification, and record the repair
in the final manifest.

Ceraxia can also repair a narrow unittest/pytest value mismatch:
`AssertionError: 1 != 2` can update exactly one `return 1` in a changed Python
file to `return 2`, rerun the failed verification command, and preserve the
repair evidence.

A second narrow test repair handles `NameError: name 'x' is not defined` when
the failing `assertEqual(..., literal)` exposes a simple expected literal and
the changed Python file contains exactly one `return x`.
