from unittest.mock import MagicMock


def create_pod(name: str, node_name: str, containers=[], init_containers=None) -> MagicMock:
    pod = MagicMock()
    metadata = MagicMock()
    metadata.name = name
    spec = MagicMock()
    spec.node_name = node_name
    spec.containers = containers
    spec.init_containers = init_containers
    pod.metadata = metadata
    pod.spec = spec
    return pod


def create_container(name: str, cpu: str, memory: str, extended: dict = {}) -> MagicMock:
    """Create a mock container with the given resource requests.

    Parameters
    ----------
    name:
        Container name.
    cpu:
        CPU request string, e.g. ``'100m'`` or ``'2'``.
    memory:
        Memory request string, e.g. ``'256Mi'`` or ``'1G'``.
    extended:
        Optional mapping of extended resource keys to raw quantity strings,
        e.g. ``{'nvidia.com/gpu': '2', 'ephemeral-storage': '10Gi'}``.
    """
    container = MagicMock()
    resources = MagicMock()
    requests = {'cpu': cpu, 'memory': memory, **extended}
    resources.requests = requests
    container.name = name
    container.resources = resources
    return container


def create_node(name: str, cpu: str, memory: str, extended: dict = {}) -> MagicMock:
    """Create a mock node with the given capacity/allocatable resources.

    Parameters
    ----------
    name:
        Node name.
    cpu:
        CPU capacity string, e.g. ``'4'``.
    memory:
        Memory capacity string, e.g. ``'8162156Ki'``.
    extended:
        Optional mapping of extended resource keys to raw quantity strings
        representing what the node *allocates*, e.g. ``{'nvidia.com/gpu': '4'}``.
        These are placed in both ``status.capacity`` and ``status.allocatable``
        so extractor code that reads either field works correctly.
    """
    node = MagicMock()
    metadata = MagicMock()
    metadata.name = name
    node.metadata = metadata
    status = MagicMock()
    capacity = {'cpu': cpu, 'memory': memory, **extended}
    status.capacity = capacity
    status.allocatable = capacity
    node.status = status
    return node
