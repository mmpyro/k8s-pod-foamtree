# Node health

Free capacity on a node that refuses pods is not really free. A node can be cordoned, under pressure, or carry a
`NoSchedule` taint. Such a node gets three markers:

- k8sfoams draws its **idle foam hatched with diagonal warning stripes** (the plate surface in 3D).
- The node gets a warning badge next to the utilization percentage.
- The sidebar's **Node health** panel counts the node.

A healthy cluster looks exactly as it would without this feature. k8sfoams adds nothing to it.

<figure class="k8sfoam-embed">
  <iframe src="../demo/index.html?embed=1#q=health%3Acordoned" loading="lazy" title="k8sfoams demo highlighting a cordoned node"></iframe>
  <figcaption>
    The demo-shop cluster with <code>health:cordoned</code> in the query bar: the cordoned node and its pods stay lit.
    <a href="../demo/index.html#q=health%3Acordoned" target="_blank">Open full screen</a>
  </figcaption>
</figure>

| Marker | Reason | Meaning | Try it |
| --- | --- | --- | --- |
| red | `cordoned` | `spec.unschedulable` is true: someone ran `kubectl cordon` | [demo-shop](demo/index.html#q=health%3Acordoned){ .k8s-try target=_blank } |
| red | `not ready` | the `Ready` condition is `False` or `Unknown` | [demo-platform](demo/index.html#ctx=arn:aws:eks:us-east-1:000000000000:cluster/demo-platform&q=health%3Anot-ready){ .k8s-try target=_blank } |
| amber | `mem pressure`, `disk pressure`, `pid pressure` | the matching kubelet condition is `True` | [memory](demo/index.html#q=health%3Amemory-pressure){ .k8s-try target=_blank } · [disk](demo/index.html#ctx=arn:aws:eks:us-east-1:000000000000:cluster/demo-platform&q=health%3Adisk-pressure){ .k8s-try target=_blank } · [pid](demo/index.html#ctx=arn:aws:eks:us-east-1:000000000000:cluster/demo-platform&q=health%3Apid-pressure){ .k8s-try target=_blank } |
| blue | `tainted` | at least one taint has effect `NoSchedule` or `NoExecute` | [demo-shop](demo/index.html#q=health%3Atainted){ .k8s-try target=_blank } |

Two rules are worth knowing:

- **`PreferNoSchedule` never marks a node.** It is a soft hint that the scheduler is free to ignore. The focus
  overlay lists it, but the node gets no stripes.
- **The cordon taint is folded into `cordoned`.** Kubernetes adds `node.kubernetes.io/unschedulable:NoSchedule` itself
  when you cordon a node. A taint marker as well would mark the same node twice for one fact. So k8sfoams drops this
  taint from the taint list.

The sidebar's **Node health** panel counts nodes per warning. Click a row to highlight those nodes and their pods in 2D
and 3D. Every other node dims. The click sets the query to `health:<warning>`. Click the row again to clear it.

Click a node to open the focus overlay. Its **Scheduling** section spells out every reason and lists each taint as
`key=value` with its effect. The worst reason wins the header pill. A cordoned node under memory pressure reads as
`SCHEDULING-DISABLED`, because the cordon is what keeps pods off it.

GPU nodes get one more marker, `stranded devices`, for free devices that no pod can reach because the node's CPU or
memory is used up. See [Extended resources](extended-resources.md).
