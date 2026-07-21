# Shushunya Watch6 v87 — built-in microphone

Build date: 2026-07-21 (Asia/Seoul)

## Artifact

- APK: `Shushunya_Watch6_v87_BuiltIn_Mic.apk`
- applicationId: `com.shushunya.m`
- versionCode: `87`
- versionName: `8.28-wear-watch6-builtin-mic-racefix-v87`
- size: `1,580,653` bytes
- SHA-256: `EB5258EBA4D4760E89D999963B1A175673A97C7636093F52883765A06031BD83`
- APK Signature Scheme v2: verified
- signer SHA-256: `9E5E5D69B654C449A0405DF164F53429CADF3AE023A325A88582EB16B75B71C5`

## Behavior

- Production capture is fixed to the Galaxy Watch built-in microphone.
- Source: `VOICE_RECOGNITION`, 16 kHz, mono, PCM16.
- The selected and routed endpoint must both be `TYPE_BUILTIN_MIC`.
- Bluetooth SCO/HFP and PowerConf are not used by the production capture service.
- Existing framed PCM transport, reconnect, drain and terminal ACK behavior is preserved.
- A foreground-service launch/stop race is gated so a fast phone rejection cannot crash Wear OS.

## Verification

- Unit tests: 48 suites, 178 tests, 0 failures, 0 errors, 0 skipped.
- Android lint: 0 errors, 11 warnings.
- Release build and R8: successful.
- Installed over v86 on Samsung Galaxy Watch6: successful.
- Runtime rejection smoke test without headphones: no app crash, no lingering microphone service.
- Phone APK, watch face and server translation services were not changed.

The full live route proof requires the phone's mandatory TTS headphones to be connected. Without them the phone intentionally rejects translation before Watch capture starts.
