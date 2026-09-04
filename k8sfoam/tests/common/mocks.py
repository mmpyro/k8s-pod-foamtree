from unittest.mock import MagicMock


def create_pod(name: str, node_name: str, containers=[], init_containers=None,
               namespace='default', labels=None, qos_class='Burstable') -> MagicMock:
    pod = MagicMock()
    metadata = MagicMock()
    metadata.name = name
    metadata.namespace = namespace
    # labels and qos_class are None on the real API whenever they are unset,
    # so the mock has to express that instead of an always-truthy MagicMock.
    metadata.labels = labels
    spec = MagicMock()
    spec.node_name = node_name
    spec.containers = containers
    spec.init_containers = init_containers
    status = MagicMock()
    status.qos_class = qos_class
    pod.metadata = metadata
    pod.spec = spec
    pod.status = status
    return pod


def create_container(name: str, cpu: str, memory: str) -> MagicMock:
    container = MagicMock()
    resources = MagicMock()
    requests = {'cpu': cpu, 'memory': memory}
    resources.requests = requests
    container.name = name
    container.resources = resources
    return container


def create_node(name: str, cpu: str, memory: str) -> MagicMock:
    node = MagicMock()
    metadata = MagicMock()
    metadata.name = name
    node.metadata = metadata
    status = MagicMock()
    status.capacity = {'cpu': cpu, 'memory': memory}
    node.status = status
    return node
