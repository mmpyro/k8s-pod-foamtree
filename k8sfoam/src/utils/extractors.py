import bitmath  # type: ignore
from k8sfoam.src.common.dtos import PodResources, ContainerResources, NodeResources
from typing import Optional, Dict

# Keys that are handled via dedicated CPU / memory code paths and must be
# excluded from the generic extended-resource sweep.
_CORE_RESOURCES = frozenset({'cpu', 'memory'})

# Resources whose quantity is a storage size string that must be normalised to
# decimal kB (same unit used for memory throughout the codebase).
_STORAGE_RESOURCES = frozenset({'ephemeral-storage', 'hugepages-1Gi', 'hugepages-2Mi'})


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

    def __convert_extended_value(self, key: str, raw: str) -> float:
        """Convert an extended resource quantity to a canonical numeric value.

        Storage-type resources (ephemeral-storage, hugepages-*) are converted
        to decimal kB so they are on the same scale as memory values elsewhere
        in the codebase.  All other extended resources (GPU count, TPU count,
        etc.) are bare integers — Kubernetes requires them to be whole numbers.
        """
        if key in _STORAGE_RESOURCES:
            return self.__convert_memory(raw)
        # Countable resources: strip any trailing 'm' that shouldn't appear
        # in practice for GPUs, but handle gracefully just in case.
        try:
            return float(int(raw))
        except ValueError:
            return 0.0

    def __extract_extended_resources(self, requests: Optional[dict]) -> Dict[str, float]:
        """Return a dict of all non-cpu/memory extended resource requests."""
        if requests is None:
            return {}
        return {
            key: self.__convert_extended_value(key, str(val))
            for key, val in requests.items()
            if key not in _CORE_RESOURCES
        }

    def __extract_container_resources(self, container) -> ContainerResources:
        requests = container.resources.requests
        cpu = self.__convert_cpu(requests['cpu']) if self.__requests_contains_key(requests, 'cpu') else 0
        memory = self.__convert_memory(requests['memory']) if self.__requests_contains_key(requests, 'memory') else 0
        extended = self.__extract_extended_resources(requests)
        return ContainerResources(container.name, cpu, memory, extended)

    def extract_pod_requested_resources(self, pod) -> PodResources:
        name = pod.metadata.name
        node_name = pod.spec.node_name

        # --- Regular containers (run concurrently → sum) ---
        containers = [self.__extract_container_resources(c) for c in pod.spec.containers]
        sum_regular_cpu = sum(c.cpu for c in containers)
        sum_regular_memory = sum(c.memory for c in containers)

        # Sum extended resources across regular containers.
        sum_regular_extended: Dict[str, float] = {}
        for c in containers:
            for key, val in c.extended.items():
                sum_regular_extended[key] = sum_regular_extended.get(key, 0.0) + val

        # --- Init containers (run sequentially → max) ---
        init_containers = [self.__extract_container_resources(c) for c in (pod.spec.init_containers or [])]
        max_init_cpu = max((c.cpu for c in init_containers), default=0)
        max_init_memory = max((c.memory for c in init_containers), default=0)

        # For extended resources init containers also run sequentially, so for
        # each key we take max across all init containers.
        max_init_extended: Dict[str, float] = {}
        for c in init_containers:
            for key, val in c.extended.items():
                if val > max_init_extended.get(key, 0.0):
                    max_init_extended[key] = val

        # --- Effective request (what the scheduler actually reserves) ---
        effective_cpu = max(sum_regular_cpu, max_init_cpu)
        effective_memory = max(sum_regular_memory, max_init_memory)

        # Collect all extended resource keys from both regular and init sets,
        # then apply the same max(sum_regular, max_init) rule per key.
        all_ext_keys = set(sum_regular_extended) | set(max_init_extended)
        effective_extended: Dict[str, float] = {
            key: max(sum_regular_extended.get(key, 0.0), max_init_extended.get(key, 0.0))
            for key in all_ext_keys
        }

        return PodResources(name, node_name, effective_cpu, effective_memory,
                            containers, init_containers, effective_extended)

    def extract_node_resources(self, node) -> NodeResources:
        cpu = self.__convert_cpu(node.status.capacity['cpu'])
        memory = self.__convert_memory(node.status.capacity['memory'])
        # Extended resources (GPUs, TPUs, etc.) are only advertised via
        # `allocatable`, not `capacity`.  Fall back to `capacity` if
        # `allocatable` is absent (e.g. in unit tests or minimal stubs).
        allocatable = getattr(node.status, 'allocatable', None) or node.status.capacity
        extended = self.__extract_extended_resources(dict(allocatable))
        # Strip the core resources from the node extended dict — they are
        # handled by dedicated fields.
        extended = {k: v for k, v in extended.items() if k not in _CORE_RESOURCES}
        return NodeResources(node.metadata.name, cpu, memory, extended)
