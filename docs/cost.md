# Cost

The sidebar's **Cost** panel shows what the cluster's nodes cost per month at **on-demand list price**, and how much of
that pays for idle capacity. The focus overlay of a node shows the same for that node, and with **Group by** on, the
**Topology** panel adds each group's monthly cost.

Pricing never breaks the dashboard. If a price is missing, that node is left out of the total and the panel shows a
warning row instead. Everything else works as before.

## Where prices come from

Each node's cloud comes from `spec.providerID` (`aws://`, `azure://`, `gce://`). Its region and instance type come from
the [topology](topology.md) labels. Nodes on no known cloud (kind, minikube, bare metal) have no price, and the panel
stays hidden for them.

1. **Bundled snapshot.** The package ships a table of Linux on-demand prices, so prices work offline and need no
   credentials. The panel note shows the snapshot date.
2. **Live refresh.** In the background, k8sfoams asks each cloud's pricing API for the instance types it sees, and
   caches the answers for 24 hours. A live price replaces the snapshot one. A failed fetch is retried after an hour,
   the snapshot price stays in use, and the panel note names the provider that could not be reached.

| Cloud | API | Needs |
| --- | --- | --- |
| Azure | Retail Prices API | nothing, it is public |
| AWS | Price List `GetProducts` | `pip install 'k8sfoams[aws]'` and AWS credentials allowed `pricing:GetProducts` |
| GCP | Cloud Billing Catalog | Application Default Credentials (`gcloud auth application-default login`) |

GCP prices vCPU and memory per machine family, so k8sfoams prices a machine type from its shape
(`n2-highmem-8` = 8 vCPU + 64 GiB). The predefined E2, N1, N2, N2D, N4, C3 and T2D shapes are covered, and so are the
GPU types A2 (`highgpu`, `megagpu`, `ultragpu`), A3 `highgpu` and G2, which add the price of each attached GPU
(`g2-standard-8` = 8 vCPU + 32 GiB + 1 L4). Custom and compute- or memory-optimized types, and GPUs attached to N1
nodes, get a *No price* warning.

`--synthetic` clusters never go to the network. They use the snapshot alone.

## Idle cost

A node's idle cost is its monthly price multiplied by the unrequested share of its **busier** resource. A node with
80% of its memory requested and 20% of its CPU is 20% idle, not 80%: no more pods fit there by memory, so its free CPU
cannot be used.

## What is not priced

Spot nodes are counted at the on-demand price, so for them the figure is an upper bound. The panel says how many spot
nodes there are. Reserved instances, savings plans, committed-use and negotiated discounts, Windows nodes, and
storage, network and control-plane charges are also not included. Prices are in USD, at 730 hours per month.

## Refreshing the snapshot

```bash
uv pip install -e '.[dev]'
python -m k8sfoam.src.pricing.snapshot            # aws azure gcp
python -m k8sfoam.src.pricing.snapshot azure      # one provider
```

If a provider fails, its previous table is kept, so missing credentials never wipe its prices. The command exits
non-zero when any provider fails.
