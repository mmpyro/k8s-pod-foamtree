# Export

The download button in the header, next to *Refresh*, saves the current view. Use it for capacity-planning decks,
Slack updates and incident reports. The browser builds every file and fetches nothing extra from the cluster. The
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
| **Copy link** | ✓ | ✓ | Puts a [shareable link](shareable-links.md) to the current view on the clipboard. The item then reads *copied*. |
| **Open snapshot…** | ✓ | ✓ | Opens a JSON report as a [snapshot](#snapshots). |
| **Compare with snapshot…** | ✓ | ✓ | Shows the changes between a JSON report and the current view. See [comparing snapshots](#comparing-snapshots). |

Images keep the current metric, colours, grouping, tweaks, query dimming and pinned workload. Each image has a title
band with the context, totals and refresh time. Reports always list every pod. When a query is active, `matched` shows
which pods it selected. The file name is `k8sfoams-<context>-<2d|3d|report>-YYYYMMDD-HHmm.<ext>`. The export does not
embed web fonts, so exported images use system fonts.

[`k8sfoams report`](cli.md#report) writes the same JSON and CSV reports without the browser, for CI and cron.

## Snapshots

You can open a JSON report again later, with no cluster behind it. Use this for an incident review, or to see a
cluster that you can no longer access. Pick *Open snapshot…* in the download menu, or drop the `.json` file anywhere on
the page. This works even when the server cannot read your kubeconfig.

While a snapshot is open:

- A banner shows the context and the time of the report. **Back to live** returns to the cluster.
- The map, the 3D view, the query bar, grouping, colouring, workload pinning and exports all work as usual.
- Every feature that asks the cluster is off: auto-refresh, live updates, the context switcher, the scheduling simulators
  and the AI advisor. *Copy link* is off too, since a link cannot carry the file.

Reports from older releases open as well. A newer k8sfoams can write a format (`schemaVersion`) that this version does
not know. This version then refuses the report, and the message tells you which version to upgrade to.

## Comparing snapshots

*Compare with snapshot…* in the download menu compares a JSON report with the current view. Use it to compare before
and after a release, or the state during an incident with the state now.

- **Snapshot vs live**: with the live cluster on screen, the comparison follows the cluster as it changes.
- **Two snapshots**: open one, then compare it with the other. The older report is always the "before" side, whichever
  you pick first.

The comparison first matches pods by namespace and name. A rollout renames every pod, so the comparison then pairs the
remaining pods within the same workload. A replaced replica does not count as one pod added and one pod removed. Each
pod then gets one of these changes:

| Change | Colour | Meaning |
| --- | --- | --- |
| **added** | green | in the current view only |
| **removed** | red | in the compared snapshot only |
| **resized** | amber | requests (CPU, memory or an extended resource) changed |
| **moved** | blue | runs on another node |

A comparison turns on *Color by → Diff* when it starts. This mode paints each pod with its change. Unchanged pods keep
the usual violet. A node that no longer exists shows as a dashed ghost labelled *removed*, with its removed pods in it.
A pod removed from a node that still exists has no place on the map, so the panel lists it instead.

The *Comparison* panel in the sidebar counts each change. A row selects its [`diff:` query](filtering.md#token-reference)
(`diff:added`, `diff:removed`, `diff:resized`, `diff:moved`), which highlights those pods like any other query. Under
the counts, the panel lists the change in requested CPU and memory per namespace, node pool and node, biggest first.
The × ends the comparison.
