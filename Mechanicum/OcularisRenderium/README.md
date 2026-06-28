# OcularisRenderium

Planned Mechanicum worker for JavaScript-rendered pages.

It is intentionally not listed in `Mechanicum/worker_services.json` yet. The
active AuspexBrowser worker performs guarded HTTP text fetches and marks
scripted low-text pages with `render_required`; this worker is the planned
service that will later consume those gaps through the common Worker API.

Expected future capabilities:

- Render public HTTP/HTTPS pages through a locked-down browser runtime.
- Return bounded DOM text snapshots.
- Capture screenshots as artifacts.
- Report blocked navigation, network errors, and render timeouts as structured
  gaps instead of treating them as missing evidence.
