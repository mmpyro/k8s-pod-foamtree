# Export

The download button in the header, next to *Refresh*, saves what you are looking at — for capacity-planning decks,
Slack updates and incident reports. Everything is built in the browser; nothing extra is fetched from the cluster. The
embedded demos on this site export too.

<figure class="k8sfoam-embed">
  <iframe src="../demo/index.html?embed=1#groupBy=zone&colorBy=qos" loading="lazy" title="k8sfoams demo to try exports on"></iframe>
  <figcaption>
    Use the download button in the header to export this view.
    <a href="../demo/index.html#groupBy=zone&colorBy=qos" target="_blank">Open full screen</a>
  </figcaption>
</figure>

| Item | 2D map | 3D cubes | Contents |
| --- | --- | --- | --- |
| **PNG image** | ✓ | ✓ | The current view at 2× resolution. |
| **SVG image** | ✓ | — | The 2D map as real vectors (cards, pods, containers, labels). The 3D view is WebGL, so it has no SVG form. |
| **JSON report** | ✓ | ✓ | Every node (with its topology) and pod with capacity, requests, QoS, labels, findings, warnings and containers. CPU in millicores, memory in MiB. Extended resources sit under `extended`, in MiB when byte-sized and as a count otherwise. `schemaVersion` and `k8sfoamsVersion` say which format and release wrote it, so the file can be [opened again](#snapshots) later. |
| **CSV report** | ✓ | ✓ | One row per pod: `context,node,zone,region,node_pool,instance_type,capacity_type,namespace,pod,qos,cpu_millicores,memory_mib,containers,init_containers,findings,node_warnings,matched`. One more column per [extended resource](extended-resources.md) on the cluster follows `memory_mib`, e.g. `nvidia.com/gpu` or `ephemeral_storage_mib`. |
| **Copy link** | ✓ | ✓ | Puts a [shareable link](shareable-links.md) to the current view on the clipboard; the item reads *copied* to confirm. |
| **Open snapshot…** | ✓ | ✓ | Opens a JSON report as a [snapshot](#snapshots). |
| **Compare with snapshot…** | ✓ | ✓ | Shows what changed between a JSON report and the current view: [comparing snapshots](#comparing-snapshots). |

Images keep the current metric, colours, grouping, tweaks, query dimming and pinned workload, and carry a title band
with the context, totals and refresh time. Reports always list every pod; with a query active, `matched` says which
ones it selected. Files are named `k8sfoams-<context>-<2d|3d|report>-YYYYMMDD-HHmm.<ext>`. Web fonts are not embedded,
so exported images fall back to system fonts.

The same JSON and CSV reports come without the browser from [`k8sfoams report`](cli.md#report), for CI and cron.

## Snapshots

A JSON report can be opened again later, with no cluster behind it: for an incident review, or to look at a cluster
you no longer have access to. Pick *Open snapshot…* in the download menu, or drop the `.json` file anywhere on the page.
This works even when the server cannot read your kubeconfig.

While a snapshot is open:

- A banner names the context and the time the report was taken, with **Back to live** to return.
- The map, the 3D view, the query bar, grouping, colouring, workload pinning and exports all work as usual.
- Anything that asks the cluster is off: auto-refresh, live updates, the context switcher, the scheduling simulators
  and the AI advisor. *Copy link* is off too, since a link cannot carry the file.

Reports from older releases open as well. A report written by a newer k8sfoams with a format this version does not know
(`schemaVersion`) is refused with a message saying which version to upgrade to.

## Comparing snapshots

*Compare with snapshot…* in the download menu compares a JSON report with what is on screen: before and after a
release, or the state during an incident against now.

- **Snapshot vs live**: with the live cluster on screen, the comparison follows the cluster as it changes.
- **Two snapshots**: open one, then compare it with the other. The older report is always the "before" side, whichever
  you pick first.

Pods are matched by namespace and name first. A rollout renames every pod, so the pods left over are paired within the
same workload; a replaced replica is not counted as one pod added and one removed. Each pod then gets one of these
changes:

| Change | Colour | Meaning |
| --- | --- | --- |
| **added** | green | in the current view only |
| **removed** | red | in the compared snapshot only |
| **resized** | amber | requests (CPU, memory or an extended resource) changed |
| **moved** | blue | runs on another node |

*Color by → Diff* (switched on when a comparison starts) paints each pod with its change; unchanged pods keep the usual
violet. A node that no longer exists is drawn as a dashed ghost labelled *removed*, holding its removed pods. A pod
removed from a node that still exists has nowhere to be drawn, so it is listed in the panel instead.

The *Comparison* panel in the sidebar counts each change; a row selects its [`diff:` query](filtering.md#token-reference)
(`diff:added`, `diff:removed`, `diff:resized`, `diff:moved`), which highlights those pods like any other query. Below,
it lists the change in requested CPU and memory per namespace, node pool and node, biggest first. The × ends the
comparison.
