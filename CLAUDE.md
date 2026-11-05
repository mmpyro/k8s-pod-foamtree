# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`k8sfoams`: a local, read-only Flask dashboard that visualizes Kubernetes **resource requests** (not live usage) per node and pod. It reads `~/.kube/config` / `$KUBECONFIG`, never writes to the cluster or the kubeconfig. Python package in `k8sfoam/`, React UI bundled with esbuild. Supports Python >= 3.9, so keep `typing.Optional`/`List` style, no `X | None`.

## Commands

```bash
make restore_dev     # uv pip install -e '.[dev]'
make run-synthetic   # build UI + run on generated clusters (no cluster needed; best for UI work)
make run             # build UI + run against kubeconfig, http://127.0.0.1:8080
npm run watch        # rebuild bundle.js on save while the server runs

make tests           # mypy + flake8 + bandit + pytest (what CI runs, via tests_ci)
make unit_tests      # pytest only
uv run pytest k8sfoam/tests/common/test_scheduler.py::test_name -v   # single test

make prices          # regenerate k8sfoam/src/pricing/prices.json.gz (AWS/GCP need credentials, see docs/cost.md)
make docs-serve      # MkDocs site with live reload; also exports the synthetic demo into docs/demo/
make docs-build      # mkdocs build --strict, as CI does
```

flake8: max line length 180. `bundle.js` and `docs/demo/` are generated and gitignored; `make web` / `make docs-demo` rebuild them.

## Architecture

**Data path.** Kubernetes objects -> `utils/extractors.py` (`ResourcesExtractor`) -> immutable DTOs (`common/dtos.py`: `NodeResources`, `PodResources`, `DisruptionBudget`) -> `utils/mappers.py` (`FoamTreeMapper`) -> FoamTree JSON for the UI. Everything downstream of the extractor works on DTOs only, never on kubernetes client objects.

**Where clusters come from (`app.py`, `Registry` protocol).** `registry.get(context)` returns something "read like a K8sClient" (`get_node_resources()`, `get_pod_resources()`, plus `version` / `wait_for_change()` for live updates). Three implementations:
- `k8s/cache.py` `CacheRegistry`: per-context watch caches (`Informer` = LIST + WATCH, idle caches swept after 15 min). Default.
- `k8s/k8s_client.py` `K8sClient`: direct paged LIST per request (`--no-watch`; `/events` then returns 404 and the UI polls).
- `synthetic/client.py` `SyntheticRegistry`: generated clusters from `synthetic/scenarios.py` + `model.py`, with churn (`--synthetic`, `--seed`, `--churn-interval`).

A new data source or field must work in all three.

**HTTP API (`app.py` `create_app`).** `/resources[/<type>]`, `/simulate/{fit,drain,pending,consolidation,replicas}`, `/events` (SSE `changed` events, debounced, with heartbeat), `/contexts`. Request bodies are parsed/validated in `app.py` (`parse_*`); errors return plain text with 400/500, which `api.jsx` surfaces as the message.

**Scheduler simulation (`common/scheduler.py`).** Pure functions over DTOs that mimic kube-scheduler filtering (requests incl. extended resources, taints/tolerations, node affinity, pod (anti-)affinity, topology spread, PDBs) for the simulators. Hypothetical "extra nodes" come from request bodies.

**Pricing (`pricing/`).** `PriceBook` prices nodes from `spec.providerID` + topology labels, using the bundled `prices.json.gz` and a background live refresh (`fetchers.py`, 24 h TTL). Rule: pricing never fails a request; a missing price is `None` and the UI warns. Synthetic mode uses the snapshot only (no network).

**Static demo export (`synthetic/export.py`).** Writes the frontend plus JSON files of every endpoint's answer for the synthetic clusters into `docs/demo/`. `api.jsx` switches to these files when `window.K8SFOAM_STATIC` is set. A new or changed endpoint usually needs a matching change in `export.py` and in `api.jsx`'s `staticApi`.

**Frontend (`k8sfoam/src/frontend/`).** `.jsx` files share code through `window.*` globals (they were once separate `<script>` tags), so `main.js` imports them in dependency order; a new file must be added there in the right place. All server calls go through `window.k8sApi` in `api.jsx`. `app.jsx` is the root; `simulate.jsx`, `scene3d.jsx` (three.js) and `treemap.jsx` are the big views. View state is mirrored in the URL by `urlstate.jsx` (shareable links).

## Tests

`k8sfoam/tests/` mirrors `k8sfoam/src/`. Kubernetes objects are built with `MagicMock` helpers in `tests/common/mocks.py`. Unset API fields must be `None`, not a bare MagicMock (always truthy), e.g. the last LIST page's continue token.

## Docs

User docs live in `docs/` (MkDocs Material, deployed to GitHub Pages from `main`). Feature changes usually need the matching page updated (`docs/http-api.md`, `docs/cli.md`, `docs/cost.md`, etc.).
