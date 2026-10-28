# Scheduling simulators

Two dry runs of the kube-scheduler's filter phase, against each node's free **allocatable** capacity (allocatable minus
the requests of the pods already bound there). Neither touches the cluster.

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
(`nvidia.com/gpu=1, ephemeral-storage=10Gi`), a `nodeSelector` (`zone=a, disk=ssd`) and tolerations
(`dedicated=gpu:NoSchedule`, `spot` for any value and effect, `*` for everything). Every node gets a verdict; the ones
that cannot take the pod say why, e.g. `Insufficient CPU: requires 4000m, available 1200m`.

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

## Reading the result

While a result is shown, nodes are outlined in both views — green fits / receives pods, red cannot, grey is the drained
node — and a strip above the map summarises it. `Esc` or its `×` clears it.

Predicates checked, in the scheduler's order: cordon, `Ready`, `nodeSelector`, `NoSchedule`/`NoExecute` taints vs.
tolerations, pod count, CPU, memory, then every extended resource the pod requests against the node's free allocatable
(`Insufficient nvidia.com/gpu: requires 1, available 0`; a node that does not advertise a resource has none). A pod
that tolerates `node.kubernetes.io/unschedulable` or `not-ready` (every DaemonSet pod does) still fits a cordoned or
NotReady node.

**Not simulated:** node/pod affinity, topology spread, PodDisruptionBudgets and volume zone binding — so a green
verdict is necessary, not sufficient.
