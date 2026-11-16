# Scheduling simulators

The simulators are dry runs of the kube-scheduler's filter phase. They check each node's free **allocatable** capacity
(allocatable minus the requests of the pods already bound there). No simulator touches the cluster. Some can add
[nodes it does not have yet](#what-if-i-add-nodes).

<figure class="k8sfoam-embed k8sfoam-embed-tall">
  <iframe src="../demo/index.html?embed=1#ctx=gke_demo-ml_us-central1_ml-training&metric=nvidia.com%2Fgpu" loading="lazy" title="k8sfoams demo of the GPU cluster for the scheduling simulators"></iframe>
  <figcaption>
    The GKE GPU cluster. Click the dashed + in the header and pick the <em>GPU inference</em> preset, or click a node and
    <em>Simulate drain</em>.
    <a href="../demo/index.html#ctx=gke_demo-ml_us-central1_ml-training&metric=nvidia.com%2Fgpu" target="_blank">Open full screen</a>
  </figcaption>
</figure>

## Can I fit this pod?

Click the dashed **+** button in the header. Enter a CPU and memory request. You can also enter:

- extended resources (`nvidia.com/gpu=1, ephemeral-storage=10Gi`)
- a `nodeSelector` (`zone=a, disk=ssd`)
- a required node affinity term
- tolerations (`dedicated=gpu:NoSchedule`, `spot` for any value and effect, `*` for everything)

Every node gets a verdict. A node that cannot take the pod says why, e.g.
`Insufficient CPU: requires 4000m, available 1200m`.

The node affinity field holds one `nodeSelectorTerm`, and its expressions are ANDed: `zone in (a,b)`, `disk notin (hdd)`,
`gpu` or `gpu exists`, `!spot` or `spot doesnotexist`, `cores gt 4`, `cores lt 64`. A node that fails the term
reads `Node affinity mismatch: requires zone In (a,b), spot DoesNotExist`. To send several ORed terms, use the
[HTTP API](http-api.md).

!!! note "In the demo"
    The demo answers from precomputed files, so it offers six preset pods instead of free input. On the GPU cluster,
    try *GPU inference*. Two A100 nodes can take it. A third node has two free GPUs but too little CPU left to run
    the pod, so its GPUs are stranded. Run `k8sfoams --synthetic` to try any pod.

## Simulate drain

Open a node and click **Simulate drain**. The simulator bin-packs the node's evictable pods onto the other nodes,
biggest first. Each pod goes onto the passing node with the most headroom left. The report lists where each pod lands
and which pods would go `Pending`. It also gives a scheduler-style tally
(`0/4 nodes available: 3 insufficient CPU, 1 cordoned`). DaemonSet and static pods stay on the node, and the report
lists them separately. The report flags naked pods (no controller), because a real drain deletes them for good.

The simulator honours PodDisruptionBudgets the way `kubectl drain` meets them. It evicts pods in the same biggest-first
order. Each eviction uses up one of `status.disruptionsAllowed` of every budget in the pod's namespace whose selector
matches the pod. Several evictions from one drain draw on the same budget. Once a budget is used up, the report lists
its other pods under **Blocked by PDB**, and they stay on the node. `kubectl drain` would keep retrying them.

A pod that more than one budget selects is blocked too, whatever the budgets allow, because the eviction API refuses it
outright. If RBAC does not let the server list PDBs (a `403`), the report says *PodDisruptionBudgets not checked*
instead of failing.

Every node of every demo cluster has its drain precomputed. On demo-shop, most drains fit, but two do not:

- the node that runs both `search-indexer` replicas. A `kubernetes.io/hostname` `nodeSelector` pins them to this
  node, so both go `Pending`.
- the analytics node. The `warehouse` pod can only run in the analytics node group, and that group has no other node.
  Also, no other node has the 52Gi of free memory that the pod requests.

Three more drains fit but are blocked. `catalog-db` has a PodDisruptionBudget with `minAvailable: 3` over its three
replicas, so no node that runs one of them can be drained.

[Try it on demo-shop](demo/index.html#q=search-indexer){ .k8s-try target=_blank } (click the highlighted node, then
*Simulate drain*).

## Which nodes could be removed?

Click the two-node button in the header, next to the dashed **+**. It answers the question that cluster-autoscaler
and Karpenter ask before they scale down. It also shows why they do not scale down when you think they should.

It works in two passes:

1. **Alone.** Every node gets a drain simulation against the cluster as it is. A node **cannot be removed** if any of
   its pods would go `Pending` or if a PodDisruptionBudget holds one of its pods. It also cannot be removed if it runs a
   naked pod (cluster-autoscaler refuses to delete a pod no controller would recreate), or if it is labelled as a
   control-plane node.
2. **Together.** You cannot simply add up the per-node verdicts. Two nodes may each fit on the rest of the cluster,
   but not both at once. So the simulator drains the removable nodes again, one after another, least utilised first
   like cluster-autoscaler. Each drain runs against the cluster that the previous steps left behind, so the pods they
   moved now count against the nodes that took them. The nodes that still fit form the **removal plan**. The
   simulator lists the others as **removable alone, not together**.

The summary adds up what the plan frees, e.g. *7 nodes of 16 can go, freeing 51.4 CPU / 188 GiB*. The sum includes
GPUs and other device counts. Each plan step expands to the pods it moves and where they land. On the map, planned
nodes are grey, the nodes that take their pods are green, and the blocked nodes are red.

On demo-shop, seven nodes can go. The `search-indexer` and analytics nodes from the drain example above are blocked,
and so are the three `catalog-db` nodes held by its PodDisruptionBudget (one of them also runs a naked pod).

[Try it on demo-shop](demo/index.html){ .k8s-try target=_blank } (click the two-node button in the header).

## Can I scale this workload?

This simulator starts from a real pod, so you do not type a pod shape into *Can I fit this pod?*. Click a pod to pin
its workload. Then click **+N** on the workload chip in the header. The workload's pod is the template. The template
holds the pod's effective requests, extended resources, `nodeSelector`, node and pod affinity, topology spread and
tolerations, and the result shows them at the top. The simulator then places copies one after another. Each copy goes
on the passing node with the most headroom left, against the room that the previous copies left behind.

The simulation first checks if the workload could double, so the answer is two clicks away. To try another count,
type any count up to 100 and click **Simulate** again. The headline says how far the workload can grow, e.g.
*Only 87 of 100 more replicas fit —
checkout-api can grow from 3 to 90, and replica 91 would stay Pending*. **Where it stops** gives the scheduler-style
tally for that replica (`0/16 nodes available: 1 cordoned, 3 untolerated taint, 12 insufficient CPU`). **Where they
land** counts the copies per node. On the map, the nodes that take copies are green. When not every copy fits, the
rest are red.

DaemonSets and static pods have no replica count, so their chip has no **+N**. Neither do naked pods, which have no
workload to scale.

On demo-shop, `checkout-api` doubles easily. A `kubernetes.io/hostname` `nodeSelector` pins `search-indexer` to one
node, so it takes only four more copies before every node turns it away.

[Try it on demo-shop](demo/index.html#wl=checkout%2FDeployment%2Fcheckout-api){ .k8s-try target=_blank } (click
**+N** on the `checkout-api` chip).

## Do the pending pods fit?

If the cluster has pods that wait for a node, the header shows a chip such as **2 pending · do they fit?**. Click it.
The simulator places every pending pod the way a drain places evicted pods. The biggest pod goes first. Each pod goes
on the passing node with the most headroom left, against the room the previous pods took. The result lists the pods that **would be
placed** and where they go. It also lists the pods that are **still Pending**, with their scheduler-style tally
(`0/16 nodes available: 1 cordoned, 3
untolerated taint, 16 insufficient CPU, 14 insufficient memory`).

The result lists pods held back by `spec.schedulingGates` (Kueue's `kueue.x-k8s.io/admission`, for example) under
**Scheduling-gated**. The simulator does not place them, because the scheduler ignores them until every gate is
removed. The chip's count leaves them out too.

If every pending pod fits on the current nodes, something the simulator does not model holds them, such as a
volume in another zone.

On demo-shop, two pods are pending and a third is gated. `quarterly-backfill` asks for 7 CPUs, more than any node has
free. `web-arm-canary` selects `kubernetes.io/arch=arm64`, and every node is amd64.

[Try it on demo-shop](demo/index.html){ .k8s-try target=_blank } (click the *pending* chip in the header).

## What if I add nodes?

*Can I fit this pod?*, *Simulate drain* and *Do the pending pods fit?* can all add nodes that the cluster does not
have yet. Use them to compare node shapes before you change a node group or a Karpenter NodePool. Click
**+ Add nodes**, pick one of these node kinds, and set how many:

- **an instance type the cluster runs**: a copy of its first node by name, with the same allocatable, labels (with a
  fresh `kubernetes.io/hostname`) and taints. The copy drops the taints that a new node would not start with. These are the
  `node.kubernetes.io/*` condition taints and the marks that autoscalers put on a node they are about to remove.
- **a custom shape**: allocatable CPU, memory and pod count, plus labels and taints (`dedicated=gpu:NoSchedule`).

In the drain and pending results, **+ Add nodes** sits under the summary and runs the simulation again.

Every hypothetical node is Ready and schedulable. Its name is `hypothetical-<instance-type>-<n>`, or
`hypothetical-custom-<n>` without an instance-type label. It also runs the DaemonSet pods that a new node of its kind
would run. A copy runs the DaemonSet pods of its template. A custom shape runs one pod of every DaemonSet whose pod
selects and tolerates it. For this, the simulator ignores the `metadata.name` pin that the DaemonSet controller adds to
each pod. The requests of these pods count against the node's room, as they would on a real node.

On demo-shop, open *Do the pending pods fit?* and add one `m5.2xlarge`. `quarterly-backfill` lands on
`hypothetical-m5.2xlarge-1`, but `web-arm-canary` still fits nowhere. That answers the question *would one more
general node do?*.

!!! note "In the demo"
    Hypothetical nodes need the server, so the demo turns **+ Add nodes** off. Run `k8sfoams --synthetic` to try
    them.

## Reading the result

While a result is on screen, both views outline the nodes:

- green: the node fits, or receives pods or copies
- red: the node cannot
- grey: the drained (or planned-for-removal) node

A strip above the map summarises the result. `Esc` or the strip's `×` clears it.

While their result is on screen, hypothetical nodes appear next to the real ones. The 2D map shows them as dashed
cards, and the 3D view shows them as ghost plates with a dashed edge. Their size comes from their allocatable, and they
hold the pods placed on them. They have no detail view. Every export (PNG, SVG, JSON, CSV) and the header totals leave
them out. When you clear the result, they go away.

The simulators check these predicates, in the scheduler's order:

1. cordon
2. `Ready`
3. `nodeSelector`
4. required node affinity
5. `NoSchedule`/`NoExecute` taints vs. tolerations
6. pod count
7. CPU, memory and every extended resource the pod requests, against the node's free allocatable
   (`Insufficient nvidia.com/gpu: requires 1, available 0`). A node that does not advertise a resource has none of it.
8. topology spread
9. required pod affinity and anti-affinity

A pod that tolerates `node.kubernetes.io/unschedulable` or `not-ready` (every DaemonSet pod does) still fits a cordoned
or NotReady node.

Node affinity is the pod's `requiredDuringSchedulingIgnoredDuringExecution` `nodeSelectorTerms`. The terms are ORed,
and the expressions inside a term are ANDed. The operators are `In`, `NotIn`, `Exists`, `DoesNotExist`, `Gt` and `Lt`,
plus `matchFields` on `metadata.name`. Drains, the consolidation finder and replica copies all honour it. Preferred
terms only score nodes, so the simulators ignore them.

Pod affinity and anti-affinity are the required `podAffinity` / `podAntiAffinity` terms. A term selects pods by
`labelSelector` in the pod's own namespace, in the listed `namespaces`, or in every namespace with an empty
`namespaceSelector: {}`. It compares the nodes by `topologyKey`. Like kube-scheduler, the simulators check both
directions:

- **affinity:** every term needs a selected pod in the node's domain (same zone, same host, ...), e.g. `Pod affinity: no
  pod matching app=cache in the same topology.kubernetes.io/zone`. The simulators reject a node without the topology
  label. The first pod of a group with affinity to itself can go anywhere. Otherwise it would stay Pending forever.
- **anti-affinity:** no selected pod can run in the node's domain (`Pod anti-affinity: shop/web-1 matches app=web in
  the same kubernetes.io/hostname`). Also, no pod that already runs there can have an anti-affinity term that selects
  the incoming pod (`Pod anti-affinity: shop/db-0 repels it in the same kubernetes.io/hostname`).

Topology spread covers the `topologySpreadConstraints` with `whenUnsatisfiable: DoNotSchedule`. `ScheduleAnyway`
only scores nodes. The simulators reject a domain for the pod when the pods that its `labelSelector` matches there
would exceed the emptiest domain's count by more than `maxSkew`. The count includes the matching pods in the pod's
namespace, plus the pod itself if it matches. The message reads:
`Topology spread: topology.kubernetes.io/zone=us-east-1a would have skew 2, over maxSkew 1 (3 matching pods there, 2
in the emptiest topology.kubernetes.io/zone)`. As in kube-scheduler:

- a node without the constraint's topology label is rejected and holds no domain.
- with `nodeAffinityPolicy: Honor` (the default), only nodes that pass the pod's `nodeSelector` and node affinity hold
  domains.
- with `nodeTaintsPolicy: Honor` (the default is `Ignore`), only nodes whose taints the pod tolerates hold domains.
- with fewer domains than `minDomains`, the emptiest one counts as 0.
- `matchLabelKeys` adds the pod's own values of those labels to the selector, so a Deployment's pods from an older
  ReplicaSet do not count.

Drains and replica copies see the pods they have already placed. So a workload with hostname anti-affinity grows by
at most one copy per node. A zone-spread workload's copies fill the zones evenly. Two evicted replicas never land on
the same node.

**Not simulated:** a non-empty `namespaceSelector` on pod affinity terms, and volume zone binding. Such a selector
needs namespace labels, so only the listed namespaces, or the pod's own, count. So a green verdict is necessary, not
sufficient.
