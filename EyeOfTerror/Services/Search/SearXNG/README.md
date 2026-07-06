# Shushunya SearXNG

Local SearXNG runtime for EyeOfTerror research workers.

The Python environment is intentionally local to this service:

```text
/media/shushunya/SHUSHUNYA/shushunya/EyeOfTerror/Services/Search/SearXNG/.venv
```

This directory keeps the cloned SearXNG source tree, runtime logs, bootstrap
files, and local `config/settings.yml` out of git. The local settings file
contains a secret key.

## Setup

```bash
./scripts/setup-searxng.sh
```

## Run

```bash
./scripts/start-searxng.sh
./scripts/check-searxng.sh
```

Default endpoint:

```text
http://127.0.0.1:8888
```

EyeOfTerror shared search tools use it through:

```bash
SHUSHUNYA_SEARCH_SEARXNG_URL=http://127.0.0.1:8888
```

## Brave Search

If a real Brave Search API key is available, put it in the worker process
environment as:

```bash
SHUSHUNYA_SEARCH_BRAVE_API_KEY=...
```

The agent provider order is Brave API, SearXNG, Marginalia, Wikipedia.
