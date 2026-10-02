from collections import defaultdict
from k8sfoam.src.common.dtos import NodeResources, PodResources
from k8sfoam.src.common.node_status import node_warnings, stranded_devices
from k8sfoam.src.common.pod_audit import pod_findings
from typing import Callable, Iterator


def _value_getter(resource: str) -> Callable:
    """Read one resource off any DTO: a field for cpu/memory, the extended map otherwise."""
    if resource in ('cpu', 'memory'):
        return lambda dto: getattr(dto, resource) or 0
    return lambda dto: (dto.extended or {}).get(resource, 0)


class FoamTreeMapper():
    def __init__(self, node_resources: Iterator[NodeResources], pod_resources: list[PodResources]):
        # Materialised: available_resources() and transform() both walk the nodes.
        self.__nodes = list(node_resources)
        # Grouped once: scanning every pod for every node was O(nodes x pods).
        self.__pods_by_node: dict = defaultdict(list)
        for pod in pod_resources:
            self.__pods_by_node[pod.node_name].append(pod)

    def __build_pod_groups(self, pod, value: Callable) -> list:
        """Build FoamTree child groups for a single pod."""
        groups = []

        # Regular containers
        for c in pod.containers:
            groups.append({
                'label': c.name,
                'weight': value(c),
                'extended': dict(c.extended or {}),
            })

        # Init containers (visually distinguished in grey)
        for c in pod.init_containers:
            if value(c) > 0:
                groups.append({
                    'label': f'{c.name} (init)',
                    'weight': value(c),
                    'color': '#aaaaaa',
                    'extended': dict(c.extended or {}),
                })

        return groups

    def __pod_metadata(self, pod, node) -> dict:
        """Selector and audit metadata added to every pod group, consumed by the frontend."""
        return {
            'namespace': pod.namespace,
            'labels': pod.labels or {},
            'qos': pod.qos_class,
            'hasInitContainers': len(pod.init_containers) > 0,
            'findings': pod_findings(pod, node),
            'extended': dict(pod.extended or {}),
        }

    def __node_usage(self, node) -> dict:
        """Summed pod requests on the node, for every resource the pods ask for."""
        used: dict = {}
        for pod in self.__pods_by_node.get(node.name, []):
            for name, amount in {'cpu': pod.cpu or 0, 'memory': pod.memory or 0, **(pod.extended or {})}.items():
                used[name] = used.get(name, 0) + amount
        return used

    def __node_metadata(self, node) -> dict:
        """Health metadata added to every node group, consumed by the frontend markers.

        `warnings` is the render-ready verdict; `taints`/`conditions` are the raw
        facts the focus overlay spells out.
        """
        taints = list(node.taints or [])
        conditions = dict(node.conditions or {})
        warnings = node_warnings(bool(node.unschedulable), taints, conditions)
        if stranded_devices(node, self.__node_usage(node)):
            warnings.append('stranded-devices')
        return {
            'unschedulable': bool(node.unschedulable),
            'taints': taints,
            'conditions': conditions,
            'warnings': warnings,
            'extended': dict(node.extended or {}),
        }

    def available_resources(self) -> list:
        """cpu and memory, then every extended resource some node can actually allocate."""
        extended = sorted({name for node in self.__nodes
                           for name, amount in (node.extended or {}).items() if amount > 0})
        return ['cpu', 'memory', *extended]

    def transform(self, resource: str) -> dict:
        value = _value_getter(resource)
        result: dict = {'groups': []}
        for node in self.__nodes:
            foam_pods = []
            used = 0
            for pod in self.__pods_by_node.get(node.name, []):
                foam_pods.append({'label': pod.name, 'weight': value(pod), 'groups': self.__build_pod_groups(pod, value),
                                  **self.__pod_metadata(pod, node)})
                used += value(pod)
            foam_pods.append({'label': 'empty', 'weight': value(node) - used, 'color': '#ffffff'})
            foam_node = {'label': node.name, 'weight': value(node), 'groups': foam_pods, **self.__node_metadata(node)}
            result['groups'].append(foam_node)
        return result

    def transform_cpu_resources_to_foamtree(self) -> dict:
        return self.transform('cpu')

    def transform_memory_resources_to_foamtree(self) -> dict:
        return self.transform('memory')
