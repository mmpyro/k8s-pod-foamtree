from k8sfoam.src.common.dtos import NodeResources, PodResources
from k8sfoam.src.common.node_status import node_warnings
from k8sfoam.src.common.pod_audit import pod_findings
from typing import Iterator


class FoamTreeMapper():
    def __init__(self, node_resources: Iterator[NodeResources], pod_resources: list[PodResources]):
        self.__nodes = node_resources
        self.__pods = pod_resources

    def __build_pod_groups(self, pod, resource_attr: str) -> list:
        """Build FoamTree child groups for a single pod."""
        groups = []

        # Regular containers
        for c in pod.containers:
            groups.append({
                'label': c.name,
                'weight': getattr(c, resource_attr),
            })

        # Init containers (visually distinguished in grey)
        for c in pod.init_containers:
            value = getattr(c, resource_attr)
            if value > 0:
                groups.append({
                    'label': f'{c.name} (init)',
                    'weight': value,
                    'color': '#aaaaaa',
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
        }

    def __node_metadata(self, node) -> dict:
        """Health metadata added to every node group, consumed by the frontend markers.

        `warnings` is the render-ready verdict; `taints`/`conditions` are the raw
        facts the focus overlay spells out.
        """
        taints = list(node.taints or [])
        conditions = dict(node.conditions or {})
        return {
            'unschedulable': bool(node.unschedulable),
            'taints': taints,
            'conditions': conditions,
            'warnings': node_warnings(bool(node.unschedulable), taints, conditions),
        }

    def transform_cpu_resources_to_foamtree(self) -> dict:
        result: dict = {'groups': []}
        for node in self.__nodes:
            foam_pods = []
            all_pods_cpu = 0
            for pod in filter(lambda p: p.node_name == node.name, self.__pods):
                foam_pods.append({'label': pod.name, 'weight': pod.cpu, 'groups': self.__build_pod_groups(pod, 'cpu'),
                                  **self.__pod_metadata(pod, node)})
                all_pods_cpu += pod.cpu if pod.cpu else 0
            foam_pods.append({'label': 'empty', 'weight': node.cpu - all_pods_cpu, 'color': '#ffffff'})
            foam_node = {'label': node.name, 'weight': node.cpu, 'groups': foam_pods, **self.__node_metadata(node)}
            result['groups'].append(foam_node)
        return result

    def transform_memory_resources_to_foamtree(self) -> dict:
        result: dict = {'groups': []}
        for node in self.__nodes:
            foam_pods = []
            all_pods_memory = 0
            for pod in filter(lambda p: p.node_name == node.name, self.__pods):
                foam_pods.append({'label': pod.name, 'weight': pod.memory, 'groups': self.__build_pod_groups(pod, 'memory'),
                                  **self.__pod_metadata(pod, node)})
                all_pods_memory += pod.memory if pod.memory else 0
            foam_pods.append({'label': 'empty', 'weight': node.memory - all_pods_memory, 'color': '#ffffff'})
            foam_node = {'label': node.name, 'weight': node.memory, 'groups': foam_pods, **self.__node_metadata(node)}
            result['groups'].append(foam_node)
        return result
