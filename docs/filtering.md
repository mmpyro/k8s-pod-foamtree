# Filtering

The query bar in the header is a **highlighter, not a filter of last resort**: matching pods glow, everything else dims.
No pod, node or box ever leaves the layout, so the shape of the cluster stays comparable while you narrow down. Once the
query is non-empty and valid, a live counter inside the input reads `N / M pods` (and turns red at `0`).

<figure class="k8sfoam-embed">
  <iframe src="../demo/index.html?embed=1#q=ns%3Acheckout" loading="lazy" title="k8sfoams demo filtered to the checkout namespace"></iframe>
  <figcaption>
    <code>ns:checkout</code> on demo-shop. Type your own query into the bar.
    <a href="../demo/index.html#q=ns%3Acheckout" target="_blank">Open full screen</a>
  </figcaption>
</figure>

Type whitespace-separated tokens. **All tokens are ANDed** — a pod must satisfy every one of them:

```
ns:storefront qos:Burstable tier=frontend
```

[Try it](demo/index.html#q=ns%3Astorefront%20qos%3ABurstable%20tier%3Dfrontend){ .k8s-try target=_blank }

An empty query matches everything. A query that contains a malformed token is **inert**: nothing dims, and the
offending tokens are listed under the bar with the reason. Half-typing `ns:` can never blank the view.

## Token reference

| Token | Matches | Notes |
| --- | --- | --- |
| `ns:<name>` | pod namespace, exact | case-insensitive (`ns:Kube-System` works) |
| `node:<glob>` | node the pod is scheduled on | `*` is the only wildcard; anchored (whole name must match); case-insensitive |
| `qos:<class>` | [QoS class](qos.md): `Guaranteed`, `Burstable`, `BestEffort` | case-insensitive; anything else is an error |
| `has:init-containers` | pods declaring at least one init container | currently the only `has:` field |
| `audit:<rule>` | pods breaking an [audit rule](audit.md) | `missing-requests`, `missing-limits`, `limit-overcommit`, `monolith`, `ratio-asymmetry`, `naked-pod`, `single-replica`, `spof-node`, `spof-zone`, `spof-spot` |
| `health:<warning>` | nodes carrying a [health warning](node-health.md), and every pod on them | `cordoned`, `not-ready`, `memory-pressure`, `disk-pressure`, `pid-pressure`, `tainted`, `stranded-devices` |
| `zone:<glob>` `region:<glob>` `pool:<glob>` `type:<glob>` | nodes with that [topology](topology.md) label, and every pod on them | globs like `node:` (`zone:us-east-1*`); a node without the label never matches |
| `capacity:<type>` | spot or on-demand nodes, and every pod on them | `spot`, `on-demand`; case-insensitive |
| `key=value` | pod label equals value | key and value are **case-sensitive** (Kubernetes labels are) |
| `key!=value` | pod label differs from value | a **missing** label counts as unequal, so it matches too |
| `text` | pod name contains `text` | case-insensitive substring |
| `"quoted text"` | pod name contains `quoted text` | quotes force literal text — the grammar is skipped |

## Examples

Every pod named like `web`, anywhere
[Try it](demo/index.html#q=web){ .k8s-try target=_blank }

```
web
```

Pods in `storefront` that run init containers
[Try it](demo/index.html#q=ns%3Astorefront%20has%3Ainit-containers){ .k8s-try target=_blank }

```
ns:storefront has:init-containers
```

Pods the kubelet evicts before Guaranteed ones, on spot nodes
[Try it](demo/index.html#q=qos%3ABurstable%20capacity%3Aspot){ .k8s-try target=_blank }

```
qos:Burstable capacity:spot
```

Frontend pods that are **not** in production, named like `web`
[Try it](demo/index.html#q=tier%3Dfrontend%20env!%3Dprod%20web){ .k8s-try target=_blank }

```
tier=frontend env!=prod web
```

Everything in one availability zone, in one namespace
[Try it](demo/index.html#q=zone%3Aeu-west-1a%20ns%3Acheckout){ .k8s-try target=_blank }

```
zone:eu-west-1a ns:checkout
```

The node whose free GPUs no pod can reach, and every pod on it
[Try it](demo/index.html#ctx=gke_demo-ml_us-central1_ml-training&q=health%3Astranded-devices){ .k8s-try target=_blank }

```
health:stranded-devices
```

One specific node — globs are anchored, so dots are literal, not wildcards:

```
node:ip-10-0-1-5.ec2.internal
```

Guaranteed pods carrying a label value with a space:

```
qos:Guaranteed app="my app"
```

Match a pod name that *looks* like a filter token — leading quotes make the whole token literal text:

```
"web:1"
```

Without the quotes, `web:1` is read as an unknown filter prefix and reported as an error.

## Sharp edges

- **`!=` wins over `=`.** `env!=prod` is one inequality, never `env!` equals `prod`.
- **A filter prefix must be a bare word before `:`.** `app=ns:x` is a label selector for key `app`, value `ns:x` — not
  a namespace filter.
- **Only `node:`, `health:` and the topology tokens can dim a node.** Node plates and boxes stay in the layout either
  way; pod-level terms dim pods, never their node.
- **A missing label matches `!=`.** `env!=prod` highlights pods with `env: staging` *and* pods with no `env` label at
  all — the Kubernetes selector semantics.
  [Try it](demo/index.html#q=env!%3Dprod){ .k8s-try target=_blank }
- **Every problem is reported at once.** The parser never stops on the first bad token, so a three-error query lists
  three errors. [Try it](demo/index.html#q=ns%3A%20qos%3ACheap%20rack%3Ar1){ .k8s-try target=_blank }

## Errors you can hit

| Query | Message |
| --- | --- |
| `ns:` | `ns: needs a value` |
| `qos:Cheap` | `unknown QoS class — use Guaranteed, Burstable or BestEffort` |
| `has:sidecars` | `unknown has: field — use init-containers` |
| `capacity:reserved` | `unknown capacity type — use spot or on-demand` |
| `rack:r1` | `unknown filter — use ns:, node:, qos:, has:, …` |
| `=frontend` | `label selector needs a key` |
| `app=` | `label selector needs a value` |
| `""` | `empty quoted value` |
| `app="my app` | `unterminated quoted value` |

Focusing the input opens a popover with the same token list; it is replaced by the error list while a token is
malformed. The `×` on the right clears the query.
