# ResearchWarband shadow deployment

These files define two deliberately separate services. Neither unit edits,
stops, proxies, or binds the legacy Iskandar port `7101`.

- `research-warband-shadow.service`: production shadow on loopback `7201`,
  exact native Iskandar envelope, mandatory bearer token, one mission slot,
  persistent production mission store and CAS.
- `research-warband-evaluator.service`: tokenless loopback evaluator on `7202`,
  exact standalone allowlist, one slot, separate evaluator mission store and CAS.

Before installing the 7201 unit, copy
`research-warband-shadow-secret.env.example` to
`/media/shushunya/SHUSHUNYA/shushunya/.secrets/research-warband-shadow.env`,
replace the placeholder with a high-entropy token, and set mode `0600`.
The non-secret profiles and classifier remain repository files and are included
in deployment source attestation. Startup fails if a trusted contract file or
the classifier is missing, a symlink, malformed, or omitted from the attested
file list.
The exact imported search implementation (`EyeOfTerror/Services/Search/web_tools.py`)
and every `SHUSHUNYA_SEARCH_*` input are bound by the deployment guard. Put an
optional Brave key only in the mode-`0600` secret file; its manifest value is a
SHA-256 digest, never the raw credential.

Install the units into the user systemd directory only during an approved
deployment, run `systemctl --user daemon-reload`, and start the required profile
explicitly. Do not add either unit to the legacy 7101 launcher before cutover.
Before the first start, create the selected profile's
`runtime/research-warband-{shadow,evaluator}` directory with mode `0700` and
ownership matching the service user. `ProtectSystem=strict` makes the repository
read-only and exposes only that profile's runtime directory for writes;
`UMask=0077` keeps newly written mission and CAS data private. `Delegate=yes` is
required by the fail-closed per-attempt cgroup-v2 process boundary.
The units also redirect interpreter cache lookup away from the repository with
`PYTHONPYCACHEPREFIX=/dev/null` and invoke Python with `-B`; deployment must
remove old repository `__pycache__` and `.pyc` artifacts before the first
start. Startup rejects them and never deletes them automatically.

Submit a native canary package to 7201 with:

```bash
RESEARCH_WARBAND_BEARER_TOKEN='<secret>' \
  python -m EyeOfTerror.Scriptorium.ResearchWarband.integration.shadow_dispatch \
  /path/to/native-run --wait-sec 604800
```

With the isolated 7202 daemon running, invoke the HTTP evaluation subject with:

```bash
python -m EyeOfTerror.Scriptorium.ResearchWarband.integration.run_external_eval \
  --base-url http://127.0.0.1:7202 --out /absolute/path/current-result.json
```

This HTTP command defaults to the versioned `public_smoke_long_v1` real-model
gate: clarification gets 1,800 seconds and the evidence cases get 21,600
seconds. The immutable `public_smoke_v1` 20–120 second suite remains the fast
fake-subject/evaluator self-test and must not be used to judge the temporary 31B
and FIFO Qwen deployment.

The current attested author is the temporary TP1, text-only Gemma 31B runtime:
the dispatcher still advertises four legacy slots, while the physical upstream
has `max_num_seqs=2`, `max_model_len=7936`, and needs headroom for interactive
chat. ResearchWarband therefore uses `max_active=1`, `max_tokens=3072`, a
conservative 16,000-character request bound, and 8,000-character reader chunks.
Vision is intentionally not required or advertised by this profile. After the
second 3090 is installed, TP2/32k/vision and `max_active=4` require a new
versioned runtime contract and a clean service restart; do not edit this profile
in place.

Qwen is routed through the independent dispatcher lane on `8079` with
`X-LLM-Route: qwen`, background priority, and an `86400` second transport
timeout. The production attempt envelope is seven days; bounded rounds and
model-call budgets remain enforced inside the pipeline. Evidence remains in the
persistent CAS, while trusted knowledge-graph merge stays disabled.
