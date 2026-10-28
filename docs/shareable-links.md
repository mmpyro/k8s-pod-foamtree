# Shareable links

The view is encoded in the URL hash, so a link reproduces exactly what the sender saw and any view can be bookmarked:

```
http://localhost:8080/#ctx=prod&view=3d&groupBy=zone&q=ns%3Apayments
```

Every embedded dashboard and *Try it* link on this site is such a link into the [live demo](demo/index.html). This one
opens demo-shop in 3D, grouped by zone, colored by QoS, with the checkout namespace highlighted:
[open it](demo/index.html#view=3d&groupBy=zone&colorBy=qos&q=ns%3Acheckout){ .k8s-try target=_blank }

| Parameter | Values | Default |
| --- | --- | --- |
| `ctx` | Context name as listed in your kubeconfig (the name, not its position) | the active context |
| `view` | `2d`, `3d` | `2d` |
| `metric` | `cpu`, `mem`, or an [extended resource](extended-resources.md) such as `nvidia.com/gpu` | `cpu` |
| `colorBy` | `node`, `qos` | `node` |
| `groupBy` | `none`, `zone`, `region`, `nodePool`, `instanceType`, `capacityType` | `none` |
| `memUnit` | `MiB`, `GiB`, `TiB` | `GiB` |
| `q` | A [query](filtering.md), URL-encoded | empty |
| `wl` | Pinned workload, e.g. `web-api` | none |

- The URL updates as you change the view. Parameters at their default are left out, and `ctx` is always written.
- The URL is replaced in place, so typing a query does not fill your browser history.
- Pasting a link into a tab that is already open applies it. A parameter the link leaves out goes back to its default.
- A context that is not in your kubeconfig falls back to the active one, and a notice above the map says so. A resource
  the context cannot allocate falls back to CPU, as when switching contexts. Any other unknown value uses its default.
  [Try it](demo/index.html#ctx=prod){ .k8s-try target=_blank }
- A pinned workload that matches no pod in the context is dropped, so it cannot dim the whole map.
- *Zoom*, *Refresh* and the tweaks panel are not part of the link.

The demo also understands `?embed=1` before the hash, which starts it with the sidebar collapsed and adds a button that
opens the same view full-page. See [Synthetic mode](synthetic-mode.md#the-live-demo-on-this-site).
