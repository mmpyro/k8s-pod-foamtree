# HTTP API

Every route reads the cluster; none changes it. `?context=<name>` picks a kubeconfig context (a synthetic cluster with
`--synthetic`); without it the active context is used.

| Route | Returns |
| --- | --- |
| `GET /` | the dashboard |
| `GET /healthcheck` | `{"status": "ok"}` |
| `GET /resources` | what the dashboard fetches on each refresh; optional `?context=<name>`. `{"resources": [...], "nodes": [...]}`: `resources` lists `cpu`, `memory` and every extended resource some node allocates. Each node carries `cpu`, `memory`, `extended`, the [node health](node-health.md) fields, `topology` and its `pods`; each pod carries `cpu`, `memory`, `extended`, `namespace`, `labels`, `qos`, `findings`, `owner` (`{kind, name}` of its controlling workload, with a Deployment's ReplicaSet resolved to the Deployment; `null` for a naked pod) and its `containers` (`name`, `init`, `cpu`, `memory`, `extended`). Same units as below. Nodes and pods are listed once per request |
| `GET /resources/cpu`, `GET /resources/memory` | treemap JSON; optional `?context=<name>`. CPU in millicores, memory in decimal kB. Each node group also carries `unschedulable`, `taints`, `conditions` and a render-ready `warnings` list — see [Node health](node-health.md). Each pod group carries a `findings` list — see [Audit & hygiene](audit.md). Node, pod and container groups carry an `extended` map of every other resource (count, or decimal kB when byte-sized). A synthetic `empty` child per node stands for its free capacity |
| `GET /resources/<extended>` | the same treemap JSON for an extended resource some node allocates, e.g. `/resources/nvidia.com/gpu` or `/resources/ephemeral-storage`. Any other name returns `400`, listing the supported ones |
| `POST /simulate/fit` | per-node fit verdict for a hypothetical pod — see [Scheduling simulators](simulators.md). Body `{"cpu": "4", "memory": "16Gi", "extended": {"nvidia.com/gpu": "1"}, "nodeSelector": {...}, "tolerations": [...]}`; `400` on a malformed spec |
| `GET /simulate/drain/<node>` | where the node's evictable pods would land, and which would go `Pending`; `404` for an unknown node |
| `GET /simulate/consolidation` | which nodes could be removed — see [Scheduling simulators](simulators.md#which-nodes-could-be-removed). `{"total": n, "nodes": [...], "plan": [...], "freed": {...}}`: each node carries `removable` (alone), `blockers` (`[{slug, pods}]` with slug `control-plane`, `pending` or `unmanaged`), `inPlan`, `conflictWith` (the planned nodes removed before it, when it is removable alone but not together), `evictable`, `cpu`/`memory` of the pods a drain would move, `utilisation` and `allocatable`. `plan` lists the removals in order, each with its `placements` and `skipped` pods like a drain; `freed` sums the planned nodes' allocatable (`nodes`, `cpu`, `memory`, `extended`) |
| `GET /events` | Server-Sent Events: `ready` once, then `changed` whenever the context's data changes — see [Live updates](concepts.md#live-updates). `404` under `--no-watch` |
| `GET /contexts` | `[{"context": "...", "active": true}]` |

Errors other than `400` and `404` return `500` with the reason as plain text, e.g. when the API server cannot be
reached.
