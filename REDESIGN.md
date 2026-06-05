# REDESIGN: Kubernetes Pod Resource Calculation

## Problem Statement

The current implementation in [`extractors.py`](k8sfoam/src/utils/extractors.py) iterates **only over `pod.spec.containers`** (regular containers) and sums their resource requests to compute the pod-level totals:

```python
# Current code (extractors.py L50-61)
def extract_pod_requested_resources(self, pod) -> PodResources:
    ...
    for container in pod.spec.containers:
        ...
    cpu = sum(map(lambda c: c.cpu, containers))
    memory = sum(map(lambda c: c.memory, containers))
    return PodResources(name, node_name, cpu, memory, containers)
```

This has **two issues**:

### Issue 1 — Init containers are completely ignored

`pod.spec.init_containers` is never read. Init containers run before regular containers, and the Kubernetes scheduler reserves the **maximum** of any single init container's request when calculating how much resource a pod needs. Ignoring them means the visualization underreports the actual reserved resources whenever an init container requests more than the sum of regular containers.

### Issue 2 — Naive summation is incorrect even conceptually

Even if init containers were included, simply **summing all containers + all init containers** would be wrong. Kubernetes does not do that. The scheduler uses an **Effective Resource Request** formula that computes the *maximum* of two values:

```
Effective(pod) = max(
    max(each init container's request),   # init containers run one-at-a-time
    sum(all regular container requests)    # regular containers run concurrently
)
```

> **Reference**: [Kubernetes documentation — Resource Management for Pods](https://kubernetes.io/docs/concepts/configuration/manage-resources-containers/#how-pods-with-resource-requests-are-scheduled)

---

## Why It Matters

| Scenario | Current result | Correct result |
|---|---|---|
| Pod with 2 containers (100m + 200m CPU), no init containers | 300m ✅ | 300m |
| Pod with 1 container (100m CPU), 1 init container (500m CPU) | 100m ❌ | 500m |
| Pod with 2 containers (200m + 300m = 500m CPU), 1 init container (400m CPU) | 500m ✅ (by luck) | 500m |
| Pod with 2 containers (100m + 100m = 200m CPU), 1 init container (400m CPU) | 200m ❌ | 400m |

The visualization currently shows the pod as consuming 200m when the scheduler actually reserved 400m. This misleads cluster capacity planning.

---

## Proposed Redesign

### 1. Update the DTO to distinguish container types

**File**: [`dtos.py`](k8sfoam/src/common/dtos.py)

```python
from collections import namedtuple

ContainerResources = namedtuple('ContainerResources', 'name cpu memory')

PodResources = namedtuple(
    'PodResources',
    'name node_name cpu memory containers init_containers'
)

NodeResources = namedtuple('NodeResources', 'name cpu memory')
```

- Add an `init_containers` field to `PodResources` so the downstream mapper can visualize them separately if desired.
- `cpu` / `memory` at the pod level now represent the **effective** request (see formula above).

### 2. Implement the Kubernetes effective-request formula

**File**: [`extractors.py`](k8sfoam/src/utils/extractors.py)

```python
def extract_pod_requested_resources(self, pod) -> PodResources:
    name = pod.metadata.name
    node_name = pod.spec.node_name

    # --- Regular containers (run concurrently → sum) ---
    containers = []
    for container in pod.spec.containers:
        requests = container.resources.requests
        cpu = self.__convert_cpu(requests['cpu']) \
              if self.__requests_contains_key(requests, 'cpu') else 0
        memory = self.__convert_memory(requests['memory']) \
                 if self.__requests_contains_key(requests, 'memory') else 0
        containers.append(ContainerResources(container.name, cpu, memory))

    sum_regular_cpu = sum(c.cpu for c in containers)
    sum_regular_memory = sum(c.memory for c in containers)

    # --- Init containers (run sequentially → max) ---
    init_containers = []
    raw_init = pod.spec.init_containers or []
    for container in raw_init:
        requests = container.resources.requests
        cpu = self.__convert_cpu(requests['cpu']) \
              if self.__requests_contains_key(requests, 'cpu') else 0
        memory = self.__convert_memory(requests['memory']) \
                 if self.__requests_contains_key(requests, 'memory') else 0
        init_containers.append(ContainerResources(container.name, cpu, memory))

    max_init_cpu = max((c.cpu for c in init_containers), default=0)
    max_init_memory = max((c.memory for c in init_containers), default=0)

    # --- Effective request (what the scheduler actually reserves) ---
    effective_cpu = max(sum_regular_cpu, max_init_cpu)
    effective_memory = max(sum_regular_memory, max_init_memory)

    return PodResources(
        name, node_name,
        effective_cpu, effective_memory,
        containers, init_containers
    )
```

#### Key design decisions

| Decision | Rationale |
|---|---|
| `pod.spec.init_containers or []` | The field is `None` when no init containers are defined; guard against it. |
| `max(..., default=0)` | Handles the empty-list case without an extra `if`. |
| Pod-level `cpu`/`memory` = effective request | This is the number the scheduler uses and therefore the number the visualization should display to be accurate. |

### 3. Expose init containers in the visualization

**File**: [`mappers.py`](k8sfoam/src/utils/mappers.py)

The mapper already drills into `pod.containers` to build child groups.  Extend it to also show init containers — visually distinguished — so users can understand *why* a pod reserves a given amount.

```python
def __build_pod_groups(self, pod, resource_attr: str) -> list:
    """Build FoamTree child groups for a single pod."""
    groups = []

    # Regular containers
    for c in pod.containers:
        groups.append({
            'label': c.name,
            'weight': getattr(c, resource_attr),
        })

    # Init containers (only if they drove the effective request up)
    for c in pod.init_containers:
        value = getattr(c, resource_attr)
        if value > 0:
            groups.append({
                'label': f'{c.name} (init)',
                'weight': value,
                'color': '#aaaaaa',  # grey to visually distinguish
            })

    return groups
```

> **Note on weight semantics**: Init containers don't run concurrently with regular containers, so their weights are not additive. Two reasonable options:
>
> 1. **Show them but don't add to the pod weight** — keeps the treemap proportions accurate but init containers won't appear as area.
> 2. **Show the effective request as the pod weight and include init containers as children** — slightly inflates the visual size when init containers dominate, but makes the "why" transparent.
>
> **Recommendation**: Option 2 — show everything. Users can then see that a pod reserves 400m because of an init container requesting 400m, even though regular containers only ask for 200m. The treemap will size the pod at the *effective* 400m, and the children will reveal the breakdown.

### 4. Handle sidecar containers (Kubernetes 1.28+)

Starting with Kubernetes 1.28, a new **sidecar container** pattern exists via `restartPolicy: Always` on init containers. These containers behave like regular containers (they run for the pod's lifetime) but are defined under `init_containers`.

The effective resource formula for sidecar-aware scheduling is more nuanced:

```
For each init container i (processed in order):
  effective[i] = initContainer[i].requests
                 + sum(sidecar requests from init containers 0..i-1)

Effective(pod) = max(
    max(effective[i] for all init containers),
    sum(all regular containers) + sum(all sidecar init containers)
)
```

**Recommendation**: For now, implement the standard formula (Step 2 above). Add sidecar-aware calculation as a follow-up when the cluster runs Kubernetes ≥1.28. Gate it behind a version check or a configuration flag.

### 5. Update tests

**File**: [`test_extractors.py`](k8sfoam/tests/utils/test_extractors.py) — add cases:

| Test case | Init containers | Regular containers | Expected effective CPU |
|---|---|---|---|
| No init containers | — | 100m + 200m | 300m |
| Init container < regular sum | 200m | 100m + 200m | 300m |
| Init container > regular sum | 500m | 100m + 200m | 500m |
| Multiple init containers | 500m, 300m | 100m | 500m |
| No resource requests on init | (none set) | 100m | 100m |

**File**: [`mocks.py`](k8sfoam/tests/common/mocks.py) — extend `create_pod` to accept `init_containers`:

```python
def create_pod(name, node_name, containers=[], init_containers=None):
    pod = MagicMock()
    ...
    spec.containers = containers
    spec.init_containers = init_containers
    ...
    return pod
```

### 6. Summary of file changes

| File | Change |
|---|---|
| [`dtos.py`](k8sfoam/src/common/dtos.py) | Add `init_containers` field to `PodResources` |
| [`extractors.py`](k8sfoam/src/utils/extractors.py) | Read `init_containers`, compute effective request via `max(sum_regular, max_init)` |
| [`mappers.py`](k8sfoam/src/utils/mappers.py) | Extract shared helper; render init containers as grey-labelled children |
| [`mocks.py`](k8sfoam/tests/common/mocks.py) | Add `init_containers` parameter to `create_pod` |
| [`test_extractors.py`](k8sfoam/tests/utils/test_extractors.py) | Add init container test cases |
| [`test_mappers.py`](k8sfoam/tests/utils/test_mappers.py) | Update to include `init_containers` in test data |
| [`test_app.py`](k8sfoam/tests/test_app.py) | Update mock `PodResources` to include `init_containers` field |

---

## Out of Scope (future improvements)

- **Sidecar containers** (K8s 1.28+ `restartPolicy: Always` on init containers) — needs version-gated logic.
- **Limits vs Requests** — current code only looks at `.requests`; limits affect OOM-kill and throttling but not scheduling.
- **Ephemeral containers** — debug containers added at runtime; no resource scheduling impact.
- **Overhead** — `pod.spec.overhead` adds runtime-class overhead (e.g., Kata containers); could be added to effective calculation.
