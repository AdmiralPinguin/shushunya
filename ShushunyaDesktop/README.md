# Shushunya Desktop

Fullscreen multi-monitor presence for turning the Linux workstation into
Shushunya. It is an interface to one companion, not a dashboard for his
services. The product intent is documented in `docs/PRODUCT_VISION.md`.

The application does not replace COSMIC or take over the compositor. It creates
one ordinary native fullscreen window per monitor and coordinates them as one
scene.

## Current screen policy

- Primary landscape display: `presence` — Shushunya himself and his current words.
- First portrait display: `mind` — what he is doing, intends, and needs from the owner.
- Third display: `canvas` — actual results and material he has brought back.
- Further displays: `ambient` — quiet synchronized presence.

Every role is an opaque full-screen organ of one possessed sanctum. The
cat-free `chaos-sanctum-panorama.png` supplies architecture and depth while the
original SVG pack under `assets/heresy/` supplies the living mark, fixed runes
and state fractures. The canonical face restores the richer fractured star
from the liked portrait-screen prototype, masks only its old crowded core and
places one restrained Eye of Horus there. Presence, Mind, Canvas and Ambient
now have different compositions; none is a dashboard, overlay or reskinned
copy of the same centered logo.

Roles are assigned by display identity and orientation, not by a hardcoded
monitor count. Hot-plug is supported. No window uses always-on-top,
layer-shell, transparency over other applications, or overlay behavior.
Per-display scale and safe-area overrides live in
`runtime/display_profiles.json`; `config/display_profiles.example.json`
documents the connector-and-model keyed format.

## Install

```bash
cd /media/shushunya/SHUSHUNYA/shushunya/ShushunyaDesktop
./scripts/install.sh
```

## Run from the active Linux desktop

Run this as the graphical user `shushunya`, not from the `codexbox` SSH
session:

```bash
cd /media/shushunya/SHUSHUNYA/shushunya/ShushunyaDesktop
./scripts/run.sh
```

Exit the development build with `Ctrl+Shift+Q`. Toggle fullscreen on the
focused display with `F11`. `Ctrl+Shift+S` refreshes live screenshots and
screen geometry under `runtime/live/` for layout diagnostics.

The normal launch is the disconnected visual prototype. It advances through
sleep, attention, thinking, forging, waiting, speaking, triumph, wounded and
sealing on every screen at once. `Right`/`Left` move through the states and
`Space` pauses or resumes the cycle; no control overlay is drawn. Live Core
data remains available explicitly with `./scripts/run.sh --core`.

If the launching terminal is still open, `Ctrl+C` stops the application. A
detached instance owned by the current graphical user can be stopped safely
with `./scripts/stop.sh`; it never tries to kill another user's process.
For a clean one-command replacement of the current instance, run
`./scripts/restart.sh` from a terminal inside the graphical `shushunya`
session. It waits for the old process to exit before creating the new windows.

Every real launch writes a strict placement gate to `runtime/live/layout.json`.
Verify a fresh two-display run with:

```bash
./scripts/verify-live.py --expect 2
```

The verifier fails if either role landed on the wrong connector, lost
fullscreen, has the wrong size, or the capture is stale.

To add a normal COSMIC application launcher for the current graphical user:

```bash
./scripts/install-user-launcher.sh
```

Autostart is deliberately opt-in:

```bash
./scripts/install-user-launcher.sh --autostart
```

## Preview capture without a desktop session

```bash
./scripts/capture-previews.sh
```

This writes deterministic role previews under `runtime/previews/` using the Qt
offscreen backend.

Responsive regression captures are generated with:

```bash
./scripts/capture-formats.sh
```

The matrix covers 1366x768, 2560x1080, 900x1600, 1280x1024 and portrait
1080x1920 layouts. `ambient` is captured explicitly in both landscape and
portrait orientations so future displays cannot silently inherit a
landscape-only composition.

Empty and deliberately overloaded state captures are generated with:

```bash
./scripts/capture-states.sh
```

These exercise quiet idle screens, long owner questions, multiple concurrent
thoughts, five agenda items, several results, wrapping, clamping and scrolling.

The complete nine-state visual matrix is generated with:

```bash
./scripts/capture-demo-states.sh
```

## Runtime data

The default prototype uses deterministic fake snapshots and does not require
Core. In explicit `--core` mode the visible model reads human-facing truth from
ShushunyaCore on loopback: identity, commitments, agenda, and a sanitized cursor
over resolved turns. Raw event payloads, prompts, health data, ports, delegates,
governors, and task IDs never enter QML.

Canonical conversation remains an Archive transport responsibility. The
Desktop does not manufacture Core turn envelopes or bypass memory assembly.
