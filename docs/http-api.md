# HTTP API

Every route reads the cluster; none changes it. `?context=<name>` picks a kubeconfig context (a synthetic cluster with
`--synthetic`); without it the active context is used.

| Route | Returns |
| --- | --- |
| `GET /` | the dashboard |
| `GET /healthcheck` | `{"status": "ok"}` |
| `GET /resources` | what the dashboard fetches on each refresh; optional `?context=<name>`. `{"resources": [...], "nodes": [...]}`: `resources` lists `cpu`, `memory` and every extended resource some node allocates. Each node carries `cpu`, `memory`, `extended`, the [node health](node-health.md) fields, `topology`, `price` (`{hourly, source}` in USD, `source` is `live` or `snapshot`; `null` when unknown — see [Cost](cost.md)) and its `pods`; each pod carries `cpu`, `memory`, `extended`, `namespace`, `labels`, `qos`, `findings`, `owner` (`{kind, name}` of its controlling workload, with a Deployment's ReplicaSet resolved to the Deployment; `null` for a naked pod) and its `containers` (`name`, `init`, `cpu`, `memory`, `extended`). `pendingPods` counts the pods not bound to a node and not held by `schedulingGates`. `pricing` is `{generated, liveErrors, missing}`: the snapshot date, the providers whose live fetch failed with why, and `[{provider, region, instanceType, count}]` of cloud nodes without a price. Same units as below. Nodes and pods are listed once per request |
| `GET /resources/cpu`, `GET /resources/memory` | treemap JSON; optional `?context=<name>`. CPU in millicores, memory in decimal kB. Each node group also carries `unschedulable`, `taints`, `conditions` and a render-ready `warnings` list — see [Node health](node-health.md). Each pod group carries a `findings` list — see [Audit & hygiene](audit.md). Node, pod and container groups carry an `extended` map of every other resource (count, or decimal kB when byte-sized). A synthetic `empty` child per node stands for its free capacity |
| `GET /resources/<extended>` | the same treemap JSON for an extended resource some node allocates, e.g. `/resources/nvidia.com/gpu` or `/resources/ephemeral-storage`. Any other name returns `400`, listing the supported ones |
| `POST /simulate/fit` | per-node fit verdict for a hypothetical pod — see [Scheduling simulators](simulators.md). Body `{"cpu": "4", "memory": "16Gi", "extended": {"nvidia.com/gpu": "1"}, "nodeSelector": {...}, "nodeAffinity": [{"matchExpressions": [{"key": "zone", "operator": "In", "values": ["a", "b"]}]}], "tolerations": [...]}`. `nodeAffinity` is the required `nodeSelectorTerms` (ORed; `matchExpressions` only, ANDed; operators `In`, `NotIn`, `Exists`, `DoesNotExist`, `Gt`, `Lt`). An optional `extraNodes` adds [hypothetical nodes](#hypothetical-nodes); each node verdict then carries `hypothetical`. `400` on a malformed spec |
| `GET /simulate/drain/<node>`, `POST /simulate/drain/<node>` | where the node's evictable pods would land, and which would go `Pending`. `blocked` lists the pods a PodDisruptionBudget keeps on the node (`[{pod, namespace, pdb, disruptionsAllowed}]`); `pdbsChecked` is `false` when the server may not list PDBs. `POST` takes `{"extraNodes": [...]}` ([hypothetical nodes](#hypothetical-nodes)); each placement carries `hypothetical`. `404` for an unknown node |
| `POST /simulate/pending` | where the pods waiting for a node would go — see [Scheduling simulators](simulators.md#do-the-pending-pods-fit). Optional body `{"extraNodes": [...]}`. `{"fits": bool, "total": n, "nodes": n, "placements": [{pod, namespace, cpu, memory, to, hypothetical}], "pending": [{pod, namespace, cpu, memory, reasons}], "gated": [{pod, namespace, gates}], "hypotheticalNodes": [...]}`: `total` counts the simulated pods, `nodes` the nodes they were tried on, `reasons` is the scheduler-style tally (`[{slug, nodes}]`), and `gated` lists the pods held by `schedulingGates`, which are not simulated |
| `GET /simulate/consolidation` | which nodes could be removed — see [Scheduling simulators](simulators.md#which-nodes-could-be-removed). `{"total": n, "nodes": [...], "plan": [...], "freed": {...}, "pdbsChecked": bool}`: each node carries `removable` (alone), `blockers` (`[{slug, pods}]` with slug `control-plane`, `pending`, `pdb` or `unmanaged`), `inPlan`, `conflictWith` (the planned nodes removed before it, when it is removable alone but not together), `evictable`, `cpu`/`memory` of the pods a drain would move, `utilisation` and `allocatable`. `plan` lists the removals in order, each with its `placements` and `skipped` pods like a drain; `freed` sums the planned nodes' allocatable (`nodes`, `cpu`, `memory`, `extended`) |
| `GET /simulate/replicas/<namespace>/<kind>/<name>?count=N` | where `N` (1 to 100) more copies of a workload's pod would land — see [Scheduling simulators](simulators.md#can-i-scale-this-workload). `<kind>` and `<name>` are the pod `owner` from `/resources`. `{"workload": {...}, "template": {...}, "requested": N, "placed": k, "fits": bool, "total": n, "placements": [...], "nodes": [...], "pending": [...]}`: `template` is the copied pod (`pod`, `cpu`, `memory`, `extended`, `nodeSelector`, `tolerations`, `nodeAffinity`, `podAffinity`, `podAntiAffinity`, `topologySpread`), `placements` the node of each placed copy in order, each node carries how many copies it took (`replicas`) and why it could not take one more (`reasons`), and `pending` is the scheduler-style tally (`[{slug, nodes}]`) for the first copy that fits nowhere. `400` for a bad `count` or a DaemonSet / static pod (`Node`) workload, `404` for an unknown workload |
| `GET /events` | Server-Sent Events: `ready` once, then `changed` whenever the context's data changes — see [Live updates](concepts.md#live-updates). `404` under `--no-watch` |
| `GET /contexts` | `[{"context": "...", "active": true}]` |
| `GET /advisor/status` | `{"enabled": bool, "model": "..."}` — whether the [AI advisor](advisor.md) is configured. The API key is never returned |
| `GET /advisor/preview` | the exact chat messages `POST /advisor` would send for the context: `{"model": "...", "messages": [{role, content}]}`; optional `?context=<name>&redact=false` (names are redacted unless `redact=false`). `404` when the advisor is off |
| `POST /advisor` | asks the model; optional `?context=<name>`, JSON body `{"redact": true}` (`Content-Type: application/json`). `{"model": "...", "cached": bool, "recommendations": [{title, severity, category, rationale, affected: [{kind, namespace, name}], suggestedChange}]}`, most severe first, with real names restored. `severity` is `high`, `medium` or `low`; `category` is `requests/limits`, `resilience`, `scheduling`, `node health` or `cost`. `cached` is `true` when the last answer for an identical summary was reused. `400` on a bad body, `415` when the body is not JSON, `404` when the advisor is off, `401` / `429` / `504` / `502` for an invalid key, a rate limit, a timeout, or an unreachable provider or malformed model output |
| `POST /assistant` | answers a question with the same model; optional `?context=<name>`, body `{"question": "...", "history": [{"role": "user" or "assistant", "content": "..."}], "redact": true}` (question up to 2000 characters, at most 20 history turns). `{"model": "...", "answer": "...", "sources": ["Filtering › Token reference", ...]}`: the answer is markdown with real names restored, `sources` the docs sections that were sent. `400` on a bad body, `404` when the advisor is off, and the same provider errors as `POST /advisor` — see [Ask a question](advisor.md#ask-a-question) |

Errors other than `400` and `404` return `500` with the reason as plain text, e.g. when the API server cannot be
reached.

## Authentication

When k8sfoams runs with a token (any non-loopback `--host`, or `--token`; see
[Network access](cli.md#network-access)), every route except `/`, the static files and `/healthcheck` needs
`Authorization: Bearer <token>`, and answers `401` without it. `GET /events` also takes `?token=<token>`, because a
browser `EventSource` cannot set headers; no other route does.

Regardless of the token, a `Host` header the server was not told about returns `403`, and so does a `POST` whose `Origin`
is another site. `/healthcheck` answers any `Host`, so probes can reach it by IP.

```bash
curl -H "Authorization: Bearer $TOKEN" -H "Host: dash.internal:8080" http://10.0.0.5:8080/contexts
```

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
