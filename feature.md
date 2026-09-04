# Potential New Features & Enhancement Roadmap for k8sfoams

This document outlines potential new features, architectural enhancements, and visualization improvements for **k8sfoams** (`k8s-pod-foamtree`). The proposals are structured to build upon the tool's core strength: a fast, local, lightweight, read-only cluster capacity visualizer.

---

## Table of Contents

1. [Workload & Resource Modeling](#1-workload--resource-modeling)
2. [Alternative Grouping & Topology Hierarchies](#2-alternative-grouping--topology-hierarchies)
3. [Filtering, Search & Selection](#3-filtering-search--selection)
4. [Visualization & UX Enhancements](#4-visualization--ux-enhancements)
5. [Cluster Health, Right-Sizing & Cost Estimation](#5-cluster-health-right-sizing--cost-estimation)
6. [Simulations & "What-If" Planning](#6-simulations--what-if-planning)
7. [Deployment, Integration & Ecosystem](#7-deployment-integration--ecosystem)
8. [Feature Priority & Implementation Matrix](#8-feature-priority--implementation-matrix)

---

## 1. Workload & Resource Modeling

### 1.1 Allocatable Capacity vs. Raw Capacity
- **Description**: Kubernetes distinguishes between total hardware `status.capacity` and `status.allocatable` (which subtracts `kube-reserved`, `system-reserved`, and eviction hard thresholds).
- **Value Proposition**: The Kubernetes scheduler places pods against *allocatable* capacity, not raw capacity. Showing allocatable capacity gives a 100% accurate picture of true scheduling limits and highlights the overhead consumed by the node operating system and kubelet.
- **Technical Approach**:
  - In [extractors.py](file:///Users/mmarszalek/playground/k8s-pod-foamtree/k8sfoam/src/utils/extractors.py), extract both `node.status.capacity` and `node.status.allocatable`.
  - Add a dedicated synthetic foam block for **System/Kubelet Overhead** (`capacity - allocatable`), visually distinguished from **Free Space** (`allocatable - sum(pod_requests)`).
  - Add a toggle in the UI: `Show: Allocatable vs. Raw Capacity`.
- **Effort**: Low | **Impact**: High

### 1.2 Resource Limits & Overcommit Visualization
- **Description**: In addition to requests, pods specify resource `limits`. Visualizing limits reveals the cluster's overcommit ratio (e.g. CPU oversubscription).
- **Value Proposition**: Helps operators quickly identify "noisy neighbor" risks where a node's CPU limits exceed capacity by 300%+, or where memory limits exceed capacity (creating OOM kill risks).
- **Technical Approach**:
  - Extract `container.resources.limits` in backend DTOs.
  - In 2D mode, add a metric selector for `CPU Limit`, `Memory Limit`, and a `Limits vs Requests Ratio` heatmap.
  - In node cards, display the overcommit badge: e.g., `CPU Limits: 240% (Overcommitted)`.
- **Effort**: Low | **Impact**: High

### 1.3 Extended Resources (GPUs, Ephemeral Storage, HugePages)
- **Description**: Support specialized hardware resources such as `nvidia.com/gpu`, `amd.com/gpu`, `ephemeral-storage`, and HugePages.
- **Value Proposition**: AI/ML and big-data workloads heavily rely on GPU scheduling and disk quotas. SREs running LLM/training clusters need to see GPU fragmentation across nodes.
- **Technical Approach**:
  - Inspect `status.allocatable` keys on nodes and container requests for non-CPU/memory resource types.
  - Dynamically populate the `Resource` metric selector dropdown with detected resources (e.g. `GPU`, `Ephemeral Storage`).
- **Effort**: Medium | **Impact**: High

### 1.4 Optional Live Utilization Overlay (via Metrics Server / Prometheus)
- **Description**: Allow an optional live usage overlay comparing actual consumption against requested reservations.
- **Value Proposition**: Visualizes the "Kubernetes Wastage Gap" — pods that requested 8 CPU cores but are only using 150m, enabling immediate cost-saving opportunities.
- **Technical Approach**:
  - Add optional endpoint `/resources/metrics` querying `metrics.k8s.io` (Metrics Server).
  - Show a fill percentage inside pod rectangles or dual-color bars indicating `Used / Requested`.
  - Keep the tool fully functional when Metrics Server is absent (graceful fallback to pure requests).
- **Effort**: Medium | **Impact**: High

---

## 2. Alternative Grouping & Topology Hierarchies

Currently, k8sfoams hardcodes the hierarchy as: `Cluster -> Node -> Pod -> Container`. Adding flexible grouping modes would unlock multiple new perspectives.

```
Current:  [ Cluster ] ──> [ Node ] ──> [ Pod ] ──> [ Container ]
New:      [ Cluster ] ──> [ Namespace ] ──> [ Deployment / StatefulSet ] ──> [ Pod ]
          [ Cluster ] ──> [ Zone / Region ] ──> [ Node Pool ] ──> [ Node ] ──> [ Pod ]
          [ Cluster ] ──> [ QoS Class (Guaranteed / Burstable / BestEffort) ]
```

### 2.1 Namespace & Workload-First Grouping
- **Description**: Group resources by Namespace, and then by owning Controller (Deployment, StatefulSet, DaemonSet, Job).
- **Value Proposition**: Multi-tenant teams and platform leads can instantly see which namespace, team, or application consumes the largest share of cluster capacity.
- **Technical Approach**:
  - In [extractors.py](file:///Users/mmarszalek/playground/k8s-pod-foamtree/k8sfoam/src/utils/extractors.py), extract `pod.metadata.namespace` and `pod.metadata.owner_references`.
  - Provide a `Group By` selector: `Node (Default) | Namespace | Controller`.
- **Effort**: Medium | **Impact**: High

### 2.2 Multi-AZ & Node Pool Topology
- **Description**: Group nodes by Availability Zone (`topology.kubernetes.io/zone`), Region, or Cloud Provider Node Pool / Machine Type (e.g. Karpenter node pools, EKS managed node groups).
- **Value Proposition**: Helps diagnose multi-AZ capacity imbalances, spot instances vs. on-demand distribution, and cross-zone traffic costs.
- **Technical Approach**:
  - Read node labels (`node.kubernetes.io/instance-type`, `topology.kubernetes.io/zone`, `karpenter.sh/nodepool`).
  - Render an outer container bounding box per AZ or node pool in 2D and 3D scenes.
- **Effort**: Medium | **Impact**: Medium

### 2.3 Quality of Service (QoS) & Eviction Risk Breakdown
- **Description**: Categorize and color-code pods by their Kubernetes QoS class: `Guaranteed`, `Burstable`, or `BestEffort`.
- **Value Proposition**: SREs can see where `BestEffort` pods reside (which are the first to be evicted under memory pressure) and where unconstrained workloads exist.
- **Technical Approach**:
  - Classify pods based on container requests and limits into QoS classes.
  - Add a color scheme / filter mode for QoS classes.
- **Effort**: Low | **Impact**: Medium

---

## 3. Filtering, Search & Selection

### 3.1 Advanced Query Bar & Label Selectors
- **Description**: Expand the node search bar into a rich filter bar supporting Kubernetes selectors:
  - `ns:kube-system` (filter by namespace)
  - `app=frontend` or `env=prod` (label selectors)
  - `node:worker-*` (node name wildcards)
  - `qos:BestEffort`
  - `has:init-containers`
- **Value Proposition**: Enables precision filtering in large clusters containing hundreds of nodes and thousands of pods.
- **Technical Approach**:
  - Implement a client-side parser in [app.jsx](file:///Users/mmarszalek/playground/k8s-pod-foamtree/k8sfoam/src/frontend/app.jsx) for key-value tokens.
  - Highlight matched pods while dimming unmatched ones instead of completely removing nodes.
- **Effort**: Medium | **Impact**: High

### 3.2 Cross-Node Workload Highlighting
- **Description**: Clicking a pod or selecting a deployment highlights all replicas of that deployment across all nodes in the cluster.
- **Value Proposition**: Instantly reveals replica distribution and anti-affinity compliance (e.g. confirming if replicas are safely spread across distinct nodes).
- **Technical Approach**:
  - On hover or selection of a pod, extract its base owner prefix / deployment name.
  - Apply an active highlight glow effect to all peer pods across all node cards and 3D plates.
- **Effort**: Low | **Impact**: High

---

## 4. Visualization & UX Enhancements

### 4.1 Enhanced 3D Controls (Orbit, Pan, Tilt with Three.js / WebGL)
- **Description**: Upgrade the pure CSS 3D view to an optional Three.js / WebGL canvas with full camera controls (orbit, pan, tilt, perspective toggle, and smooth focal zoom).
- **Value Proposition**: Improves rendering performance on clusters with 50+ nodes and allows full 360-degree rotation and inspection of dense pod clusters.
- **Technical Approach**:
  - Introduce an optional WebGL-backed scene component using Three.js (via CDN).
  - Add interactive floor grid coordinates, hover tooltips anchored in 3D world space, and directional lighting.
- **Effort**: High | **Impact**: High

### 4.2 Historical Playback & Snapshot Comparison
- **Description**: Save snapshots of cluster state and allow users to scrub backwards in time to see capacity changes, or compare two kube contexts side-by-side (diff view).
- **Value Proposition**: Ideal for post-mortems (e.g. analyzing cluster state during a deployment or incident) and capacity drift analysis.
- **Technical Approach**:
  - Add a local in-memory/indexedDB snapshot recorder in the frontend.
  - Implement a diff visualizer highlighting added pods (green), removed pods (red), and resized workloads (amber).
- **Effort**: High | **Impact**: Medium

### 4.3 Node Pressure, Taints & Unschedulable Indicators
- **Description**: Visually mark nodes that are `Cordoned` (`spec.unschedulable: true`), under `MemoryPressure`, `DiskPressure`, or tainted (e.g. `CriticalAddonsOnly`, `GPU-only`).
- **Value Proposition**: Prevents confusion when a node appears to have free space but cannot accept new pods.
- **Technical Approach**:
  - Extract `node.spec.taints`, `node.spec.unschedulable`, and `node.status.conditions`.
  - Render diagonal warning stripes or status badges over the empty capacity foam of cordoned/tainted nodes.
- **Effort**: Low | **Impact**: High

### 4.4 Dark/Light Theme & Accessible Color Palettes
- **Description**: Support dark/light mode toggle and colorblind-accessible palettes (e.g. Viridis, ColorBrewer, High-Contrast).
- **Value Proposition**: Meets accessibility standards and caters to varied presentation/work environments.
- **Technical Approach**:
  - Extend the existing `tweaks-panel.jsx` color scheme options with WCAG-compliant presets.
- **Effort**: Low | **Impact**: Medium

---

## 5. Cluster Health, Right-Sizing & Cost Estimation

### 5.1 Right-Sizing & "Bad Practice" Spotter
- **Description**: Automatic rule-based badges flagging workload misconfigurations:
  - ⚠️ **Missing Requests**: Pods running with 0 requested CPU/memory (cluster hygiene risk).
  - ⚠️ **Missing Limits**: Pods with no memory limit (runaway memory leak risk).
  - ⚠️ **Single Container Monolith**: Overly large single pods taking up >80% of node capacity.
  - ⚠️ **Severe Ratio Asymmetry**: CPU:Memory request ratios drastically different from node capacity ratios (causing stranded capacity).
- **Value Proposition**: Acts as an automated advisor for cluster efficiency and reliability best practices.
- **Technical Approach**:
  - Add a client-side or backend analytics pass over parsed pods.
  - Display an "Audit & Hygiene" summary panel in the sidebar.
- **Effort**: Medium | **Impact**: High

### 5.2 Estimated Cloud Cost & Waste Breakdown
- **Description**: Associate node instance types (e.g., `m5.2xlarge`, `e2-standard-8`) with public cloud hourly rates (AWS, GCP, Azure) to calculate dollar costs per node, pod, and wasted empty space.
- **Value Proposition**: Translates abstract millicores and GiB into actionable monthly budget figures for engineering managers and FinOps teams.
- **Technical Approach**:
  - Bundle a lightweight static pricing dictionary for standard cloud instance types.
  - Show cost breakdown: e.g. `$420/mo used vs. $180/mo unallocated headroom`.
- **Effort**: Medium | **Impact**: High

---

## 6. Simulations & "What-If" Planning

### 6.1 "Can I Fit This Pod?" (Dry-Run Scheduler Simulator)
- **Description**: An interactive modal where users enter a hypothetical pod spec (e.g. `CPU: 4000m, Memory: 16GiB, NodeSelector: ...`) and k8sfoams highlights which nodes can schedule it.
- **Value Proposition**: Solves the common question: *"Do we have room for our new release without triggering a cluster auto-scaler scale-up?"*
- **Technical Approach**:
  - Emulate the basic Kubernetes scheduler filter phase (evaluating free allocatable CPU, memory, and simple node selectors).
  - Highlight qualifying nodes in green and disqualified nodes in red with the reason (e.g. `Insufficient CPU: requires 4000m, available 1200m`).
- **Effort**: Medium | **Impact**: High

### 6.2 Node Drain / Failure Impact Simulator
- **Description**: Click "Simulate Drain" on any node to test what happens if that node is removed: can all of its pods fit onto the remaining nodes?
- **Value Proposition**: Invaluable for preparing maintenance windows, node pool upgrades, and disaster recovery planning.
- **Technical Approach**:
  - Run a bin-packing algorithm in the frontend against the remaining nodes' free capacity.
  - Report any pods that would become `Pending` due to lack of cluster headroom.
- **Effort**: High | **Impact**: High

---

## 7. Deployment, Integration & Ecosystem

### 7.1 Single-Binary & Containerized Read-Only In-Cluster Service
- **Description**: Package k8sfoams as a lightweight Docker container / Helm chart with `InClusterConfig` support and read-only RBAC.
- **Value Proposition**: Teams can host k8sfoams inside their internal developer portals or staging clusters without requiring local kubeconfig setup.
- **Technical Approach**:
  - In [k8s_client.py](file:///Users/mmarszalek/playground/k8s-pod-foamtree/k8sfoam/src/k8s/k8s_client.py), support `config.load_incluster_config()` when running in a pod.
  - Provide a production Dockerfile and Helm chart.
- **Effort**: Low | **Impact**: High

### 7.2 Static Report & Image Export (PNG / SVG / PDF)
- **Description**: Add an export button in the header to capture high-resolution SVG or PNG exports of the 2D treemap and 3D visualizer, or download a structured JSON/CSV report.
- **Value Proposition**: Facilitates embedding visuals in capacity planning decks, Slack updates, and incident reports.
- **Technical Approach**:
  - Use HTML5 Canvas / SVG serialization (`html2canvas` or native SVG capture) to trigger one-click image downloads.
- **Effort**: Low | **Impact**: Medium

### 7.3 Multi-Cluster Aggregate Dashboard
- **Description**: Provide a multi-cluster overview tab that aggregates totals across all configured kubeconfig contexts.
- **Value Proposition**: Platform engineering teams managing fleet clusters (dev, staging, prod, multi-region) can view aggregate capacity and spot overloaded clusters from a single pane of glass.
- **Technical Approach**:
  - Add backend endpoint `/resources/all-contexts` or allow client-side batch querying of multiple contexts.
  - Render a cluster-level treemap where each box is an entire cluster.
- **Effort**: High | **Impact**: Medium

---

## 8. Feature Priority & Implementation Matrix

| Phase | Feature | Complexity | Impact | Primary Component |
|---|---|---|---|---|
| **Phase 1: Quick Wins** | **Allocatable vs. Raw Capacity** | Low | High | Backend / DTOs / Treemap |
| | **Cordoned, Tainted & Node Conditions Display** | Low | High | Backend / UI Badges |
| | **Cross-Node Workload Highlighting** | Low | High | Frontend ([app.jsx](file:///Users/mmarszalek/playground/k8s-pod-foamtree/k8sfoam/src/frontend/app.jsx)) |
| | **Resource Limits & Overcommit Ratio** | Low | High | Backend / UI Badges |
| | **Image / SVG Export** | Low | Medium | Frontend Header |
| **Phase 2: Core Enhancements** | **Namespace & Workload Hierarchy Grouping** | Medium | High | Backend Mappers / Treemap |
| | **Advanced Query Bar & Label Selectors** | Medium | High | Frontend Search Engine |
| | **Extended Resources (GPUs, Storage)** | Medium | High | Backend Extractors / UI |
| | **"Can I Fit This Pod?" Scheduler Simulator** | Medium | High | Frontend Simulator Engine |
| | **Missing Requests & Hygiene Best-Practice Spotter** | Medium | High | Frontend Sidebar |
| | **Containerized In-Cluster Mode & Helm Chart** | Low | High | Dockerfile / Helm / Client |
| **Phase 3: Advanced Innovations** | **Node Drain / Failure Impact Simulator** | High | High | Frontend Bin-Packing Engine |
| | **Optional Live Metrics Overlay (Metrics-Server)** | Medium | High | Backend API / Frontend |
| | **FinOps Cost & Idle Headroom Estimator** | Medium | High | Frontend Pricing Map |
| | **Three.js WebGL Orbit 3D Visualizer** | High | High | Frontend ([cube3d.jsx](file:///Users/mmarszalek/playground/k8s-pod-foamtree/k8sfoam/src/frontend/cube3d.jsx)) |
| | **Multi-Cluster Fleet View** | High | Medium | Full-Stack |
