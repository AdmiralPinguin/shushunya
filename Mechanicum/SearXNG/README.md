# Shushunya SearXNG

Local SearXNG runtime for ShushunyaAgent web search.

The Python environment is intentionally inside the agent folder and has the same
name as the agent folder:

```text
/media/shushunya/SHUSHUNYA/shushunya/Mechanicum/ShushunyaAgent/ShushunyaAgent
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

ShushunyaAgent uses it through:

```bash
SHUSHUNYA_AGENT_SEARXNG_URL=http://127.0.0.1:8888
```

## Brave Search

If a real Brave Search API key is available, put it in the agent process
environment as:

```bash
SHUSHUNYA_AGENT_BRAVE_SEARCH_API_KEY=...
```

The agent provider order is Brave API, SearXNG, Marginalia, Wikipedia.
