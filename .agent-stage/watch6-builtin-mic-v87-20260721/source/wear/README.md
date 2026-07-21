# Shushunya Wear controller

Wear OS companion for Samsung Galaxy Watch6. It exposes two complication data
sources and one transparent, user-visible launcher shortcut for the configurable
double-press Home action. The visible Activity is intentional: Android 14+ only
allows it to start the microphone foreground service from a current user gesture.

- `com.shushunya.m.wear.complication.LiveTranslationComplicationService`
- `com.shushunya.m.wear.complication.MusicControlComplicationService`

Both sources support `SHORT_TEXT`, `SMALL_IMAGE`, and `ICON`; use `SHORT_TEXT` as
the preferred/default type in a Watch Face Format face. The bundled
`com.shushunya.m.watchface` package is explicitly trusted as a safe default face.

## Data Layer contract

The wearable and phone APKs must use the same application ID (`com.shushunya.m`),
be signed by the same certificate, and both include Play Services Wearable.

Watch to nearby phone:

- `/shushunya/live/toggle`
- `/shushunya/music/toggle`
- `/shushunya/magic/toggle` (atomically toggles live translation and music)
- `/shushunya/state/request`

Payload: UTF-8 JSON `{ "requestId": "uuid" }`.

Phone to watch on `/shushunya/state`:

```json
{
  "requestId": "uuid-or-empty-for-async-state",
  "live": {
    "state": "running|paused|stopped|error",
    "status": "human readable status",
    "armed": true,
    "selectedMic": "configured input",
    "actualMic": "active input"
  },
  "music": {
    "state": "playing|paused|stopped|none",
    "packageName": "player package",
    "title": "track title"
  },
  "error": null
}
```

Only directly connected (`Node.isNearby()`) nodes receive commands. Messages are
best effort and contain no credentials or audio.

An empty/background state may refresh the complication, but it never owns the
Watch microphone. Capture lifetime changes only for the exact pending SET:
successful SET(false), failed SET(true), an explicit local abort, or a proven
built-in microphone route failure. Failed SET(false) retains the existing capture.

### ADB-only raw PowerConf probe

The diagnostic component is protected by `android.permission.DUMP`, never opens
a phone/server transport, and refuses to run while the translator capture is
active. It proves the exact PowerConf HFP identity, then uses only the modern
`AudioManager.getAvailableCommunicationDevices()` +
`setCommunicationDevice()` route (no `startVoiceRecognition` double-owner).

```text
adb shell am start -n com.shushunya.m/.wear.audio.PowerConfRawDiagnosticActivity \
  --ei seconds_per_source 4
adb shell ls /sdcard/Android/data/com.shushunya.m/files/diagnostics
adb pull /sdcard/Android/data/com.shushunya.m/files/diagnostics
```

Each run stores raw PCM, WAV, and `summary.json` for `VOICE_COMMUNICATION`,
`VOICE_RECOGNITION`, and `MIC`, including exact-route identity,
`isClientSilenced`, active recording configurations, peak/RMS, and an exact
all-zero verdict.

Watch microphone audio uses a continuous ChannelClient stream at
`/shushunya/audio/watch/v1`. The stream header is big-endian `SWH1`, protocol 1,
16 kHz, 320 samples, session id. Every 20 ms frame contains big-endian sequence,
watch elapsed-realtime timestamp, unsigned sample count and flags followed by
exactly 320 little-endian PCM16 mono samples. Production capture selects only a
Watch `TYPE_BUILTIN_MIC`, requests `VOICE_RECOGNITION`, and proves the routed
AudioRecord endpoint remains built-in; Bluetooth and other external inputs are
never substituted. A bounded 240 ms queue drops oldest
audio under backpressure and the writer derives `FLAG_GAP_BEFORE` on the first
surviving discontinuous sequence.

Phone-owned lifecycle messages use:

- phone to Watch binding: `/shushunya/audio/watch/binding/v1`;
- phone to Watch STOP/drain: `/shushunya/audio/watch/drain/v1`;
- Watch to phone terminal: `/shushunya/audio/watch/terminal/v1`;
- phone to Watch terminal ACK: `/shushunya/audio/watch/terminal-ack/v1`.

The START binding UUID and STOP UUID are distinct. The first valid STOP owner is
immutable, while its session may rebase to the current exact replacement channel.
Graceful STOP halts `AudioRecord`, flushes 1-4 buffered startup frames with a
monotonic fallback clock, drains every queued complete frame, and sends a
correlated `graceful_eos` terminal. The byte-identical terminal is retried at
0/250/750/1500/3000 ms inside one five-second ACK deadline. `FINISH` is accepted
only for an exact `finished` ACK with equal last sequence and zero measured drops.

A channel-only write failure keeps the exact Watch built-in capture and opens
at most three same-node replacement channels under one seven-second cumulative
budget (inside the phone's eight-second recovery gate). Late asynchronous channel
or output completions from a timed-out generation are closed as orphans. Route,
permission, format, and AudioRecord failures are hard terminal failures;
no microphone fallback exists.

## Root integration

Add `include(":wear")` to the root `settings.gradle`. The nested `settings.gradle`
only makes this directory independently buildable; Gradle ignores it when `wear`
is included by the parent project. Build and sign `:wear` with the same signing
configuration/certificate used for `:app`.
