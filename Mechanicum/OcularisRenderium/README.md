# OcularisRenderium

Prototype Mechanicum worker for JavaScript-rendered pages.

The active AuspexBrowser worker performs guarded HTTP text fetches and marks
scripted low-text pages with `render_required`; this worker consumes those
source snapshots through the common Worker API and writes
`rendered_snapshots.json`.

Current capabilities:

- Render public HTTP/HTTPS pages through an optional locked-down Playwright
  runtime when `OCULARIS_ENABLE_PLAYWRIGHT=1`.
- Return bounded DOM text snapshots.
- Keep a diagnostic fallback when Playwright or Chromium is unavailable.
- Report blocked navigation, network errors, and render timeouts as structured
  gaps instead of treating them as missing evidence.
