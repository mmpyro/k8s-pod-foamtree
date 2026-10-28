# Extended resources

Besides CPU and memory, the *Resource* picker lists every extended resource a node in the current context can
allocate: GPUs and other device-plugin resources (`nvidia.com/gpu`, `amd.com/gpu`), `ephemeral-storage` and HugePages
(`hugepages-2Mi`, `hugepages-1Gi`). When there is at least one, the two-button toggle becomes a dropdown. Switching to a
context without the selected resource falls back to CPU.

<figure class="k8sfoam-embed">
  <iframe src="../demo/index.html?embed=1#ctx=gke_demo-ml_us-central1_ml-training&metric=nvidia.com%2Fgpu" loading="lazy" title="k8sfoams demo GPU map"></iframe>
  <figcaption>
    GPUs on the GKE demo cluster: A100 and spot L4 pools, with the header showing free GPUs and the largest free block.
    <a href="../demo/index.html#ctx=gke_demo-ml_us-central1_ml-training&metric=nvidia.com%2Fgpu" target="_blank">Open full screen</a>
  </figcaption>
</figure>

- **2D map.** Nodes are sized by how much of the resource they can allocate, and pods by what they request. Nodes
  without the resource are hidden, and the header counts them.
- **Fragmentation.** For devices the header also shows the free units cluster-wide and the largest free block on one
  node. For example, 6 free GPUs spread 2 + 2 + 2 cannot run a 4-GPU job.
- **3D cubes.** Cubes keep plotting CPU × Memory. Pods requesting the selected resource stay lit and the rest dim.
  Plates show `used/allocatable` for it.
  [Try it](demo/index.html#ctx=gke_demo-ml_us-central1_ml-training&metric=nvidia.com%2Fgpu&view=3d){ .k8s-try target=_blank }
- **Node health.** `stranded devices` marks a node that still has free devices but has 90% or more of its CPU or
  memory requested, so no pod can reach those devices. Query it with `health:stranded-devices`.
  [Try it](demo/index.html#ctx=gke_demo-ml_us-central1_ml-training&metric=nvidia.com%2Fgpu&q=health%3Astranded-devices){ .k8s-try target=_blank }

The other resources of the GKE demo cluster:
[HugePages](demo/index.html#ctx=gke_demo-ml_us-central1_ml-training&metric=hugepages-2Mi){ .k8s-try target=_blank }
· [ephemeral storage](demo/index.html#ctx=gke_demo-ml_us-central1_ml-training&metric=ephemeral-storage){ .k8s-try target=_blank }

Requests follow the scheduler's rules, including init containers and native sidecars. An extended resource set only as
a limit counts as a request of the same size. Node sizes come from `status.allocatable`, for CPU and memory too, so
kube- and system-reserved capacity is not drawn as free space.
