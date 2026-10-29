# HTTP API

Every route reads the cluster; none changes it. `?context=<name>` picks a kubeconfig context (a synthetic cluster with
`--synthetic`); without it the active context is used.

| Route | Returns |
| --- | --- |
| `GET /` | the dashboard |
| `GET /healthcheck` | `{"status": "ok"}` |
| `GET /resources` | what the dashboard fetches on each refresh; optional `?context=<name>`. `{"resources": [...], "nodes": [...]}`: `resources` lists `cpu`, `memory` and every extended resource some node allocates. Each node carries `cpu`, `memory`, `extended`, the [node health](node-health.md) fields, `topology` and its `pods`; each pod carries `cpu`, `memory`, `extended`, `namespace`, `labels`, `qos`, `findings`, `owner` (`{kind, name}` of its controlling workload, with a Deployment's ReplicaSet resolved to the Deployment; `null` for a naked pod) and its `containers` (`name`, `init`, `cpu`, `memory`, `extended`). `pendingPods` counts the pods not bound to a node and not held by `schedulingGates`. Same units as below. Nodes and pods are listed once per request |
| `GET /resources/cpu`, `GET /resources/memory` | treemap JSON; optional `?context=<name>`. CPU in millicores, memory in decimal kB. Each node group also carries `unschedulable`, `taints`, `conditions` and a render-ready `warnings` list — see [Node health](node-health.md). Each pod group carries a `findings` list — see [Audit & hygiene](audit.md). Node, pod and container groups carry an `extended` map of every other resource (count, or decimal kB when byte-sized). A synthetic `empty` child per node stands for its free capacity |
| `GET /resources/<extended>` | the same treemap JSON for an extended resource some node allocates, e.g. `/resources/nvidia.com/gpu` or `/resources/ephemeral-storage`. Any other name returns `400`, listing the supported ones |
| `POST /simulate/fit` | per-node fit verdict for a hypothetical pod — see [Scheduling simulators](simulators.md). Body `{"cpu": "4", "memory": "16Gi", "extended": {"nvidia.com/gpu": "1"}, "nodeSelector": {...}, "nodeAffinity": [{"matchExpressions": [{"key": "zone", "operator": "In", "values": ["a", "b"]}]}], "tolerations": [...]}`. `nodeAffinity` is the required `nodeSelectorTerms` (ORed; `matchExpressions` only, ANDed; operators `In`, `NotIn`, `Exists`, `DoesNotExist`, `Gt`, `Lt`). An optional `extraNodes` adds [hypothetical nodes](#hypothetical-nodes); each node verdict then carries `hypothetical`. `400` on a malformed spec |
| `GET /simulate/drain/<node>`, `POST /simulate/drain/<node>` | where the node's evictable pods would land, and which would go `Pending`. `blocked` lists the pods a PodDisruptionBudget keeps on the node (`[{pod, namespace, pdb, disruptionsAllowed}]`); `pdbsChecked` is `false` when the server may not list PDBs. `POST` takes `{"extraNodes": [...]}` ([hypothetical nodes](#hypothetical-nodes)); each placement carries `hypothetical`. `404` for an unknown node |
| `POST /simulate/pending` | where the pods waiting for a node would go — see [Scheduling simulators](simulators.md#do-the-pending-pods-fit). Optional body `{"extraNodes": [...]}`. `{"fits": bool, "total": n, "nodes": n, "placements": [{pod, namespace, cpu, memory, to, hypothetical}], "pending": [{pod, namespace, cpu, memory, reasons}], "gated": [{pod, namespace, gates}], "hypotheticalNodes": [...]}`: `total` counts the simulated pods, `nodes` the nodes they were tried on, `reasons` is the scheduler-style tally (`[{slug, nodes}]`), and `gated` lists the pods held by `schedulingGates`, which are not simulated |
| `GET /simulate/consolidation` | which nodes could be removed — see [Scheduling simulators](simulators.md#which-nodes-could-be-removed). `{"total": n, "nodes": [...], "plan": [...], "freed": {...}, "pdbsChecked": bool}`: each node carries `removable` (alone), `blockers` (`[{slug, pods}]` with slug `control-plane`, `pending`, `pdb` or `unmanaged`), `inPlan`, `conflictWith` (the planned nodes removed before it, when it is removable alone but not together), `evictable`, `cpu`/`memory` of the pods a drain would move, `utilisation` and `allocatable`. `plan` lists the removals in order, each with its `placements` and `skipped` pods like a drain; `freed` sums the planned nodes' allocatable (`nodes`, `cpu`, `memory`, `extended`) |
| `GET /simulate/replicas/<namespace>/<kind>/<name>?count=N` | where `N` (1 to 100) more copies of a workload's pod would land — see [Scheduling simulators](simulators.md#can-i-scale-this-workload). `<kind>` and `<name>` are the pod `owner` from `/resources`. `{"workload": {...}, "template": {...}, "requested": N, "placed": k, "fits": bool, "total": n, "placements": [...], "nodes": [...], "pending": [...]}`: `template` is the copied pod (`pod`, `cpu`, `memory`, `extended`, `nodeSelector`, `tolerations`, `nodeAffinity`, `podAffinity`, `podAntiAffinity`, `topologySpread`), `placements` the node of each placed copy in order, each node carries how many copies it took (`replicas`) and why it could not take one more (`reasons`), and `pending` is the scheduler-style tally (`[{slug, nodes}]`) for the first copy that fits nowhere. `400` for a bad `count` or a DaemonSet / static pod (`Node`) workload, `404` for an unknown workload |
| `GET /events` | Server-Sent Events: `ready` once, then `changed` whenever the context's data changes — see [Live updates](concepts.md#live-updates). `404` under `--no-watch` |
| `GET /contexts` | `[{"context": "...", "active": true}]` |

Errors other than `400` and `404` return `500` with the reason as plain text, e.g. when the API server cannot be
reached.

## Hypothetical nodes

`extraNodes` lists nodes to add for one simulation, at most 50 in all:

```json
{"extraNodes": [
  {"template": "ip-10-0-22-132.eu-west-1.compute.internal", "count": 3},
  {"shape": {"cpu": "16", "memory": "64Gi", "extended": {"nvidia.com/gpu": "4"}, "pods": 58,
             "labels": {"node.kubernetes.io/instance-type": "g5.4xlarge"},
             "taints": [{"key": "nvidia.com/gpu", "value": "present", "effect": "NoSchedule"}]},
   "count": 1}
]}
```

A `template` copies an existing node's allocatable, labels (with a fresh `kubernetes.io/hostname`) and taints, minus
the taints a new node would not carry yet. A `shape` needs `cpu` and `memory`; `pods` defaults to 110. Taint effects are
`NoSchedule`, `PreferNoSchedule` or `NoExecute`. `count` defaults to 1. The nodes are named
`hypothetical-<instance-type>-<n>`, or `hypothetical-custom-<n>` without an instance-type label, and run the DaemonSet
pods a node of their kind would.

The result gains `hypotheticalNodes`: `[{name, template, cpu, memory, pods, extended, labels, taints, topology,
daemonPods}]`, with `template` `null` for a shape and `daemonPods` the DaemonSet pods bound to it (`[{pod, namespace,
cpu, memory, extended}]`). An unknown template or a malformed entry returns `400`.
