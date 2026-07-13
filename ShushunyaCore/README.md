# ShushunyaCore

`ShushunyaCore` is the durable identity and agency layer of Shushunya. Archive,
Abaddon, Administratum, Vox and WarpWails are organs; none of them is the
personality by itself.

The service binds only to `127.0.0.1:7600` and uses the existing Gemma lane of
the priority dispatcher. Its canonical state lives in SQLite WAL under
`runtime/shushunya-core/`.

## Guarantees

- The incoming turn is durably appended before model processing.
- A resolution, commitment and external-effect outbox entry are committed in
  one transaction.
- Events are append-only; SQLite triggers reject update/delete.
- External delivery is at-least-once with stable idempotency keys.
- A model proposes actions but cannot extend the capability manifest.
- `blocked` is not a Core state. Waiting/failure states require an explanation,
  evidence, required action and resume condition.
- One broken organ degrades only its action. Foreground conversation remains
  available in speech-only mode.
- Repeated approval creates a preference proposal, not silent global authority.
- Identity changes are versioned proposals and require explicit approval.
- The steward may advance already-authorized commitments; doing nothing is a
  valid cycle when there is no sufficiently valuable work.

## API

- `POST /v1/turns/resolve` — one identity/context/decision pass.
- `POST /v1/effects/{id}/dispatch` — ask the fenced Core steward to dispatch a
  Core-owned Abaddon or Archive/Administratum effect.
- `GET /v1/commitments`, `GET /v1/events`, `GET /v1/self` — inspect truth.
- `POST/GET /v1/agenda` — finite background work with value, risk, budget and
  an explicit stop condition.
- `GET /health/ready` — database and recovery readiness.

## Run

```bash
mkdir -p runtime/shushunya-core
cp deploy/shushunya-core.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now shushunya-core.service
curl -fsS http://127.0.0.1:7600/health/ready
```

The current migration seam keeps Archive as the authenticated HTTP/SSE/chat
transport and memory owner. Archive assembles persona + Magos + live roster
once and sends that envelope to Core. Core alone leases and finalizes typed
effects; Archive only exposes the loopback Administratum adapter and persists
the factual outcome. It no longer runs a separate poor-context turn controller.
