# QoS & eviction risk

Under memory pressure the kubelet evicts pods by QoS class. Set **Color by → QoS** in the sidebar to color every pod box
(2D) and cube (3D) by its class. Node cards and plates turn a neutral slate, so only the pods carry color.

<figure class="k8sfoam-embed">
  <iframe src="../demo/index.html?embed=1#colorBy=qos" loading="lazy" title="k8sfoams demo colored by QoS class"></iframe>
  <figcaption>
    demo-shop colored by QoS class: mostly Burstable, Guaranteed databases and Kafka, one BestEffort debug shell.
    <a href="../demo/index.html#colorBy=qos" target="_blank">Open full screen</a>
  </figcaption>
</figure>

| Color | Class | Eviction order | Try it |
| --- | --- | --- | --- |
| red | `BestEffort` | first — no container sets any request or limit | [demo-shop](demo/index.html#colorBy=qos&q=qos%3ABestEffort){ .k8s-try target=_blank } |
| amber | `Burstable` | second — requests are set but lower than limits | [demo-shop](demo/index.html#colorBy=qos&q=qos%3ABurstable){ .k8s-try target=_blank } |
| green | `Guaranteed` | last — every container's requests equal its limits | [demo-shop](demo/index.html#colorBy=qos&q=qos%3AGuaranteed){ .k8s-try target=_blank } |

The sidebar's **QoS & Eviction Risk** panel counts pods per class, riskiest first. It is shown in either color mode,
and a class with no pods still reads `0`. Click a row to highlight those pods; this sets the query to `qos:<Class>`.
Click it again to clear.

The class is read from the pod's `status.qosClass`, which the kubelet sets. It is not recomputed from requests and
limits. A pod with no reported class renders neutral and is not counted.
