# Synthetic mode

```bash
k8sfoams --synthetic
```

Synthetic mode replaces your kubeconfig contexts with generated clusters. You can try k8sfoams, demo it, or work on it
without a Kubernetes cluster. k8sfoams reads no kubeconfig and contacts no API server.

k8sfoams does not pour mock data straight into the UI. It builds real `V1Node` and `V1Pod` objects (the models the
Kubernetes Python client returns). Then it sends them through the same code as a live cluster: the extractor that
computes effective requests, the mapper, the node health and audit rules, and the scheduling simulators. So you see
what k8sfoams would show for a real cluster of that shape.

## The clusters

Each cluster is one context in the sidebar, named the way EKS and GKE name theirs.

| Context | Shape | What to look at |
| --- | --- | --- |
| `demo-shop` (EKS, eu-west-1) | 16 nodes in four managed node groups over three zones, about 170 pods | a cordoned node, a node under memory pressure, tainted system and analytics nodes, every [audit rule](audit.md), a service mesh with native sidecars, all three QoS classes, two [pending pods](simulators.md#do-the-pending-pods-fit) and a scheduling-gated one |
| `gke_demo-ml_us-central1_ml-training` (GKE) | 12 nodes: CPU, highmem with HugePages, A100 and spot L4 GPU pools | [GPUs](extended-resources.md) and their fragmentation, a node with stranded GPUs, a GPU notebook nobody owns, inference running only on spot |
| `demo-platform` (EKS with Karpenter, us-east-1) | 102 nodes, about 1,500 pods | the [3D view](cubes-3d.md) at scale, NotReady and pressured nodes, spot vs on-demand [topology](topology.md) |

`demo-shop` is the active context. The exact counts depend on the seed.

## Live changes

Every 10 seconds, each cluster makes one small change:

- A Deployment scales up or down by one replica. It stays within two replicas of its declared size.
- Now and then, a node stops reporting `Ready` instead. It comes back on the next step.

Each change reaches the browser through the same [live update stream](concepts.md#live-updates) as a watched cluster.
The header's *live* chip and the refresh behaviour therefore work as they do against a real cluster.

```bash
k8sfoams --synthetic --churn-interval 2   # change every 2 seconds
k8sfoams --synthetic --churn-interval 0   # keep the clusters still
```

## Seeds

k8sfoams generates the clusters from a seed, 42 by default. The same seed always builds the same clusters, down to node and
pod names, so a link to a view stays valid. Another seed gives other node names, placements and service sizes.

```bash
k8sfoams --synthetic --seed 7
```

## The live demo on this site

The [live demo](demo/index.html) and the dashboards embedded in these pages use the regular k8sfoams frontend. There,
the frontend reads files instead of the HTTP API. Unlike `k8sfoams --synthetic`, each cluster there is a fixed
snapshot.

The files hold what each API endpoint returns for the synthetic clusters. This command writes them:

```bash
python -m k8sfoam.src.synthetic.export docs/demo   # what `make docs-demo` runs
```

It writes the dashboard (`index.html`, `bundle.js`, `styles.css`) and a `data/` directory:

| File | Answers |
| --- | --- |
| `data/contexts.json` | `GET /contexts`, plus the directory of each context |
| `data/<context>/resources.json` | `GET /resources?context=<context>` |
| `data/<context>/drain/<node>.json` | `GET /simulate/drain/<node>`, for every node |
| `data/<context>/fit/<preset>.json` | `POST /simulate/fit` for each preset pod |
| `data/<context>/pending.json` | `POST /simulate/pending` without hypothetical nodes |
| `data/fit-presets.json` | the preset pods offered by the fit simulator |

The export precomputes every drain and the pending pods simulation. The fit simulator accepts any pod, so the export
cannot compute all of its answers ahead of time. In the demo, the fit simulator offers a few presets: a small service,
a big batch job, an in-memory cache, a GPU inference pod, a scratch-heavy worker and a node agent that tolerates every
taint. The same limit applies to [hypothetical nodes](simulators.md#what-if-i-add-nodes), which the demo leaves out.
Run `k8sfoams --synthetic` to try any other pod or node shape.

The `index.html` from the export sets `window.K8SFOAM_STATIC`. This switches the bundle from the HTTP API to these
files. Add `?embed=1` to its URL to start with the sidebar collapsed and a button that opens the full-page demo. The
view itself comes from the [URL hash](shareable-links.md), as it does in the regular dashboard.
