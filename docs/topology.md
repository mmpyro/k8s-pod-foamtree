# Topology

Set **Group by** in the sidebar to box nodes by where they run. In 2D each group is a dashed box sized by its share of
cluster capacity, with its nodes squarified inside it. In 3D each group is a floor under its plates. Either way the box
header shows the group's node count and utilisation. Use it to spot zones that are running hot, pools that are nearly
empty, or a workload sitting entirely on spot capacity.

<figure class="k8sfoam-embed">
  <iframe src="../demo/index.html?embed=1#groupBy=zone" loading="lazy" title="k8sfoams demo grouped by zone"></iframe>
  <figcaption>
    The demo-shop cluster grouped by zone. eu-west-1a holds more nodes, and the payments pods pinned to it.
    <a href="../demo/index.html#groupBy=zone" target="_blank">Open full screen</a>
  </figcaption>
</figure>

| Mode | Read from (first label present wins) | Try it |
| --- | --- | --- |
| Zone | `topology.kubernetes.io/zone`, `failure-domain.beta.kubernetes.io/zone` | [demo-shop](demo/index.html#groupBy=zone){ .k8s-try target=_blank } |
| Region | `topology.kubernetes.io/region`, `failure-domain.beta.kubernetes.io/region` | [demo-shop](demo/index.html#groupBy=region){ .k8s-try target=_blank } |
| Pool | `karpenter.sh/nodepool`, `eks.amazonaws.com/nodegroup`, `cloud.google.com/gke-nodepool`, `kubernetes.azure.com/agentpool`, `agentpool` | [GKE](demo/index.html#ctx=gke_demo-ml_us-central1_ml-training&groupBy=nodePool){ .k8s-try target=_blank } |
| Type | `node.kubernetes.io/instance-type`, `beta.kubernetes.io/instance-type` | [Karpenter](demo/index.html#ctx=arn:aws:eks:us-east-1:000000000000:cluster/demo-platform&groupBy=instanceType){ .k8s-try target=_blank } |
| Capacity | `karpenter.sh/capacity-type`, `eks.amazonaws.com/capacityType`, `cloud.google.com/gke-spot=true`, `kubernetes.azure.com/scalesetpriority=spot` — folded to `spot` / `on-demand` | [Karpenter](demo/index.html#ctx=arn:aws:eks:us-east-1:000000000000:cluster/demo-platform&groupBy=capacityType){ .k8s-try target=_blank } |

Nodes without the label share one last group, e.g. *no zone label*, so a local minikube or kind cluster shows a single
box. GKE and AKS only label spot nodes, so their unlabelled nodes land in *unknown capacity* instead of being guessed
on-demand — [the GKE demo cluster](demo/index.html#ctx=gke_demo-ml_us-central1_ml-training&groupBy=capacityType){ target=_blank }
shows it.

While grouping is on, the sidebar's **Topology** panel lists each group with its CPU %, Memory % and node count. Click a
row to highlight that group in 2D and 3D; every other node dims. This sets the query to `zone:<name>` (or `region:`,
`pool:`, `type:`, `capacity:`); click the row again to clear it.
