import bitmath  # type: ignore
from app.common.dtos import PodResources, ContainerResources, NodeResources
from typing import Optional


class ResourcesExtractor():
    def __requests_contains_key(self, requests: dict, key: str) -> bool:
        return requests is not None and key in requests

    def __convert_to_int(self, memory: str, suffix: str) -> int:
        return int(memory.replace(suffix, ''))

    def __convert_cpu(self, cpu: str) -> Optional[int]:
        if 'm' in cpu:
            return int(cpu.replace('m', ''))
        elif 'Ki' in cpu:
            return int(cpu.replace('Ki', '')) * 1000
        else:
            return int(cpu) * 1000

    def __convert_memory(self, memory: str) -> Optional[float]:
        value = None
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
            value = bitmath.KB(self.__convert_to_int(memory, 'K')).kB
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
        return float(value) if value else None

    def extract_pod_requested_resources(self, pod) -> PodResources:
        name = pod.metadata.name
        node_name = pod.spec.node_name
        containers = []
        for container in pod.spec.containers:
            requests = container.resources.requests
            cpu = self.__convert_cpu(requests['cpu']) if self.__requests_contains_key(requests, 'cpu') else None
            memory = self.__convert_memory(requests['memory']) if self.__requests_contains_key(requests, 'memory') else None
            containers.append(ContainerResources(container.name, cpu, memory))
        cpu = sum(map(lambda c: c.cpu, filter(lambda c: c.cpu is not None, containers)))
        memory = sum(map(lambda c: c.memory, filter(lambda c: c.memory is not None, containers)))
        return PodResources(name, node_name, cpu, memory, containers)

    def extract_node_resources(self, node) -> NodeResources:
        cpu = self.__convert_cpu(node.status.capacity['cpu'])
        memory = self.__convert_memory(node.status.capacity['memory'])
        return NodeResources(node.metadata.name, cpu, memory)
