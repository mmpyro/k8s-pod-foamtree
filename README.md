# k8s-pod-foamtree

**k8sfoams** is a local, read-only dashboard that answers one question: *where is my cluster's requested CPU and memory actually going, and how much room is left on each node?*

It visualizes **resource requests** — what the scheduler reserves — not live usage. That makes it a tool for spotting over-requesting pods and idle headroom, not a performance monitor. It runs on your laptop, reads *~/.kube/config* (or `$KUBECONFIG`) with the standard Kubernetes client, and needs no in-cluster deployment and no metrics-server.

## How it works

1. Lists nodes (`status.capacity`) and all non-terminated pods. Pods in `Succeeded`/`Failed` are excluded — they still report requests via the API but no longer reserve anything.
2. Normalizes CPU to millicores and memory with `bitmath`. A pod's **effective request** is `max(sum(regular containers), max(init containers))` — init containers run sequentially, so they are maxed, not summed. This is what the scheduler actually reserves.
3. Nests the result node → pod → container and adds a synthetic `empty` child per node for free capacity, then serves it as JSON.
4. A React single-page app (no build step — React and Babel come from a CDN) fetches CPU and memory in parallel, merges them, and renders. The view auto-refreshes every 60 seconds by default.

## 2D map

![k8sfoams 2D treemap view](k8s-foam-tree.png)

A squarified treemap. Each node is a square box, each pod is a foam inside it. A pod with more than one container is split into sub-foams. The empty foam is unused (free) capacity on that node. Pick **CPU** or **Memory** with the Resource control.

## 3D cubes

![k8sfoams 3D cube view](k8s-foam-tree-3d.png)

An isometric view: one plate per node, one cube per pod. A cube encodes both resources at once:

- **width × depth** (footprint) → CPU request
- **height** → memory request
- **color** → node

Both dimensions are square-root scaled, so a 10× larger pod is not 10× wider. Because a cube already shows both resources, the CPU/Memory picker is disabled in 3D and a **Zoom** slider takes its place.

Switch views with the sidebar *View* control or the `2D`/`3D` pill in the header. It is client-side state — no flag, no restart. The scene is pure CSS 3D, not WebGL, so it needs no GPU support.

## Controls

- **Memory unit**: MiB, GiB (default), or TiB.
- **Context**: the sidebar lists every context from your kubeconfig, active one first, tagged by provider. **Switching only changes the context inside the k8sfoams web server — your ~/.kube/config file is never modified.**
- **Refresh**: slider from 5 to 600 seconds, plus a *Refresh now* button.
- **Filter**: the header query bar highlights matching pods and dims the rest — nothing is removed from the view. Whitespace-separated tokens are ANDed: `ns:kube-system`, `node:worker-*` (glob), `qos:BestEffort`, `has:init-containers`, `app=frontend`, `env!=prod`, and bare text as a pod-name substring. Quote values containing spaces (`app="my app"`). The bar shows a live match count and flags malformed tokens.
- **Focus**: click a node to open an overlay listing its pods with per-pod CPU/memory and container breakdown.

## HTTP API

| Route | Returns |
| --- | --- |
| `GET /` | the dashboard |
| `GET /healthcheck` | `{"status": "ok"}` |
| `GET /resources/cpu`, `GET /resources/memory` | treemap JSON; optional `?context=<name>`. CPU in millicores, memory in decimal kB |
| `GET /contexts` | `[{"context": "...", "active": true}]` |

## Installation

### Prerequisites
Install [uv](https://docs.astral.sh/uv/) package manager:
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### Install from source
```bash
# Install dependencies and the package in development mode
make restore_dev

# Or install without dev dependencies
make restore
```

### Install via PyPi
```bash
pip install k8sfoams
# or with uv
uv pip install k8sfoams
```

## Run k8s-pod-foamtree
After installation, run the application:
```bash
# Using make
make run

# Or directly
k8sfoams

# Or with uv run
uv run k8sfoams
```

## Command lines arguments
- host: host IP address on which server listen, default is **127.0.0.1**
- port: port number on which server listen, default is **8080**
- d: turn on **debug** mode when server starts

Example:
```bash
k8sfoams --host 0.0.0.0 --port 8080 -d
```

## Development

### Running tests
```bash
# Run all tests (type checking, linting, security, unit tests)
make tests

# Run individual test suites
make unit_tests
make static_code_analysis
make check_types
make bandit
```

### CI/CD targets
For CI/CD environments (GitHub Actions, etc.), use these targets that work with system Python:
```bash
make restore_ci   # Install dependencies with --system flag
make tests_ci     # Run all tests without uv run prefix
```

### Building the package
```bash
make build
```

### Clean build artifacts
```bash
make clean
```