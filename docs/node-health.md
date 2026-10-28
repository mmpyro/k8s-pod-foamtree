# Node health

Free capacity on a node that refuses pods is not really free. A node that is cordoned, under pressure, or carrying a
`NoSchedule` taint has its **idle foam hatched with diagonal warning stripes** (the plate surface in 3D), gets a
warning badge next to the utilization percentage, and is counted in the sidebar's **Node health** panel. A healthy
cluster looks exactly as it would without this feature: nothing is added.

<figure class="k8sfoam-embed">
  <iframe src="../demo/index.html?embed=1#q=health%3Acordoned" loading="lazy" title="k8sfoams demo highlighting a cordoned node"></iframe>
  <figcaption>
    The demo-shop cluster with <code>health:cordoned</code> in the query bar: the cordoned node and its pods stay lit.
    <a href="../demo/index.html#q=health%3Acordoned" target="_blank">Open full screen</a>
  </figcaption>
</figure>

| Marker | Reason | Meaning | Try it |
| --- | --- | --- | --- |
| red | `cordoned` | `spec.unschedulable` is true — someone ran `kubectl cordon` | [demo-shop](demo/index.html#q=health%3Acordoned){ .k8s-try target=_blank } |
| red | `not ready` | the `Ready` condition is `False` or `Unknown` | [demo-platform](demo/index.html#ctx=arn:aws:eks:us-east-1:000000000000:cluster/demo-platform&q=health%3Anot-ready){ .k8s-try target=_blank } |
| amber | `mem pressure`, `disk pressure`, `pid pressure` | the matching kubelet condition is `True` | [memory](demo/index.html#q=health%3Amemory-pressure){ .k8s-try target=_blank } · [disk](demo/index.html#ctx=arn:aws:eks:us-east-1:000000000000:cluster/demo-platform&q=health%3Adisk-pressure){ .k8s-try target=_blank } · [pid](demo/index.html#ctx=arn:aws:eks:us-east-1:000000000000:cluster/demo-platform&q=health%3Apid-pressure){ .k8s-try target=_blank } |
| blue | `tainted` | at least one taint has effect `NoSchedule` or `NoExecute` | [demo-shop](demo/index.html#q=health%3Atainted){ .k8s-try target=_blank } |

Two rules are worth knowing:

- **`PreferNoSchedule` never marks a node.** It is a soft hint the scheduler is free to ignore, so it is listed in the
  focus overlay but does not stripe.
- **The cordon taint is folded into `cordoned`.** Kubernetes adds `node.kubernetes.io/unschedulable:NoSchedule` itself
  when you cordon; reporting it as a taint too would mark the same node twice for one fact, so it is dropped from the
  taint list.

The sidebar's **Node health** panel counts nodes per warning. Click a row to highlight those nodes and their pods in 2D
and 3D; every other node dims. This sets the query to `health:<warning>`; click the row again to clear it.

Click a node to open the focus overlay: a **Scheduling** section spells out every reason and lists each taint as
`key=value` with its effect. Worst reason wins the header pill — a cordoned node under memory pressure reads as
`SCHEDULING-DISABLED`, because that is what actually keeps pods off it.

GPU nodes get one more marker, `stranded devices`, for free devices that no pod can reach because the node's CPU or
memory is used up. See [Extended resources](extended-resources.md).
