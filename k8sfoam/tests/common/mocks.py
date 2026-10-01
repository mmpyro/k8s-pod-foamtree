from unittest.mock import MagicMock


def create_pod(name: str, node_name: str, containers=[], init_containers=None,
               namespace='default', labels=None, qos_class='Burstable', node_selector=None,
               tolerations=None, owner_kind='ReplicaSet', annotations=None) -> MagicMock:
    pod = MagicMock()
    metadata = MagicMock()
    metadata.name = name
    metadata.namespace = namespace
    # labels and qos_class are None on the real API whenever they are unset,
    # so the mock has to express that instead of an always-truthy MagicMock.
    metadata.labels = labels
    metadata.annotations = annotations
    # owner_kind=None builds a naked pod with no controller to recreate it.
    metadata.owner_references = [create_owner(owner_kind)] if owner_kind else None
    spec = MagicMock()
    spec.node_name = node_name
    spec.node_selector = node_selector
    spec.tolerations = tolerations
    spec.containers = containers
    spec.init_containers = init_containers
    status = MagicMock()
    status.qos_class = qos_class
    pod.metadata = metadata
    pod.spec = spec
    pod.status = status
    return pod


def create_owner(kind: str, controller: bool = True) -> MagicMock:
    owner = MagicMock()
    owner.kind = kind
    owner.controller = controller
    return owner


def create_toleration(key=None, operator='Equal', value=None, effect=None) -> MagicMock:
    toleration = MagicMock()
    toleration.key = key
    toleration.operator = operator
    toleration.value = value
    toleration.effect = effect
    return toleration


def create_container(name: str, cpu: str, memory: str, memory_limit=None, restart_policy=None) -> MagicMock:
    container = MagicMock()
    # 'Always' on an init container makes it a native sidecar; None everywhere else.
    container.restart_policy = restart_policy
    resources = MagicMock()
    requests = {'cpu': cpu, 'memory': memory}
    resources.requests = requests
    # The real API reports None when no limit is set at all.
    resources.limits = {'memory': memory_limit} if memory_limit is not None else None
    container.name = name
    container.resources = resources
    return container


def create_taint(key: str, value=None, effect: str = 'NoSchedule') -> MagicMock:
    taint = MagicMock()
    taint.key = key
    taint.value = value
    taint.effect = effect
    return taint


def create_condition(type: str, status: str) -> MagicMock:
    condition = MagicMock()
    condition.type = type
    condition.status = status
    return condition


def create_node(name: str, cpu: str, memory: str, unschedulable=None, taints=None,
                conditions=None, labels=None, allocatable=None) -> MagicMock:
    node = MagicMock()
    metadata = MagicMock()
    metadata.name = name
    metadata.labels = labels
    node.metadata = metadata
    status = MagicMock()
    status.capacity = {'cpu': cpu, 'memory': memory}
    status.allocatable = allocatable
    # A bare MagicMock is truthy and not iterable, so an unset attribute would read
    # as "cordoned" and blow up on the taint loop. Default to a plain healthy node.
    status.conditions = conditions if conditions is not None else [create_condition('Ready', 'True')]
    node.status = status
    spec = MagicMock()
    spec.unschedulable = unschedulable
    spec.taints = taints
    node.spec = spec
    return node
