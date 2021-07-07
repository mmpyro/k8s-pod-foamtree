from app.common.dtos import NodeResources, PodResources
from typing import List


class FoamTreeMapper():
    def __init__(self, node_resources: List[NodeResources], pod_resources: List[PodResources]):
        self.__nodes = node_resources
        self.__pods = pod_resources

    def transform_cpu_resources_to_foamtree(self) -> dict:
        result: dict = {'groups': []}
        for node in self.__nodes:
            foam_pods = []
            all_pods_cpu = 0
            for pod in filter(lambda p: p.node_name == node.name, self.__pods):
                foam_pods.append({'label': pod.name, 'weight': pod.cpu, 'groups': [*map(lambda c: {'label': c.name, 'weight': c.cpu}, pod.containers)]})
                all_pods_cpu += pod.cpu if pod.cpu else 0
            foam_pods.append({'label': 'empty', 'weight': node.cpu - all_pods_cpu, 'color': '#ffffff'})
            foam_node = {'label': node.name, 'weight': node.cpu, 'groups': foam_pods}
            result['groups'].append(foam_node)
        return result

    def transform_memory_resources_to_foamtree(self) -> dict:
        result: dict = {'groups': []}
        for node in self.__nodes:
            foam_pods = []
            all_pods_memory = 0
            for pod in filter(lambda p: p.node_name == node.name, self.__pods):
                foam_pods.append({'label': pod.name, 'weight': pod.memory, 'groups': [*map(lambda c: {'label': c.name, 'weight': c.memory}, pod.containers)]})
                all_pods_memory += pod.memory if pod.memory else 0
            foam_pods.append({'label': 'empty', 'weight': node.memory - all_pods_memory, 'color': '#ffffff'})
            foam_node = {'label': node.name, 'weight': node.memory, 'groups': foam_pods}
            result['groups'].append(foam_node)
        return result
