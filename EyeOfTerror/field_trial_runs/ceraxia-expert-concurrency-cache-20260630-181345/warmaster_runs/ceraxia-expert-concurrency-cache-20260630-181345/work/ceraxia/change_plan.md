# Ceraxia Change Plan

Goal: кодовая expert-задача: исправь race-prone cache invalidation, докажи stale-read и concurrent behavior тестами, не используй sleep как синхронизацию и опиши residual concurrency risk.
CERAXIA_TARGET_REPO: /media/shushunya/SHUSHUNYA/shushunya/EyeOfTerror/field_trial_runs/ceraxia-expert-concurrency-cache-20260630-181345/fixture/repo
CERAXIA_FILES:
{
  "files": [
    {
      "path": "cache_store.py",
      "overwrite": true,
      "content": "import threading\n\nclass CacheStore:\n    def __init__(self):\n        self._lock = threading.RLock()\n        self._values = {}\n        self._version = 0\n\n    def get_or_load(self, key, loader):\n        with self._lock:\n            if key not in self._values:\n                self._values[key] = loader()\n            return self._values[key]\n\n    def invalidate(self, key):\n        with self._lock:\n            self._values.pop(key, None)\n            self._version += 1\n            return self._version\n\n    def version(self):\n        with self._lock:\n            return self._version\n"
    },
    {
      "path": "tests/test_cache_store.py",
      "content": "import threading\nimport unittest\nfrom cache_store import CacheStore\n\nclass CacheStoreTest(unittest.TestCase):\n    def test_invalidate_is_idempotent_and_reloadable(self):\n        store = CacheStore()\n        calls = []\n        self.assertEqual(store.get_or_load('a', lambda: 'old'), 'old')\n        self.assertEqual(store.invalidate('a'), 1)\n        self.assertEqual(store.invalidate('a'), 2)\n        self.assertEqual(store.get_or_load('a', lambda: 'new'), 'new')\n\n    def test_concurrent_readers_share_loaded_value(self):\n        store = CacheStore()\n        calls = []\n        def loader():\n            calls.append(1)\n            return 'value'\n        results = []\n        threads = [threading.Thread(target=lambda: results.append(store.get_or_load('k', loader))) for _ in range(8)]\n        for thread in threads:\n            thread.start()\n        for thread in threads:\n            thread.join()\n        self.assertEqual(results, ['value'] * 8)\n        self.assertEqual(len(calls), 1)\n\nif __name__ == '__main__':\n    unittest.main()\n"
    },
    {
      "path": "docs/cache_risk.md",
      "content": "# Cache Concurrency Risk\n\nUses an RLock around read, load, invalidate, and version updates.\n"
    }
  ],
  "verification_commands": [
    "python -m unittest tests.test_cache_store",
    "python -m py_compile cache_store.py"
  ]
}

## Scope
- Inspect the named task and constrain edits to the smallest coherent module set.
- Preserve user changes and expose blockers instead of guessing.

## Candidate Files
- cache_store.py

## Ranked Repo Map
- cache_store.py: score=14 reasons=[goal_filename_match, goal_symbol_match:cache,cachestore,store, python_source]

## Test Source Links

## Recommended Read Order
- inspect_source: cache_store.py (goal_filename_match, goal_symbol_match:cache,cachestore,store, python_source)

## Targeted Reading Plan
- cache_store.py: What contract does this file expose, and what tests or dependents would break if it changes? dependents=0

## Hypothesis Log
- [high] cache_store.py is likely relevant to the requested code change. evidence=[goal_filename_match, goal_symbol_match:cache,cachestore,store, python_source] risk=local change risk appears limited

## Design Decision Seed
- Prefer the smallest patch that satisfies the failing test or explicit user contract.
- Inspect dependents before changing public functions or modules with reverse dependencies.
- If no high-confidence source candidate exists, block with a focused clarification instead of broad mutation.

## File Impact Matrix
- cache_store.py: impact=medium dependents=0 tests=[]

## Risk Register
- [medium] no_test_surface_detected: require syntax checks and task-specific verification commands

## Acceptance Criteria
- requested_behavior_addressed: patch candidate selected from explicit contract, task text, or test evidence
- source_scope_is_explained: changed files map back to repo survey or review warns about drift
- changed_python_compiles: py_compile runs for changed Python files
- task_verification_passes: requested or inferred verification commands return zero
- review_has_no_blockers: code_review decision record approves final package

## Test Strategy
- fallback: python -m py_compile <changed .py files>
- fallback: git diff --check

## Test Surface

## Python Symbol Surface
- cache_store.py: functions=[] classes=[CacheStore]

## Suggested Verification

## Implementation Policy
- Produce an auditable patch manifest before mutating source files.
- Require verification commands or explicit blockers before final readiness.

## Task Profile
- kinds: explicit_patch, test_repair, multi_file, bugfix
- complexity: high
- risk: multi_file_scope_drift
- risk: test_diagnostic_required

## Worker Brief
- brief: Convert repo evidence into a scoped implementation plan.
- handoff_question: What is the narrowest safe patch path?
- must_produce: candidate file rationale
- must_produce: test surface
- must_produce: verification suggestions
- must_produce: risk notes

## Role Policy
- role: change_strategist
- authority: scoped_plan_from_repository_evidence
- may_mutate_source: False
- required_evidence: candidate_files
- required_evidence: test_surface
- required_evidence: implementation_policy
