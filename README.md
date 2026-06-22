# Shushunya

Shushunya is a local-first AI workspace rooted at `/media/shushunya/SHUSHUNYA/shushunya`.
Project code, runtime tools, virtual environments, generated assets, Android tooling, and models are kept under this tree.

## Modules

- `CoreOfMadness/` stores local model runtime pieces and the Telegram bot bridge.
- `ArchiveOfHeresy/` is the server gateway, memory/archive layer, OpenAI-compatible LLM proxy, and mobile backend facade.
- `Shushunya_M/` is the Android client.
- `Mechanicum/ShushunyaAgent/` is the server-side agent API used by the mobile app and console.
- `Mechanicum/RoxDub/` contains the dubbing/voice workflow.
- `PalatineConsole/` is the local service control GUI.
- `WarpWails/`, `DemonsForge/`, and `ShushunyaSite/` contain additional local tools and web surfaces.
- `android-tools/` and `.gradle-home/` hold Android build tooling inside the project root.

## Mobile Architecture

The Android app is a thin client. It should not call translator, STT, or agent services directly, and it should not be the source of truth for chat history.

The phone calls `ArchiveOfHeresy` through `https://chat.shushunya.com` with the mobile API key compiled into local debug builds. `ArchiveOfHeresy` then routes requests to the local server services:

- `/archive/chat/completions` for model chat with server-side session history.
- `/archive/chat/messages` for restoring server-side chat history.
- `/archive/mobile/translate` for translation.
- `/archive/mobile/stt-live` and `/archive/mobile/stt-pcm` for speech recognition.
- `/archive/mobile/agent/run`, `/archive/mobile/agent/run-stream`, `/archive/mobile/agent/cancel`, and `/archive/mobile/agent/state` for agent tasks.

`ARCHIVE_MOBILE_API_KEY` is accepted only by the mobile facade routes. Full archive and memory routes continue to require `ARCHIVE_API_KEY`.

## Operational Notes

- Local secrets live in ignored `.env` files and must not be committed.
- Large generated assets, APKs, virtual environments, model files, and runtime data are ignored by git.
- Run `./fix-permissions.sh` after moving the project between systems or users. It restores directory/file readability and executable bits for scripts, Android tools, llama.cpp, whisper.cpp, and Gradle's cached `aapt2`.
- Build the Android debug APK from `Shushunya_M/` with `SHUSHUNYA_MOBILE_API_KEY` supplied from `ArchiveOfHeresy/.env`.
