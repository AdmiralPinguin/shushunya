# Research Warband external evaluation

This directory is outside the Research Warband implementation on purpose. The
system under evaluation receives only an allowlisted mission payload and access
to a loopback fixture gateway. It never receives expected outcomes, gold facts,
source-span keys, forbidden claims, or cutover thresholds.

The current slice is evaluator hardening, not a model benchmark. It provides:

- strict duplicate-key and unknown-key rejection for suite and fixture JSON;
- immutable raw and normalized fixture bytes pinned by SHA-256 and byte count;
- a loopback-only search/document server with no directory listing;
- a `SubjectAdapter` boundary and deterministic replay subject;
- mechanical source, hash, UTF-8 byte-locator, and exact-quote verification;
- typed evidence relations (`reports`, `supports`, `refutes`, `qualifies`,
  `context`) plus deterministic required-fact/conflict/gap/outcome oracles;
- fail-closed lifecycle, identity, metrics, and atomic result handling;
- a sanitized LegacyIskandar RISC-V false-accept regression containing hashes
  and audited structural counts, not mutable run artifacts or copied web text.

Run from this directory:

```text
python run_eval.py
python -m unittest discover -s tests -v
```

The six public synthetic cases cover known answer, conflicting sources,
unanswerable/blocked, clarification, a negation entailment trap, and hostile
instructions embedded in a source. Public synthetic tasks are a smoke test only.
A cutover run must load a private bundle from a separately protected evaluator
root; private answer keys must never be placed in the readable project tree.

No live model or production endpoint is called by this slice.
