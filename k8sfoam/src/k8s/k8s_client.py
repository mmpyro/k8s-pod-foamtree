from functools import cache
from kubernetes import config, client  # type: ignore
from typing import List, Dict, Any, Optional, Iterator, Callable, Tuple
from k8sfoam.src.utils.extractors import ResourcesExtractor
from k8sfoam.src.common.dtos import PodResources, NodeResources

DEFAULT_PAGE_SIZE = 500
# Terminated pods (Succeeded/Failed) keep their requests in the API
# but no longer reserve node resources, so exclude them.
ACTIVE_POD_SELECTOR = 'status.phase!=Succeeded,status.phase!=Failed'


@cache
def core_v1(context: Optional[str]) -> Any:
    """One API client per kubeconfig context, built on first use.

    Loading the kubeconfig runs its exec plugin (e.g. `aws eks get-token`), which
    can take longer than the API calls themselves, so it must not happen per request.
    The client re-runs the plugin by itself once the token it returned expires.
    Kubeconfig edits to a context already in use apply after a restart.
    """
    return client.CoreV1Api(config.new_client_from_config(context=context))


def list_all(func: Callable[..., Any], page_size: int, **kwargs) -> Tuple[List[Any], Optional[str]]:
    """Every item of a LIST call, fetched `page_size` at a time.

    All pages come from one consistent snapshot, whose resourceVersion is
    returned too so a WATCH can resume exactly where the list ended.
    """
    items: List[Any] = []
    token = None
    while True:
        page = func(limit=page_size, _continue=token, **kwargs)
        items.extend(page.items)
        token = page.metadata._continue
        if not token:
            return items, page.metadata.resource_version


class K8sClient():
    def __init__(self, current_context: Optional[str] = None, page_size: int = DEFAULT_PAGE_SIZE):
        self.__current_context = current_context
        self.__page_size = page_size
        self.__extractor = ResourcesExtractor()

    def get_node_resources(self) -> Iterator[NodeResources]:
        v1_client = core_v1(self.__current_context)
        nodes, _ = list_all(v1_client.list_node, self.__page_size)
        return map(lambda node: self.__extractor.extract_node_resources(node), nodes)

    def get_pod_resources(self) -> Iterator[PodResources]:
        v1_client = core_v1(self.__current_context)
        pods, _ = list_all(v1_client.list_pod_for_all_namespaces, self.__page_size, field_selector=ACTIVE_POD_SELECTOR)
        return map(lambda pod: self.__extractor.extract_pod_requested_resources(pod), pods)

    def get_contexts(slef) -> List[Dict[str, Any]]:
        contexts, active = config.list_kube_config_contexts()
        return [{'context': context['name'], 'active': True if active['name'] == context['name'] else False} for context in contexts]

    @property
    def current_context(self) -> Optional[str]:
        return self.__current_context

    @current_context.setter
    def current_context(self, value: str) -> None:
        self.__current_context = value
