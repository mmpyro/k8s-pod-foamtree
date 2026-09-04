import bitmath  # type: ignore
from k8sfoam.src.common.dtos import PodResources, ContainerResources, NodeResources
from k8sfoam.src.common.node_status import PRESSURE_CONDITIONS, CORDON_TAINT
from typing import Optional


class ResourcesExtractor():
    def __requests_contains_key(self, requests: dict, key: str) -> bool:
        return requests is not None and key in requests

    def __convert_to_int(self, memory: str, suffix: str) -> int:
        return int(memory.replace(suffix, ''))

    def __convert_cpu(self, cpu: str) -> Optional[int]:
        if 'm' in cpu:
            return int(cpu.replace('m', ''))
        else:
            return int(float(cpu) * 1000)

    def __convert_memory(self, memory: str) -> float:
        value = 0
        if 'Ki' in memory:
            value = bitmath.KiB(self.__convert_to_int(memory, 'Ki')).kB
        elif 'Mi' in memory:
            value = bitmath.MiB(self.__convert_to_int(memory, 'Mi')).kB
        elif 'Gi' in memory:
            value = bitmath.GiB(self.__convert_to_int(memory, 'Gi')).kB
        elif 'Ti' in memory:
            value = bitmath.TiB(self.__convert_to_int(memory, 'Ti')).kB
        elif 'Pi' in memory:
            value = bitmath.PiB(self.__convert_to_int(memory, 'Pi')).kB
        elif 'Ei' in memory:
            value = bitmath.EiB(self.__convert_to_int(memory, 'Ei')).kB
        elif 'K' in memory:
            value = bitmath.kB(self.__convert_to_int(memory, 'K')).kB
        elif 'M' in memory:
            value = bitmath.MB(self.__convert_to_int(memory, 'M')).kB
        elif 'G' in memory:
            value = bitmath.GB(self.__convert_to_int(memory, 'G')).kB
        elif 'T' in memory:
            value = bitmath.TB(self.__convert_to_int(memory, 'T')).kB
        elif 'P' in memory:
            value = bitmath.PB(self.__convert_to_int(memory, 'P')).kB
        elif 'E' in memory:
            value = bitmath.EB(self.__convert_to_int(memory, 'E')).kB
        else:
            # Assume bytes if no suffix
            value = bitmath.Byte(float(memory)).kB
        return float(value)

    def __extract_container_resources(self, container) -> ContainerResources:
        requests = container.resources.requests
        cpu = self.__convert_cpu(requests['cpu']) if self.__requests_contains_key(requests, 'cpu') else 0
        memory = self.__convert_memory(requests['memory']) if self.__requests_contains_key(requests, 'memory') else 0
        return ContainerResources(container.name, cpu, memory)

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
        sum_regular_cpu = sum(c.cpu for c in containers)
        sum_regular_memory = sum(c.memory for c in containers)

        # --- Init containers (run sequentially → max) ---
        init_containers = [self.__extract_container_resources(c) for c in (pod.spec.init_containers or [])]
        max_init_cpu = max((c.cpu for c in init_containers), default=0)
        max_init_memory = max((c.memory for c in init_containers), default=0)

        # --- Effective request (what the scheduler actually reserves) ---
        effective_cpu = max(sum_regular_cpu, max_init_cpu)
        effective_memory = max(sum_regular_memory, max_init_memory)

        return PodResources(name, node_name, effective_cpu, effective_memory, containers, init_containers,
                            namespace, labels, qos_class)

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
        return NodeResources(node.metadata.name, cpu, memory, unschedulable,
                             self.__extract_node_taints(node), self.__extract_node_conditions(node))
