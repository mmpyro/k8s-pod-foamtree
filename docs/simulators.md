# Scheduling simulators

Dry runs of the kube-scheduler's filter phase, against each node's free **allocatable** capacity (allocatable minus
the requests of the pods already bound there). None of them touches the cluster.

<figure class="k8sfoam-embed k8sfoam-embed-tall">
  <iframe src="../demo/index.html?embed=1#ctx=gke_demo-ml_us-central1_ml-training&metric=nvidia.com%2Fgpu" loading="lazy" title="k8sfoams demo of the GPU cluster for the scheduling simulators"></iframe>
  <figcaption>
    The GKE GPU cluster. Click the dashed + in the header and pick the <em>GPU inference</em> preset, or click a node and
    <em>Simulate drain</em>.
    <a href="../demo/index.html#ctx=gke_demo-ml_us-central1_ml-training&metric=nvidia.com%2Fgpu" target="_blank">Open full screen</a>
  </figcaption>
</figure>

## Can I fit this pod?

The dashed **+** button in the header. Enter a CPU and memory request, optionally extended resources
(`nvidia.com/gpu=1, ephemeral-storage=10Gi`), a `nodeSelector` (`zone=a, disk=ssd`), a required node affinity term
and tolerations (`dedicated=gpu:NoSchedule`, `spot` for any value and effect, `*` for everything). Every node gets a
verdict; the ones that cannot take the pod say why, e.g. `Insufficient CPU: requires 4000m, available 1200m`.

The node affinity field holds one `nodeSelectorTerm`, its expressions ANDed: `zone in (a,b)`, `disk notin (hdd)`,
`gpu` or `gpu exists`, `!spot` or `spot doesnotexist`, `cores gt 4`, `cores lt 64`. A node that fails the term
reads `Node affinity mismatch: requires zone In (a,b), spot DoesNotExist`. Several ORed terms can be sent through the
[HTTP API](http-api.md).

!!! note "In the demo"
    The demo answers from precomputed files, so it offers six preset pods instead of free input. On the GPU cluster,
    try *GPU inference*: two A100 nodes can take it, while a third has two free GPUs but too little CPU left to run
    the pod (its GPUs are stranded). Run `k8sfoams --synthetic` to try any pod.

## Simulate drain

Open a node and click **Simulate drain**. Its evictable pods are bin-packed onto the other nodes, biggest first, each
onto the passing node with the most headroom left. The report lists where each pod lands and which would go `Pending`,
with a scheduler-style tally (`0/4 nodes available: 3 insufficient CPU, 1 cordoned`). DaemonSet and static pods stay on
the node and are listed separately. Naked pods (no controller) are flagged, because a real drain deletes them for good.

Every node of every demo cluster has its drain precomputed. On demo-shop, most drains fit, but two do not:

- the node running both `search-indexer` replicas: they are pinned to it with a `kubernetes.io/hostname`
  `nodeSelector`, so both go `Pending`;
- the analytics node: the `warehouse` pod may only run in the analytics node group, which has no other node, and
  no other node has the 52Gi of memory it requests free anyway.

[Try it on demo-shop](demo/index.html#q=search-indexer){ .k8s-try target=_blank } (click the highlighted node, then
*Simulate drain*).

## Which nodes could be removed?

The two-node button in the header, next to the dashed **+**. It answers the question cluster-autoscaler and Karpenter
ask before scaling down — and shows why they do not when you think they should.

It works in two passes:

1. **Alone.** Every node gets a drain simulation against the cluster as it is. A node **cannot be removed** if any of
   its pods would go `Pending`, if it runs a naked pod (cluster-autoscaler refuses to delete a pod no controller would
   recreate), or if it is labelled as a control-plane node.
2. **Together.** Per-node verdicts cannot simply be added up: two nodes may each fit on the rest of the cluster, but
   not both at once. So the removable nodes are drained again one after another, least utilised first like
   cluster-autoscaler, each against the cluster the previous steps left behind — the pods they moved now count
   against the nodes that took them. Nodes that still fit form the **removal plan**; the others are listed as
   **removable alone, not together**.

The summary adds up what the plan frees, e.g. *7 nodes of 16 can go, freeing 51.4 CPU / 188 GiB* (GPUs and other
device counts are included). Each plan step expands to the pods it moves and where they land. On the map, planned
nodes are grey, the nodes taking their pods green and the blocked ones red.

On demo-shop, seven nodes can go. The `search-indexer` and analytics nodes from the drain example above are blocked,
and so is a node running a naked pod.

[Try it on demo-shop](demo/index.html){ .k8s-try target=_blank } (click the two-node button in the header).

## Can I scale this workload?

Instead of typing a pod shape into *Can I fit this pod?*, start from a real one. Click a pod to pin its workload, then
click **+N** on the workload chip in the header. The workload's pod is the template: its effective requests, extended
resources, `nodeSelector`, node and pod affinity, and tolerations, shown at the top of the result. Copies are then placed one after another,
each on the passing node with the most headroom left, against the room the previous copies left behind.

The simulation first asks whether the workload could double, so the answer is two clicks away. Type any count up to
100 and **Simulate** again. The headline says how far the workload can grow, e.g. *Only 87 of 100 more replicas fit —
checkout-api can grow from 3 to 90, and replica 91 would stay Pending*. **Where it stops** gives the scheduler-style
tally for that replica (`0/16 nodes available: 1 cordoned, 3 untolerated taint, 12 insufficient CPU`), and **Where they
land** counts the copies per node. On the map, nodes taking copies are green; when not every copy fits, the rest are
red.

DaemonSets and static pods have no replica count, so their chip has no **+N**. Neither do naked pods, which have no
workload to scale.

On demo-shop, `checkout-api` doubles easily. `search-indexer` is pinned to one node by a `kubernetes.io/hostname`
`nodeSelector`, so it only takes four more copies before every node turns it away.

[Try it on demo-shop](demo/index.html#wl=checkout%2FDeployment%2Fcheckout-api){ .k8s-try target=_blank } (click
**+N** on the `checkout-api` chip).

## Reading the result

While a result is shown, nodes are outlined in both views — green fits / receives pods or copies, red cannot, grey is the drained
(or planned-for-removal) node — and a strip above the map summarises it. `Esc` or its `×` clears it.

Predicates checked, in the scheduler's order: cordon, `Ready`, `nodeSelector`, required node affinity,
`NoSchedule`/`NoExecute` taints vs. tolerations, pod count, CPU, memory, every extended resource the pod requests
against the node's free allocatable (`Insufficient nvidia.com/gpu: requires 1, available 0`; a node that does not
advertise a resource has none), then required pod affinity and anti-affinity. A pod that tolerates
`node.kubernetes.io/unschedulable` or `not-ready` (every DaemonSet pod does) still fits a cordoned or NotReady node.

Node affinity is the pod's `requiredDuringSchedulingIgnoredDuringExecution` `nodeSelectorTerms`: the terms are ORed,
the expressions inside a term ANDed, with the operators `In`, `NotIn`, `Exists`, `DoesNotExist`, `Gt` and `Lt`, and
`matchFields` on `metadata.name`. Drains, the consolidation finder and replica copies all honour it. Preferred terms
only score nodes, so they are ignored.

Pod affinity and anti-affinity are the required `podAffinity` / `podAntiAffinity` terms. A term selects pods by
`labelSelector` in the pod's own namespace, in the listed `namespaces`, or in every namespace with an empty
`namespaceSelector: {}`, and compares the nodes by `topologyKey`. Both directions are checked, like kube-scheduler:

- **affinity:** every term needs a selected pod in the node's domain (same zone, same host, ...), e.g. `Pod affinity: no
  pod matching app=cache in the same topology.kubernetes.io/zone`. A node without the topology label is rejected. The
  first pod of a group with affinity to itself may go anywhere, or it would stay Pending forever.
- **anti-affinity:** no selected pod may run in the node's domain (`Pod anti-affinity: shop/web-1 matches app=web in
  the same kubernetes.io/hostname`), and no pod already running there may have an anti-affinity term selecting the
  incoming pod (`Pod anti-affinity: shop/db-0 repels it in the same kubernetes.io/hostname`).

Drains and replica copies see the pods they have already placed, so a workload with hostname anti-affinity grows by
at most one copy per node, and two evicted replicas never land on the same node.

**Not simulated:** topology spread, PodDisruptionBudgets, a non-empty `namespaceSelector` on pod affinity terms (it
needs namespace labels, so only the listed namespaces, or the pod's own, count) and volume zone binding — so a green
verdict is necessary, not sufficient.
