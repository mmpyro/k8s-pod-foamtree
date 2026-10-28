# Controls

The sidebar holds every control; in the embedded dashboards on this site it starts collapsed, and the ☰ button in the
header opens it.

<figure class="k8sfoam-embed">
  <iframe src="../demo/index.html?embed=1" loading="lazy" title="k8sfoams demo with its sidebar controls"></iframe>
  <figcaption>
    Open the sidebar with ☰ to try the controls.
    <a href="../demo/index.html" target="_blank">Open full screen</a>
  </figcaption>
</figure>

- **View**: the [2D map](map-2d.md) or the [3D cubes](cubes-3d.md).
- **Resource**: CPU, memory, or an [extended resource](extended-resources.md) such as `nvidia.com/gpu` when the
  cluster can allocate one.
- **Memory unit**: MiB, GiB (default), or TiB.
- **Color by**: *Node* (default) gives each node its own hue; *QoS* colors each pod by its QoS class. See
  [QoS & eviction risk](qos.md).
- **Group by**: *None* (default), *Zone*, *Region*, *Pool*, *Type* (instance type) or *Capacity* (spot / on-demand).
  See [Topology](topology.md).
- **Context**: the sidebar lists every context from your kubeconfig, active one first, tagged by provider.
  **Switching only changes the context inside the k8sfoams web server — your `~/.kube/config` file is never
  modified.** The demo lists its three [synthetic clusters](synthetic-mode.md).
- **Refresh**: *Live* (default) updates as the cluster changes; *Interval* polls on the slider's schedule, from 5 to
  600 seconds. The slider also sets the fallback polling interval, and is disabled while Live is connected. The header
  shows *live* when the stream is connected and *reconnecting* while it is down. If live updates are unavailable, a
  note under the slider says so. The *Refresh now* button fetches immediately in either mode. The demo on this site is
  a fixed snapshot, so it shows *Refresh now* only.
- **Filter**: the header query bar highlights matching pods and dims the rest; nothing is removed from the view. See
  [Filtering](filtering.md) for the full grammar.
- **Focus**: click a node to open an overlay listing its pods with per-pod CPU/memory and container breakdown.

The URL keeps track of the context, view, resource, colour and grouping modes, memory unit, query and pinned workload,
so a link or bookmark reopens the same view. See [Shareable links](shareable-links.md).
