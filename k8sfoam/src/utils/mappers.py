from k8sfoam.src.common.dtos import NodeResources, PodResources
from typing import Iterator, List


class FoamTreeMapper():
    def __init__(self, node_resources: Iterator[NodeResources], pod_resources: list[PodResources]):
        self.__nodes = list(node_resources)
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

    def __build_pod_groups_extended(self, pod, resource_key: str) -> list:
        """Build FoamTree child groups for extended resources (GPU, TPU, etc.)."""
        groups = []

        for c in pod.containers:
            value = c.extended.get(resource_key, 0)
            if value > 0:
                groups.append({'label': c.name, 'weight': value})

        for c in pod.init_containers:
            value = c.extended.get(resource_key, 0)
            if value > 0:
                groups.append({'label': f'{c.name} (init)', 'weight': value, 'color': '#aaaaaa'})

        return groups

    def transform_cpu_resources_to_foamtree(self) -> dict:
        result: dict = {'groups': []}
        for node in self.__nodes:
            foam_pods = []
            all_pods_cpu = 0
            for pod in filter(lambda p: p.node_name == node.name, self.__pods):
                foam_pods.append({'label': pod.name, 'weight': pod.cpu, 'groups': self.__build_pod_groups(pod, 'cpu')})
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
                foam_pods.append({'label': pod.name, 'weight': pod.memory, 'groups': self.__build_pod_groups(pod, 'memory')})
                all_pods_memory += pod.memory if pod.memory else 0
            foam_pods.append({'label': 'empty', 'weight': node.memory - all_pods_memory, 'color': '#ffffff'})
            foam_node = {'label': node.name, 'weight': node.memory, 'groups': foam_pods}
            result['groups'].append(foam_node)
        return result

    def transform_extended_resources_to_foamtree(self, resource_key: str) -> dict:
        """Build a FoamTree for any extended Kubernetes resource (GPU, TPU, ephemeral-storage, etc.).

        Nodes without the resource are given a capacity of 0 and will appear
        empty in the visualisation.  If *no* node advertises this resource the
        response still has the correct structure — the frontend can handle it.
        """
        result: dict = {'groups': []}
        for node in self.__nodes:
            node_capacity = node.extended.get(resource_key, 0)
            foam_pods = []
            all_pods_used = 0.0
            for pod in filter(lambda p: p.node_name == node.name, self.__pods):
                pod_value = pod.extended.get(resource_key, 0)
                if pod_value > 0:
                    foam_pods.append({
                        'label': pod.name,
                        'weight': pod_value,
                        'groups': self.__build_pod_groups_extended(pod, resource_key),
                    })
                all_pods_used += pod_value

            # Represent unused capacity on this node (may be 0 for nodes that
            # don't have this resource at all).
            free = max(0.0, node_capacity - all_pods_used)
            foam_pods.append({'label': 'empty', 'weight': free, 'color': '#ffffff'})

            # Use max(capacity, used) as the node weight so nodes that are
            # over-committed still render correctly.
            node_weight = max(node_capacity, all_pods_used)
            foam_node = {'label': node.name, 'weight': node_weight, 'groups': foam_pods}
            result['groups'].append(foam_node)
        return result

    def get_extended_resource_keys(self) -> List[str]:
        """Return a sorted, de-duplicated list of all extended resource keys
        present across nodes and pods in this dataset.

        Only keys with a positive quantity on at least one node or pod are
        included, so the frontend never shows a resource type that doesn't
        exist in the cluster.
        """
        keys: set = set()
        for node in self.__nodes:
            for key, val in node.extended.items():
                if val > 0:
                    keys.add(key)
        for pod in self.__pods:
            for key, val in pod.extended.items():
                if val > 0:
                    keys.add(key)
        return sorted(keys)
