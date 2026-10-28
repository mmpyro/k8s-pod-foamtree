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
| **JSON report** | ✓ | ✓ | Every node (with its topology) and pod with capacity, requests, QoS, labels, findings, warnings and containers. CPU in millicores, memory in MiB. Extended resources sit under `extended`, in MiB when byte-sized and as a count otherwise. |
| **CSV report** | ✓ | ✓ | One row per pod: `context,node,zone,region,node_pool,instance_type,capacity_type,namespace,pod,qos,cpu_millicores,memory_mib,containers,init_containers,findings,node_warnings,matched`. One more column per [extended resource](extended-resources.md) on the cluster follows `memory_mib`, e.g. `nvidia.com/gpu` or `ephemeral_storage_mib`. |
| **Copy link** | ✓ | ✓ | Puts a [shareable link](shareable-links.md) to the current view on the clipboard; the item reads *copied* to confirm. |

Images keep the current metric, colours, grouping, tweaks, query dimming and pinned workload, and carry a title band
with the context, totals and refresh time. Reports always list every pod; with a query active, `matched` says which
ones it selected. Files are named `k8sfoams-<context>-<2d|3d|report>-YYYYMMDD-HHmm.<ext>`. Web fonts are not embedded,
so exported images fall back to system fonts.
