import math
from kubernetes.utils import parse_quantity  # type: ignore
from k8sfoam.src.common.dtos import PodResources, ContainerResources, NodeResources
from k8sfoam.src.common.node_status import PRESSURE_CONDITIONS, CORDON_TAINT


# The kubelet marks a static pod's API copy with this annotation. Such a pod is
# owned by the node itself: a drain cannot evict it and nothing reschedules it.
MIRROR_POD_ANNOTATION = 'kubernetes.io/config.mirror'


def to_millicores(cpu: str) -> int:
    """Millicores, rounded up like the scheduler's MilliValue()."""
    return math.ceil(parse_quantity(cpu) * 1000)


def to_kb(memory: str) -> float:
    """Decimal kB (1000 bytes), the unit the frontend expects."""
    return float(parse_quantity(memory) / 1000)


class ResourcesExtractor():
    def __requests_contains_key(self, requests: dict, key: str) -> bool:
        return requests is not None and key in requests

    def __convert_cpu(self, cpu: str) -> int:
        return to_millicores(cpu)

    def __convert_memory(self, memory: str) -> float:
        return to_kb(memory)

    def __extract_tolerations(self, pod) -> list:
        return [{'key': t.key, 'operator': t.operator, 'value': t.value, 'effect': t.effect}
                for t in pod.spec.tolerations or []]

    def __extract_owner_kind(self, pod):
        """Who recreates the pod after an eviction: a controller kind, 'Node' for a
        static pod, or None for a naked pod that nothing brings back."""
        if MIRROR_POD_ANNOTATION in (pod.metadata.annotations or {}):
            return 'Node'
        refs = pod.metadata.owner_references or []
        controller = next((r for r in refs if r.controller), refs[0] if refs else None)
        return controller.kind if controller is not None else None

    def __extract_container_resources(self, container) -> ContainerResources:
        requests = container.resources.requests
        cpu = self.__convert_cpu(requests['cpu']) if self.__requests_contains_key(requests, 'cpu') else 0
        memory = self.__convert_memory(requests['memory']) if self.__requests_contains_key(requests, 'memory') else 0
        # None, not 0: an unset limit means "unbounded", which is exactly what the audit flags.
        limits = container.resources.limits
        memory_limit = self.__convert_memory(limits['memory']) if self.__requests_contains_key(limits, 'memory') else None
        return ContainerResources(container.name, cpu, memory, memory_limit)

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
        sidecar_cpu = sidecar_memory = max_init_cpu = max_init_memory = 0
        for spec in pod.spec.init_containers or []:
            c = self.__extract_container_resources(spec)
            if spec.restart_policy == 'Always':
                containers.append(c)
                sidecar_cpu += c.cpu
                sidecar_memory += c.memory
                max_init_cpu = max(max_init_cpu, sidecar_cpu)
                max_init_memory = max(max_init_memory, sidecar_memory)
            else:
                init_containers.append(c)
                max_init_cpu = max(max_init_cpu, sidecar_cpu + c.cpu)
                max_init_memory = max(max_init_memory, sidecar_memory + c.memory)

        # --- Effective request (what the scheduler actually reserves) ---
        effective_cpu = max(sum(c.cpu for c in containers), max_init_cpu)
        effective_memory = max(sum(c.memory for c in containers), max_init_memory)

        return PodResources(name, node_name, effective_cpu, effective_memory, containers, init_containers,
                            namespace, labels, qos_class, dict(pod.spec.node_selector or {}),
                            self.__extract_tolerations(pod), self.__extract_owner_kind(pod))

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
        cpu = self.__convert_cpu(node.status.capacity['cpu'])
        memory = self.__convert_memory(node.status.capacity['memory'])
        unschedulable = bool(node.spec.unschedulable) if node.spec is not None else False
        # Allocatable is capacity minus what the kubelet reserves for the system,
        # and is what the scheduler actually fills. None when the node omits it.
        allocatable = node.status.allocatable or {}
        alloc_cpu = self.__convert_cpu(allocatable['cpu']) if 'cpu' in allocatable else None
        alloc_memory = self.__convert_memory(allocatable['memory']) if 'memory' in allocatable else None
        alloc_pods = int(parse_quantity(allocatable['pods'])) if 'pods' in allocatable else None
        return NodeResources(node.metadata.name, cpu, memory, unschedulable,
                             self.__extract_node_taints(node), self.__extract_node_conditions(node),
                             dict(node.metadata.labels or {}), alloc_cpu, alloc_memory, alloc_pods)
