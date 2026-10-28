# Development

```bash
make restore_dev     # the package and its dev dependencies, in development mode
make run             # build the UI and start the server
make run-synthetic   # the same, on the synthetic clusters (no cluster needed)
```

## Tests

```bash
# All of them: type checking, linting, security, unit tests
make tests

# One suite at a time
make unit_tests
make static_code_analysis
make check_types
make bandit
```

## Frontend

The UI sources are the `.jsx` files in `k8sfoam/src/frontend`; `main.js` lists them in load order. esbuild compiles
them into `bundle.js`, which is gitignored.

```bash
make web        # npm ci + one build
npm run watch   # rebuild on every save while the server runs
```

All data goes through `api.jsx`: the HTTP API normally, or the static files of the [demo site](synthetic-mode.md#the-live-demo-on-this-site)
when the page sets `window.K8SFOAM_STATIC`.

`k8sfoams --synthetic` is the quickest way to work on the UI: no cluster, and every view has something to show.

## Docs

This site is built with [MkDocs Material](https://squidfunk.github.io/mkdocs-material/) from `docs/`, into a
`.venv-docs` virtualenv.

```bash
make docs-demo    # build the UI and export the live demo into docs/demo/
make docs-serve   # serve the site with live reload at http://127.0.0.1:8000/k8s-pod-foamtree/
make docs-build   # build it with --strict into site/, as CI does
```

`docs/demo/` is generated and gitignored; run `make docs-demo` again after changing the UI or the synthetic clusters.
The *Docs* workflow builds the site on every pull request that touches the docs, the UI or the synthetic clusters, and
deploys it to GitHub Pages from `main`.

## CI/CD targets

For CI/CD environments (GitHub Actions, etc.), use these targets that work with system Python:

```bash
make restore_ci   # Install dependencies with --system flag
make tests_ci     # Run all tests without uv run prefix
```

## Building the package

```bash
make build
```

## Cleaning build artifacts

```bash
make clean
```
