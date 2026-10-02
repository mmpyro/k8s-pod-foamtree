from k8sfoam.src.common.dtos import PodResources, ContainerResources, NodeResources
from k8sfoam.src.common.node_status import PRESSURE_CONDITIONS, CORDON_TAINT
from k8sfoam.src.common.resources import convert_cpu, convert_memory, extract_extended


def _add(a: dict, b: dict) -> dict:
    return {k: a.get(k, 0) + b.get(k, 0) for k in a.keys() | b.keys()}


def _max(a: dict, b: dict) -> dict:
    return {k: max(a.get(k, 0), b.get(k, 0)) for k in a.keys() | b.keys()}


def _totals(c: ContainerResources) -> dict:
    """Every resource a container requests, keyed by name, for the effective-request rule."""
    return {'cpu': c.cpu, 'memory': c.memory, **(c.extended or {})}


class ResourcesExtractor():
    def __requests_contains_key(self, requests: dict, key: str) -> bool:
        return requests is not None and key in requests

    def __extract_container_resources(self, container) -> ContainerResources:
        requests = container.resources.requests
        cpu = convert_cpu(requests['cpu']) if self.__requests_contains_key(requests, 'cpu') else 0
        memory = convert_memory(requests['memory']) if self.__requests_contains_key(requests, 'memory') else 0
        # None, not 0: an unset limit means "unbounded", which is exactly what the audit flags.
        limits = container.resources.limits
        memory_limit = convert_memory(limits['memory']) if self.__requests_contains_key(limits, 'memory') else None
        # Extended resources may be set as limits only; the request then defaults to the limit.
        extended = {**extract_extended(limits), **extract_extended(requests)}
        return ContainerResources(container.name, cpu, memory, memory_limit, extended)

    def extract_pod_requested_resources(self, pod) -> PodResources:
        name = pod.metadata.name
        node_name = pod.spec.node_name
        namespace = pod.metadata.namespace

        # Both are None on the API whenever unset — labels normalise to an
        # empty dict, while QoS stays None because it must never be guessed.
        labels = pod.metadata.labels or {}
        qos_class = pod.status.qos_class if pod.status is not None else None

        # --- Regular containers (run concurrently → sum) ---
        containers = [self.__extract_container_resources(c) for c in pod.spec.containers]

        # --- Init containers (run sequentially → max) ---
        # Native sidecars (restartPolicy: Always) start in order but keep running,
        # so they join the regular containers and add to every init container
        # started after them. Same rule as the scheduler's PodRequests.
        init_containers = []
        sidecars: dict = {}
        max_init: dict = {}
        for spec in pod.spec.init_containers or []:
            c = self.__extract_container_resources(spec)
            if spec.restart_policy == 'Always':
                containers.append(c)
                sidecars = _add(sidecars, _totals(c))
                max_init = _max(max_init, sidecars)
            else:
                init_containers.append(c)
                max_init = _max(max_init, _add(sidecars, _totals(c)))

        # --- Effective request (what the scheduler actually reserves) ---
        regular: dict = {}
        for c in containers:
            regular = _add(regular, _totals(c))
        effective = _max(regular, max_init)
        effective_cpu = effective.get('cpu', 0)
        effective_memory = effective.get('memory', 0)
        extended = {k: v for k, v in effective.items() if k not in ('cpu', 'memory') and v}

        return PodResources(name, node_name, effective_cpu, effective_memory, containers, init_containers,
                            namespace, labels, qos_class, extended)

    def __extract_node_taints(self, node) -> list:
        """Every taint on the node, minus the one Kubernetes adds on cordon.

        PreferNoSchedule is kept: it does not mark the node, but the detail view
        still lists it.
        """
        taints = node.spec.taints or [] if node.spec is not None else []
        return [{'key': t.key, 'value': t.value, 'effect': t.effect}
                for t in taints if t.key != CORDON_TAINT]

    def __extract_node_conditions(self, node) -> dict:
        """Scheduling-relevant conditions, flattened to booleans."""
        conditions = node.status.conditions or [] if node.status is not None else []
        status_by_type = {c.type: c.status for c in conditions}
        result = {name: status_by_type.get(name) == 'True' for name in PRESSURE_CONDITIONS}
        # 'Unknown' means the API server stopped hearing from the kubelet, which
        # is not ready. A missing Ready key means the node reported no conditions
        # at all, so default to healthy rather than invent an outage.
        result['Ready'] = status_by_type.get('Ready', 'True') == 'True'
        return result

    def extract_node_resources(self, node) -> NodeResources:
        # Allocatable, not capacity: it excludes kube/system-reserved, so it is what
        # the scheduler can actually place pods into.
        allocatable = node.status.allocatable
        cpu = convert_cpu(allocatable['cpu'])
        memory = convert_memory(allocatable['memory'])
        unschedulable = bool(node.spec.unschedulable) if node.spec is not None else False
        # None on the API when unset, like a pod's labels.
        labels = node.metadata.labels or {}
        return NodeResources(node.metadata.name, cpu, memory, unschedulable,
                             self.__extract_node_taints(node), self.__extract_node_conditions(node),
                             extract_extended(allocatable), labels)
