from kubernetes import config, client  # type: ignore
from typing import List
from app.utils.extractors import ResourcesExtractor
from app.common.dtos import PodResources, NodeResources


class K8sClient():
    def __init__(self):
        config.load_kube_config()
        self.__extractor = ResourcesExtractor()

    def get_node_resources(self) -> List[NodeResources]:
        v1_client = client.CoreV1Api()
        return [*map(lambda node: self.__extractor.extract_node_resources(node), v1_client.list_node().items)]

    def get_pod_resources(self) -> List[PodResources]:
        v1_client = client.CoreV1Api()
        return [*map(lambda pod: self.__extractor.extract_pod_requested_resources(pod), v1_client.list_pod_for_all_namespaces().items)]
