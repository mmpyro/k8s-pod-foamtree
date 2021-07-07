from unittest.mock import MagicMock


def create_pod(name: str, node_name: str, containers=[]) -> MagicMock:
    pod = MagicMock()
    metadata = MagicMock()
    metadata.name = name
    spec = MagicMock()
    spec.node_name = node_name
    spec.containers = containers
    pod.metadata = metadata
    pod.spec = spec
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
