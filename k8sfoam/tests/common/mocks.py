from unittest.mock import MagicMock
from kubernetes.client import (V1LabelSelector, V1ObjectMeta, V1PodDisruptionBudget,  # type: ignore
                               V1PodDisruptionBudgetSpec, V1PodDisruptionBudgetStatus)


def create_list(items, next_page=None, resource_version='1') -> MagicMock:
    """One page of a LIST response; next_page is its continue token. A bare
    MagicMock token is truthy, so paging would never stop; the last page must carry None."""
    page = MagicMock()
    page.items = items
    page.metadata._continue = next_page
    page.metadata.resource_version = resource_version
    return page


def create_pod(name: str, node_name: str, containers=[], init_containers=None,
               namespace='default', labels=None, qos_class='Burstable', node_selector=None,
               tolerations=None, owner_kind='ReplicaSet', annotations=None, phase='Running',
               owner_name=None, affinity=None, topology_spread_constraints=None, scheduling_gates=None) -> MagicMock:
    pod = MagicMock()
    metadata = MagicMock()
    metadata.name = name
    metadata.namespace = namespace
    # labels and qos_class are None on the real API whenever they are unset,
    # so the mock has to express that instead of an always-truthy MagicMock.
    metadata.labels = labels
    metadata.annotations = annotations
    # owner_kind=None builds a naked pod with no controller to recreate it.
    metadata.owner_references = [create_owner(owner_kind, owner_name)] if owner_kind else None
    spec = MagicMock()
    spec.node_name = node_name
    spec.node_selector = node_selector
    spec.tolerations = tolerations
    spec.affinity = affinity
    spec.topology_spread_constraints = topology_spread_constraints
    spec.scheduling_gates = scheduling_gates
    spec.containers = containers
    spec.init_containers = init_containers
    status = MagicMock()
    status.qos_class = qos_class
    status.phase = phase
    pod.metadata = metadata
    pod.spec = spec
    pod.status = status
    return pod


def create_owner(kind: str, name=None, controller: bool = True) -> MagicMock:
    owner = MagicMock()
    owner.kind = kind
    # Set after construction: MagicMock(name=...) names the mock, not the attribute.
    owner.name = name
    owner.controller = controller
    return owner


def create_toleration(key=None, operator='Equal', value=None, effect=None) -> MagicMock:
    toleration = MagicMock()
    toleration.key = key
    toleration.operator = operator
    toleration.value = value
    toleration.effect = effect
    return toleration


def create_container(name: str, cpu: str, memory: str, memory_limit=None, restart_policy=None,
                     extra_requests=None, extra_limits=None) -> MagicMock:
    container = MagicMock()
    # 'Always' on an init container makes it a native sidecar; None everywhere else.
    container.restart_policy = restart_policy
    resources = MagicMock()
    requests = {'cpu': cpu, 'memory': memory, **(extra_requests or {})}
    resources.requests = requests
    # The real API reports None when no limit is set at all.
    limits = {**({'memory': memory_limit} if memory_limit is not None else {}), **(extra_limits or {})}
    resources.limits = limits or None
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


def create_pdb(name: str, namespace: str = 'default', match_labels=None, disruptions_allowed: int = 1,
               selector: bool = True) -> V1PodDisruptionBudget:
    return V1PodDisruptionBudget(
        metadata=V1ObjectMeta(name=name, namespace=namespace),
        spec=V1PodDisruptionBudgetSpec(selector=V1LabelSelector(match_labels=match_labels) if selector else None),
        status=V1PodDisruptionBudgetStatus(current_healthy=0, desired_healthy=0, expected_pods=0,
                                           disruptions_allowed=disruptions_allowed))


def create_node(name: str, cpu: str, memory: str, unschedulable=None, taints=None,
                conditions=None, extended=None, labels=None, pods=None) -> MagicMock:
    node = MagicMock()
    metadata = MagicMock()
    metadata.name = name
    # None on the real API when the node has no labels, like a pod.
    metadata.labels = labels
    node.metadata = metadata
    status = MagicMock()
    # pods=None leaves the pod slot count out, like a node that reports no limit.
    status.allocatable = {'cpu': cpu, 'memory': memory, **(extended or {}), **({'pods': pods} if pods else {})}
    # A bare MagicMock is truthy and not iterable, so an unset attribute would read
    # as "cordoned" and blow up on the taint loop. Default to a plain healthy node.
    status.conditions = conditions if conditions is not None else [create_condition('Ready', 'True')]
    node.status = status
    spec = MagicMock()
    spec.unschedulable = unschedulable
    spec.taints = taints
    node.spec = spec
    return node
