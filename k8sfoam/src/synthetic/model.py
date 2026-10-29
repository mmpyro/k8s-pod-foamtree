"""A synthetic cluster built from real kubernetes.client objects.

The nodes and pods are the V1Node / V1Pod models the API client returns, so they
go through the same extractor, mapper, audit rules and scheduler as a live
cluster. Placement is a rough least-allocated scheduler: good enough to look
like a real cluster, not a model of kube-scheduler.
"""
import random
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict, List, Optional, Tuple
from kubernetes.client import (V1Container, V1LabelSelector, V1Node, V1NodeCondition, V1NodeSpec,  # type: ignore
                               V1NodeStatus, V1ObjectMeta, V1OwnerReference, V1Pod, V1PodDisruptionBudget,
                               V1PodDisruptionBudgetSpec, V1PodDisruptionBudgetStatus, V1PodSpec, V1PodStatus,
                               V1ResourceRequirements, V1Taint, V1Toleration)
from kubernetes.utils import parse_quantity  # type: ignore
from k8sfoam.src.common.node_status import PRESSURE_CONDITIONS
from k8sfoam.src.common.scheduler import CORDON_TAINT, FILTER_EFFECTS, tolerates
from k8sfoam.src.utils.extractors import MIRROR_POD_ANNOTATION, POD_TEMPLATE_HASH_LABEL

HOSTNAME = 'kubernetes.io/hostname'
ZONE = 'topology.kubernetes.io/zone'
REGION = 'topology.kubernetes.io/region'
INSTANCE_TYPE = 'node.kubernetes.io/instance-type'

# What a DaemonSet that must run everywhere (CNI, log shipper) tolerates.
TOLERATE_ALL = ({'operator': 'Exists'},)

# Kubernetes' own alphabet for generated name suffixes: no vowels, no 0/1/3.
_SUFFIX_CHARS = 'bcdfghjklmnpqrstvwxz2456789'


@dataclass
class Container:
    name: str
    cpu: Optional[str] = None
    memory: Optional[str] = None
    cpu_limit: Optional[str] = None
    memory_limit: Optional[str] = None
    # Extended resources (nvidia.com/gpu, ephemeral-storage, hugepages-2Mi), set as requests and limits.
    extended: Dict[str, str] = field(default_factory=dict)
    # A native sidecar: an init container with restartPolicy Always.
    sidecar: bool = False


@dataclass
class Workload:
    namespace: str
    name: str
    # Deployment, StatefulSet, DaemonSet, Job; 'Node' for a static pod, None for a naked pod.
    kind: Optional[str]
    containers: List[Container]
    replicas: int = 1
    init_containers: List[Container] = field(default_factory=list)
    node_selector: Dict[str, str] = field(default_factory=dict)
    tolerations: List[dict] = field(default_factory=list)
    # Pod labels on top of `app` and the model's default_labels.
    labels: Dict[str, str] = field(default_factory=dict)


@dataclass
class NodePool:
    name: str
    count: int
    instance_type: str
    cpu: str
    memory: str
    zones: Tuple[str, ...]
    pods: int = 110
    spot: bool = False
    taints: List[dict] = field(default_factory=list)
    extended: Dict[str, str] = field(default_factory=dict)
    labels: Dict[str, str] = field(default_factory=dict)
    ephemeral_storage: str = '95Gi'


# The model builds every node and pod itself, so these fields are always set,
# though newer kubernetes clients type them all as Optional.
def _name(obj) -> str:
    return obj.metadata.name


def _labels(obj) -> Dict[str, str]:
    return obj.metadata.labels


def _node_spec(node) -> V1NodeSpec:
    return node.spec


def _conditions(node) -> List[V1NodeCondition]:
    return node.status.conditions


def _node_of(pod) -> str:
    return pod.spec.node_name


def _quantities(resources: Dict[str, str]) -> Dict[str, Decimal]:
    return {name: parse_quantity(q) for name, q in resources.items()}


def _add(a: Dict[str, Decimal], b: Dict[str, Decimal]) -> Dict[str, Decimal]:
    return {k: a.get(k, Decimal(0)) + b.get(k, Decimal(0)) for k in a.keys() | b.keys()}


def _max(a: Dict[str, Decimal], b: Dict[str, Decimal]) -> Dict[str, Decimal]:
    return {k: max(a.get(k, Decimal(0)), b.get(k, Decimal(0))) for k in a.keys() | b.keys()}


def _container_requests(c: Container) -> Dict[str, Decimal]:
    # An unset request defaults to the limit, as the API server does.
    cpu, memory = c.cpu or c.cpu_limit, c.memory or c.memory_limit
    return _quantities({**({'cpu': cpu} if cpu else {}), **({'memory': memory} if memory else {}), **c.extended})


def effective_requests(w: Workload) -> Dict[str, Decimal]:
    """What the scheduler reserves for one pod of `w`; the extractor's rule, on quantities."""
    regular: Dict[str, Decimal] = {}
    sidecars: Dict[str, Decimal] = {}
    init_peak: Dict[str, Decimal] = {}
    for c in w.init_containers:
        if c.sidecar:
            sidecars = _add(sidecars, _container_requests(c))
            init_peak = _max(init_peak, sidecars)
        else:
            init_peak = _max(init_peak, _add(sidecars, _container_requests(c)))
    for c in w.containers:
        regular = _add(regular, _container_requests(c))
    return _max(_add(regular, sidecars), init_peak)


def qos_class(w: Workload) -> str:
    """The QoS class the API server would assign to a pod of `w`."""
    containers = w.containers + w.init_containers
    if not any(c.cpu or c.memory or c.cpu_limit or c.memory_limit for c in containers):
        return 'BestEffort'
    guaranteed = all(c.cpu_limit and c.memory_limit and (c.cpu or c.cpu_limit) == c.cpu_limit and (c.memory or c.memory_limit) == c.memory_limit
                     for c in containers)
    return 'Guaranteed' if guaranteed else 'Burstable'


def _v1_container(c: Container) -> V1Container:
    requests = {**({'cpu': c.cpu} if c.cpu else {}), **({'memory': c.memory} if c.memory else {}), **c.extended}
    limits = {**({'cpu': c.cpu_limit} if c.cpu_limit else {}),
              **({'memory': c.memory_limit} if c.memory_limit else {}), **c.extended}
    return V1Container(name=c.name, image=f'registry.example.com/{c.name}:1.0',
                       resources=V1ResourceRequirements(requests=requests or None, limits=limits or None),
                       restart_policy='Always' if c.sidecar else None)


class ClusterModel():
    """Nodes and pods of one synthetic cluster, plus the bookkeeping to place and remove pods."""

    def __init__(self, context: str, provider: str, region: str, seed: int,
                 default_labels: Optional[Dict[str, str]] = None):
        self.context = context
        self.provider = provider
        self.region = region
        # Labels every pod of a Deployment, StatefulSet or Job carries unless its workload overrides them.
        self.default_labels = dict(default_labels or {})
        # Layout randomness only; nothing here needs to be unpredictable.
        self.rng = random.Random(f'{seed}:{context}')  # nosec B311
        self.nodes: List[V1Node] = []
        self.pods: List[V1Pod] = []
        self.workloads: List[Workload] = []
        # (namespace, name, matchLabels, minAvailable): the specs; status follows the pods, see disruption_budgets().
        self.__budgets: List[Tuple[str, str, Dict[str, str], int]] = []
        self.__alloc: Dict[str, Dict[str, Decimal]] = {}
        self.__free: Dict[str, Dict[str, Decimal]] = {}
        self.__zone: Dict[str, str] = {}
        self.__pods_of: Dict[int, List[V1Pod]] = {}
        self.__template_hash: Dict[int, str] = {}
        self.__next_ordinal: Dict[int, int] = {}
        self.__flapping: Optional[str] = None

    # ---- names -------------------------------------------------------------

    def suffix(self, length: int = 5) -> str:
        return ''.join(self.rng.choice(_SUFFIX_CHARS) for _ in range(length))

    def __node_name(self, pool: NodePool) -> str:
        while True:
            if self.provider == 'gke':
                name = f'gke-{self.context.split("_")[-1]}-{pool.name}-{self.suffix(8)}-{self.suffix(4)}'
            else:
                name = f'ip-10-0-{self.rng.randint(0, 63)}-{self.rng.randint(1, 254)}.{self.region}.compute.internal'
            if name not in self.__alloc:
                return name

    def __node_labels(self, pool: NodePool, name: str, zone: str) -> Dict[str, str]:
        labels = {
            HOSTNAME: name,
            ZONE: zone,
            REGION: self.region,
            INSTANCE_TYPE: pool.instance_type,
            'kubernetes.io/os': 'linux',
            'kubernetes.io/arch': 'amd64',
        }
        if self.provider == 'gke':
            labels['cloud.google.com/gke-nodepool'] = pool.name
            if pool.spot:
                labels['cloud.google.com/gke-spot'] = 'true'
        elif self.provider == 'karpenter':
            labels['karpenter.sh/nodepool'] = pool.name
            labels['karpenter.sh/capacity-type'] = 'spot' if pool.spot else 'on-demand'
        else:
            labels['eks.amazonaws.com/nodegroup'] = pool.name
            labels['eks.amazonaws.com/capacityType'] = 'SPOT' if pool.spot else 'ON_DEMAND'
        return {**labels, **pool.labels}

    # ---- nodes -------------------------------------------------------------

    def add_pool(self, pool: NodePool) -> List[str]:
        """Create the pool's nodes, spread round-robin over its zones; returns their names."""
        names = []
        for i in range(pool.count):
            name = self.__node_name(pool)
            allocatable = {'cpu': pool.cpu, 'memory': pool.memory, 'pods': str(pool.pods),
                           'ephemeral-storage': pool.ephemeral_storage, **pool.extended}
            conditions = [V1NodeCondition(type=t, status='False') for t in PRESSURE_CONDITIONS]
            conditions.append(V1NodeCondition(type='Ready', status='True'))
            taints = [V1Taint(key=t['key'], value=t.get('value'), effect=t['effect']) for t in pool.taints]
            self.nodes.append(V1Node(
                api_version='v1', kind='Node',
                metadata=V1ObjectMeta(name=name, labels=self.__node_labels(pool, name, pool.zones[i % len(pool.zones)])),
                spec=V1NodeSpec(taints=taints or None),
                status=V1NodeStatus(allocatable=allocatable, capacity=dict(allocatable), conditions=conditions),
            ))
            self.__alloc[name] = _quantities(allocatable)
            self.__free[name] = dict(self.__alloc[name])
            self.__zone[name] = pool.zones[i % len(pool.zones)]
            names.append(name)
        return names

    def node(self, name: str) -> V1Node:
        return next(n for n in self.nodes if _name(n) == name)

    def cordon(self, name: str) -> None:
        _node_spec(self.node(name)).unschedulable = True

    def set_condition(self, name: str, condition: str, status: bool) -> None:
        for c in _conditions(self.node(name)):
            if c.type == condition:
                c.status = 'True' if status else 'False'

    def __ready(self, node: V1Node) -> bool:
        return all(c.status == 'True' for c in _conditions(node) if c.type == 'Ready')

    # ---- placement ---------------------------------------------------------

    def __eligible(self, node: V1Node, w: Workload) -> bool:
        labels = _labels(node)
        if any(labels.get(k) != v for k, v in w.node_selector.items()):
            return False
        spec = _node_spec(node)
        if spec.unschedulable and not any(tolerates(t, CORDON_TAINT) for t in w.tolerations):
            return False
        for taint in spec.taints or []:
            as_dict = {'key': taint.key, 'value': taint.value, 'effect': taint.effect}
            if taint.effect in FILTER_EFFECTS and not any(tolerates(t, as_dict) for t in w.tolerations):
                return False
        return True

    def __fits(self, name: str, demand: Dict[str, Decimal]) -> bool:
        free = self.__free[name]
        return free.get('pods', Decimal(1)) >= 1 and all(free.get(k, Decimal(0)) >= v for k, v in demand.items())

    def __score(self, node: V1Node, key: int, demand: Dict[str, Decimal]) -> float:
        """Least-allocated, minus a penalty for each sibling replica on the node and in
        its zone (kube-scheduler's default spreading), plus jitter so the layout is not
        perfectly even."""
        name = _name(node)
        alloc, free = self.__alloc[name], self.__free[name]
        shares = [float((free[k] - demand.get(k, Decimal(0))) / alloc[k]) for k in ('cpu', 'memory') if alloc[k]]
        zone = _labels(node).get(ZONE)
        siblings = [_node_of(p) for p in self.__pods_of[key]]
        same_node = siblings.count(name)
        same_zone = sum(1 for s in siblings if self.__zone[s] == zone)
        return min(shares, default=0.0) - 0.5 * same_node - 0.1 * same_zone + self.rng.uniform(0, 0.15)

    def __reserve(self, name: str, demand: Dict[str, Decimal], sign: int) -> None:
        free = self.__free[name]
        for k, v in demand.items():
            free[k] = free.get(k, Decimal(0)) - sign * v
        free['pods'] = free.get('pods', Decimal(0)) - sign

    def __pod_name(self, w: Workload, key: int, node_name: str) -> str:
        if w.kind == 'Deployment':
            return f'{w.name}-{self.__template_hash[key]}-{self.suffix()}'
        if w.kind == 'StatefulSet':
            ordinal = self.__next_ordinal.get(key, 0)
            self.__next_ordinal[key] = ordinal + 1
            return f'{w.name}-{ordinal}'
        if w.kind == 'Node':
            return f'{w.name}-{node_name}'
        if w.kind is None:
            return w.name if w.replicas == 1 else f'{w.name}-{len(self.__pods_of[key]) + 1}'
        return f'{w.name}-{self.suffix()}'

    def __owner(self, w: Workload, key: int, node_name: str) -> Optional[V1OwnerReference]:
        if w.kind is None:
            return None
        if w.kind == 'Node':
            return V1OwnerReference(api_version='v1', kind='Node', name=node_name, uid=self.suffix(12), controller=True)
        kind, name = w.kind, w.name
        if kind == 'Deployment':
            kind, name = 'ReplicaSet', f'{w.name}-{self.__template_hash[key]}'
        api_version = 'batch/v1' if kind == 'Job' else 'apps/v1'
        return V1OwnerReference(api_version=api_version, kind=kind, name=name, uid=self.suffix(12), controller=True)

    def __bind(self, w: Workload, key: int, node_name: str, demand: Dict[str, Decimal]) -> V1Pod:
        defaults = self.default_labels if w.kind not in ('DaemonSet', 'Node') else {}
        labels = {'app': w.name, 'app.kubernetes.io/name': w.name, **defaults, **w.labels}
        annotations = {}
        if w.kind == 'Deployment':
            labels[POD_TEMPLATE_HASH_LABEL] = self.__template_hash[key]
        if w.kind == 'Node':
            annotations[MIRROR_POD_ANNOTATION] = self.suffix(32)
        name = self.__pod_name(w, key, node_name)
        owner = self.__owner(w, key, node_name)
        sidecars = [c for c in w.init_containers if c.sidecar]
        inits = [c for c in w.init_containers if not c.sidecar]
        pod = V1Pod(
            api_version='v1', kind='Pod',
            metadata=V1ObjectMeta(name=name, namespace=w.namespace, labels=labels, annotations=annotations or None,
                                  owner_references=[owner] if owner else None),
            spec=V1PodSpec(
                node_name=node_name,
                containers=[_v1_container(c) for c in w.containers],
                init_containers=[_v1_container(c) for c in sidecars + inits] or None,
                node_selector=dict(w.node_selector) or None,
                tolerations=[V1Toleration(**t) for t in w.tolerations] or None,
            ),
            status=V1PodStatus(phase='Running', qos_class=qos_class(w)),
        )
        self.__reserve(node_name, demand, +1)
        self.pods.append(pod)
        self.__pods_of[key].append(pod)
        return pod

    def __place(self, w: Workload, key: int) -> Optional[V1Pod]:
        """Bind one more replica of `w` to the best node it fits on; None when it would stay Pending."""
        demand = effective_requests(w)
        candidates = [n for n in self.nodes if self.__eligible(n, w) and self.__fits(_name(n), demand)]
        if not candidates:
            return None
        target = max(candidates, key=lambda n: self.__score(n, key, demand))
        return self.__bind(w, key, _name(target), demand)

    def deploy(self, w: Workload) -> List[V1Pod]:
        """Add a workload and place its pods: one per eligible node for a DaemonSet, `replicas` otherwise."""
        key = len(self.workloads)
        self.workloads.append(w)
        self.__pods_of[key] = []
        self.__template_hash[key] = self.suffix(10)
        if w.kind in ('DaemonSet', 'Node'):
            # Bound by its controller, not the scheduler, so capacity is not checked.
            demand = effective_requests(w)
            for node in self.nodes:
                if self.__eligible(node, w):
                    self.__bind(w, key, _name(node), demand)
        else:
            for _ in range(w.replicas):
                self.__place(w, key)
        return self.__pods_of[key]

    def add_budget(self, namespace: str, name: str, match_labels: Dict[str, str], min_available: int) -> None:
        """A PodDisruptionBudget with minAvailable over the pods its labels select."""
        self.__budgets.append((namespace, name, dict(match_labels), min_available))

    def disruption_budgets(self) -> List[V1PodDisruptionBudget]:
        """The budgets with the status the disruption controller would report for the pods running now."""
        budgets = []
        for namespace, name, match_labels, min_available in self.__budgets:
            in_namespace = [p for p in self.pods if p.metadata.namespace == namespace]
            healthy = sum(1 for p in in_namespace if all(_labels(p).get(k) == v for k, v in match_labels.items()))
            budgets.append(V1PodDisruptionBudget(
                api_version='policy/v1', kind='PodDisruptionBudget',
                metadata=V1ObjectMeta(name=name, namespace=namespace),
                spec=V1PodDisruptionBudgetSpec(min_available=min_available,
                                               selector=V1LabelSelector(match_labels=match_labels)),
                status=V1PodDisruptionBudgetStatus(current_healthy=healthy, desired_healthy=min_available,
                                                   expected_pods=healthy,
                                                   disruptions_allowed=max(0, healthy - min_available))))
        return budgets

    def pods_of(self, w: Workload) -> List[V1Pod]:
        return list(self.__pods_of[self.workloads.index(w)])

    def __remove(self, key: int, pod: V1Pod) -> None:
        self.__reserve(_node_of(pod), effective_requests(self.workloads[key]), -1)
        self.pods.remove(pod)
        self.__pods_of[key].remove(pod)
        if self.workloads[key].kind == 'StatefulSet':
            self.__next_ordinal[key] -= 1

    # ---- churn -------------------------------------------------------------

    def churn(self) -> str:
        """One random change of a busy cluster; returns what happened, for logs and tests.

        A flapped node comes back on the next step. Otherwise a Deployment scales by
        one replica, staying within two of its declared size, or now and then a node
        stops reporting Ready.
        """
        if self.__flapping is not None:
            name, self.__flapping = self.__flapping, None
            self.set_condition(name, 'Ready', True)
            return f'node {name} Ready again'
        healthy = [_name(n) for n in self.nodes if self.__ready(n) and not _node_spec(n).unschedulable]
        if healthy and self.rng.random() < 0.1:
            self.__flapping = self.rng.choice(healthy)
            self.set_condition(self.__flapping, 'Ready', False)
            return f'node {self.__flapping} NotReady'
        scalable = [i for i, w in enumerate(self.workloads)
                    if w.kind == 'Deployment' and w.replicas >= 2 and HOSTNAME not in w.node_selector]
        if not scalable:
            return 'nothing to change'
        key = self.rng.choice(scalable)
        w, current = self.workloads[key], self.__pods_of[key]
        low, high = max(2, w.replicas - 2), w.replicas + 2
        grow = len(current) <= low or (len(current) < high and self.rng.random() < 0.5)
        if grow and self.__place(w, key) is not None:
            return f'{w.namespace}/{w.name} scaled to {len(current)}'
        if len(current) > low:
            self.__remove(key, current[-1])
            return f'{w.namespace}/{w.name} scaled to {len(current)}'
        return 'nothing to change'
