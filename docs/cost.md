# Cost

The sidebar's **Cost** panel shows what the cluster's nodes cost per month at **on-demand list price**. It also shows
how much of that pays for idle capacity. The focus overlay of a node shows the same for that node. With **Group by**
on, the **Topology** panel adds each group's monthly cost.

Pricing never breaks the dashboard. If a price is missing, k8sfoams leaves that node out of the total, and the panel
shows a warning row instead. Everything else works as before.

## Where prices come from

Each node's cloud comes from `spec.providerID` (`aws://`, `azure://`, `gce://`). Its region and instance type come from
the [topology](topology.md) labels. Nodes on no known cloud (kind, minikube, bare metal) have no price, and the panel
stays hidden for them.

1. **Bundled snapshot.** The package ships a table of Linux on-demand prices. Prices therefore work offline and need
   no credentials. The panel note shows the snapshot date.
2. **Live refresh.** In the background, k8sfoams asks each cloud's pricing API for the instance types it sees. It
   caches the answers for 24 hours. A live price replaces the snapshot price. If a fetch fails, k8sfoams tries again
   after an hour. The snapshot price stays in use, and the panel note names the provider that k8sfoams could not
   reach.

| Cloud | API | Needs |
| --- | --- | --- |
| Azure | Retail Prices API | nothing, it is public |
| AWS | Price List `GetProducts` | `pip install 'k8sfoams[aws]'` and AWS credentials allowed `pricing:GetProducts` |
| GCP | Cloud Billing Catalog | Application Default Credentials (`gcloud auth application-default login`) |

GCP prices vCPU and memory per machine family. So k8sfoams prices a machine type from its shape
(`n2-highmem-8` = 8 vCPU + 64 GiB). k8sfoams covers the predefined E2, N1, N2, N2D, N4, C3 and T2D shapes. It also
covers the GPU types A2 (`highgpu`, `megagpu`, `ultragpu`), A3 `highgpu` and G2. For these, it adds the price of each
attached GPU (`g2-standard-8` = 8 vCPU + 32 GiB + 1 L4). Custom types, compute-optimized and memory-optimized types,
and GPUs attached to N1 nodes get a *No price* warning.

`--synthetic` clusters never go to the network. They use the snapshot alone.

## Idle cost

A node's idle cost is its monthly price multiplied by the unrequested share of its **busier** resource. Take a node
with 80% of its memory requested and 20% of its CPU requested. That node is 20% idle, not 80%. No more pods fit there
by memory, so no pod can use its free CPU.

## What is not priced

k8sfoams counts spot nodes at the on-demand price, so for them the figure is an upper bound. The panel says how many
spot nodes there are. The figures also leave out reserved instances, savings plans, committed-use and negotiated
discounts, Windows nodes, and storage, network and control-plane charges. Prices are in USD, at 730 hours per month.

## Refreshing the snapshot

```bash
uv pip install -e '.[dev]'
python -m k8sfoam.src.pricing.snapshot            # aws azure gcp
python -m k8sfoam.src.pricing.snapshot azure      # one provider
```

If a provider fails, the command keeps that provider's previous table, so missing credentials never wipe its prices. The command exits
non-zero when any provider fails.
