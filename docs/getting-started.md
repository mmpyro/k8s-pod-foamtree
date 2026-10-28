# Getting started

## Install

=== "pip"

    ```bash
    pip install k8sfoams
    ```

=== "uv"

    ```bash
    uv pip install k8sfoams
    ```

=== "From source"

    Building the UI from source also needs [Node.js](https://nodejs.org/) 18 or newer, and the
    [uv](https://docs.astral.sh/uv/) package manager:

    ```bash
    curl -LsSf https://astral.sh/uv/install.sh | sh
    git clone https://github.com/mmpyro/k8s-pod-foamtree.git
    cd k8s-pod-foamtree
    make restore_dev   # or `make restore` without the dev dependencies
    ```

    `make run` and `make build` build the UI with `make web`. The PyPI package already contains the built UI.

## Run

```bash
k8sfoams
```

Then open <http://127.0.0.1:8080>. k8sfoams reads `~/.kube/config` (or `$KUBECONFIG`) and lists every context in
the sidebar, the active one first. It only ever reads from the cluster, and switching context in the dashboard never
modifies your kubeconfig.

From a source checkout, `make run` builds the UI and starts the server, and `uv run k8sfoams` runs it from the
project's environment.

## No cluster? Use synthetic mode

```bash
k8sfoams --synthetic
```

This serves three generated clusters instead of your kubeconfig contexts, and changes them every few seconds so the
live view has something to show. It is the same data as the [live demo](demo/index.html), except that the demo is a
fixed snapshot. See [Synthetic mode](synthetic-mode.md).

## Command-line arguments

The most common ones:

```bash
k8sfoams --host 0.0.0.0 --port 8080 -d   # listen on every interface, debug mode
k8sfoams --page-size 200 --no-watch      # smaller LIST pages, no watch cache
k8sfoams --synthetic --seed 7            # a different set of demo clusters
```

The full list is in the [command-line reference](cli.md).
