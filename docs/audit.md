# Audit & hygiene

k8sfoams checks every pod against the best-practice rules below. Workload rules check all replicas of a Deployment,
StatefulSet or ReplicaSet together, and mark each of its pods. A pod that breaks a rule gets a **small warning glyph in
the top-right corner** of its box. Hover the glyph to see the reasons.

The sidebar's **Audit & Hygiene** panel counts the affected pods per rule. Click a row to highlight those pods in 2D
and 3D. The click sets the query to `audit:<rule>`. Click the row again to clear it. A clean cluster shows
`No issues found`.

<figure class="k8sfoam-embed">
  <iframe src="../demo/index.html?embed=1#q=audit%3Aspof-zone" loading="lazy" title="k8sfoams demo highlighting replicas in one zone"></iframe>
  <figcaption>
    <code>audit:spof-zone</code> on demo-shop: every payments replica is pinned to eu-west-1a.
    <a href="../demo/index.html#q=audit%3Aspof-zone" target="_blank">Open full screen</a>
  </figcaption>
</figure>

| Marker | Rule | Flagged when | Try it |
| --- | --- | --- | --- |
| amber | `missing requests` | a regular container requests 0 CPU or 0 memory | [demo-shop](demo/index.html#q=audit%3Amissing-requests){ .k8s-try target=_blank } |
| blue | `no memory limit` | a regular container sets no `limits.memory` | [demo-shop](demo/index.html#q=audit%3Amissing-limits){ .k8s-try target=_blank } |
| blue | `limit far above request` (`limit-overcommit`) | a regular container's memory limit is more than 4× its memory request | [demo-shop](demo/index.html#q=audit%3Alimit-overcommit){ .k8s-try target=_blank } |
| amber | `monolith` | the pod reserves more than 80% of its node's CPU or memory | [demo-shop](demo/index.html#q=audit%3Amonolith){ .k8s-try target=_blank } |
| blue | `ratio asymmetry` | the pod's share of node CPU and its share of node memory differ by 4× or more, and the larger share is at least 10% | [demo-shop](demo/index.html#q=audit%3Aratio-asymmetry){ .k8s-try target=_blank } |
| amber | `naked pod` | no controller owns the pod, so a drain or node failure deletes it for good (static pods are not flagged, because the kubelet restarts them) | [demo-shop](demo/index.html#q=audit%3Anaked-pod){ .k8s-try target=_blank } |
| blue | `single replica` | a Deployment or StatefulSet runs exactly one pod | [demo-shop](demo/index.html#q=audit%3Asingle-replica){ .k8s-try target=_blank } |
| amber | `replicas on one node` (`spof-node`) | a workload with 2 or more replicas has all of them on the same node | [demo-shop](demo/index.html#q=audit%3Aspof-node){ .k8s-try target=_blank } |
| amber | `replicas in one zone` (`spof-zone`) | otherwise, all replicas sit in one zone while the cluster has nodes in more than one | [demo-shop](demo/index.html#q=audit%3Aspof-zone){ .k8s-try target=_blank } |
| amber | `spot-only replicas` (`spof-spot`) | a workload with 2 or more replicas runs only on [spot capacity](topology.md) | [demo-shop](demo/index.html#q=audit%3Aspof-spot){ .k8s-try target=_blank } |

These details are worth knowing:

- **Init containers are not audited.** They finish before the app starts, so their requests and limits say nothing
  about how the pod behaves after that.
- **CPU limits are not required.** The audit flags only a missing *memory* limit. A memory leak without a limit can
  take the whole node down. A CPU spike without a limit only gets throttled.
- **Limit overcommit is memory-only.** The scheduler packs nodes by request. A limit far above the request lets the
  node promise more memory than it has, and the kubelet OOM-kills by real usage. The two rules above already report a
  container with no request or no limit, so this rule does not count it again. CPU overcommit only throttles, so the
  audit does not check it. A CPU limit-to-request rule is a possible follow-up.
- **Ratio asymmetry ignores small pods.** A sidecar asking for 5% of the CPU and almost no memory has an extreme ratio,
  but it leaves no meaningful capacity stranded.
- **Replicas are the pods k8sfoams sees.** Workload rules count scheduled, running pods. k8sfoams does not read
  `spec.replicas`, so it needs no extra API call. A Deployment scaled to 2 with one pod still Pending reads as a single replica.
- **Spread rules skip DaemonSets and Jobs.** A DaemonSet runs one pod per node by design, and a Job runs to completion.
  Zones and capacity types come from the same node labels as [Group by](topology.md). A single-zone cluster, or one
  without zone labels (minikube, kind), never reports `spof-zone`. A replica on a node without a capacity-type label
  does not count as spot.
