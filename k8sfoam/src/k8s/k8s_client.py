from kubernetes import config, client  # type: ignore
from typing import List, Dict, Any, Optional, Iterator
from k8sfoam.src.utils.extractors import ResourcesExtractor
from k8sfoam.src.common.dtos import PodResources, NodeResources


class K8sClient():
    def __init__(self, current_context: Optional[str] = None):
        config.load_kube_config(context=current_context)
        self.__current_context = current_context
        self.__extractor = ResourcesExtractor()

    def get_node_resources(self) -> Iterator[NodeResources]:
        v1_client = client.CoreV1Api()
        return map(lambda node: self.__extractor.extract_node_resources(node), v1_client.list_node().items)

    def get_pod_resources(self) -> Iterator[PodResources]:
        v1_client = client.CoreV1Api()
        return map(lambda pod: self.__extractor.extract_pod_requested_resources(pod), v1_client.list_pod_for_all_namespaces().items)

    def get_contexts(slef) -> List[Dict[str, Any]]:
        contexts, active = config.list_kube_config_contexts()
        return [{'context': context['name'], 'active': True if active['name'] == context['name'] else False} for context in contexts]

    @property
    def current_context(self) -> Optional[str]:
        return self.__current_context

    @current_context.setter
    def current_context(self, value: str) -> None:
        self.__current_context = value
        config.load_kube_config(context=self.__current_context)
