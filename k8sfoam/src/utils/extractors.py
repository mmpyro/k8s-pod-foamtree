from typing import Optional
from kubernetes.utils import parse_quantity  # type: ignore
from k8sfoam.src.common.dtos import PodResources, ContainerResources, NodeResources
from k8sfoam.src.common.node_status import PRESSURE_CONDITIONS, CORDON_TAINT
from k8sfoam.src.common.resources import convert_cpu, convert_memory, extract_extended


def _add(a: dict, b: dict) -> dict:
    return {k: a.get(k, 0) + b.get(k, 0) for k in a.keys() | b.keys()}


def _max(a: dict, b: dict) -> dict:
    return {k: max(a.get(k, 0), b.get(k, 0)) for k in a.keys() | b.keys()}


def _totals(c: ContainerResources) -> dict:
    """Every resource a container requests, keyed by name, for the effective-request rule."""
    return {'cpu': c.cpu, 'memory': c.memory, **(c.extended or {})}


def _requirement(r) -> dict:
    return {'key': r.key, 'operator': r.operator, 'values': list(r.values or [])}


def _label_selector(s) -> Optional[dict]:
    """None stays None: an unset selector matches no pod, while an empty one matches every pod."""
    if s is None:
        return None
    return {'matchLabels': dict(s.match_labels or {}),
            'matchExpressions': [_requirement(r) for r in s.match_expressions or []]}


def _pod_affinity_terms(rule) -> list:
    """The required terms of a podAffinity or podAntiAffinity; preferred ones only score nodes."""
    terms = rule.required_during_scheduling_ignored_during_execution if rule is not None else None
    return [{'labelSelector': _label_selector(t.label_selector), 'namespaces': list(t.namespaces or []),
             'namespaceSelector': _label_selector(t.namespace_selector), 'topologyKey': t.topology_key}
            for t in terms or []]


def _topology_spread(pod, labels: dict) -> list:
    """The DoNotSchedule constraints; ScheduleAnyway ones only score nodes.

    matchLabelKeys ANDs `key In (the pod's value)` into the selector, so a
    Deployment's old ReplicaSet pods do not count against its new ones; keys the
    pod has no label for are ignored, as the API server does.
    """
    constraints = []
    for c in pod.spec.topology_spread_constraints or []:
        if c.when_unsatisfiable != 'DoNotSchedule':
            continue
        selector = _label_selector(c.label_selector)
        if selector is not None:
            selector['matchExpressions'] += [{'key': k, 'operator': 'In', 'values': [labels[k]]}
                                             for k in c.match_label_keys or [] if k in labels]
        constraints.append({'maxSkew': c.max_skew, 'topologyKey': c.topology_key, 'minDomains': c.min_domains,
                            'labelSelector': selector, 'nodeAffinityPolicy': c.node_affinity_policy or 'Honor',
                            'nodeTaintsPolicy': c.node_taints_policy or 'Ignore'})
    return constraints


# The kubelet marks a static pod's API copy with this annotation. Such a pod is
# owned by the node itself: a drain cannot evict it and nothing reschedules it.
MIRROR_POD_ANNOTATION = 'kubernetes.io/config.mirror'
POD_TEMPLATE_HASH_LABEL = 'pod-template-hash'


class ResourcesExtractor():
    def __requests_contains_key(self, requests: dict, key: str) -> bool:
        return requests is not None and key in requests

    def __extract_tolerations(self, pod) -> list:
        return [{'key': t.key, 'operator': t.operator, 'value': t.value, 'effect': t.effect}
                for t in pod.spec.tolerations or []]

    def __extract_node_affinity(self, pod) -> list:
        """The required nodeSelectorTerms; [] when the pod requires none."""
        affinity = pod.spec.affinity
        node_affinity = affinity.node_affinity if affinity is not None else None
        required = (node_affinity.required_during_scheduling_ignored_during_execution
                    if node_affinity is not None else None)
        if required is None:
            return []
        return [{'matchExpressions': [_requirement(r) for r in term.match_expressions or []],
                 'matchFields': [_requirement(r) for r in term.match_fields or []]}
                for term in required.node_selector_terms or []]

    def __extract_owner(self, pod, labels: dict) -> tuple:
        """Who recreates the pod after an eviction, as (kind, name): a controller,
        ('Node', None) for a static pod, or (None, None) for a naked pod that
        nothing brings back."""
        if MIRROR_POD_ANNOTATION in (pod.metadata.annotations or {}):
            return 'Node', None
        refs = pod.metadata.owner_references or []
        controller = next((r for r in refs if r.controller), refs[0] if refs else None)
        if controller is None:
            return None, None
        kind, name = controller.kind, controller.name
        # A Deployment names its ReplicaSet <deployment>-<pod-template-hash> and
        # stamps the hash on the pod, so the Deployment is recoverable without
        # reading apps/v1. A bare ReplicaSet carries no such suffix.
        template_hash = labels.get(POD_TEMPLATE_HASH_LABEL)
        if kind == 'ReplicaSet' and template_hash and name and name.endswith(f'-{template_hash}'):
            return 'Deployment', name[:-len(template_hash) - 1]
        return kind, name

    def __extract_container_resources(self, container) -> ContainerResources:
        requests = container.resources.requests
        cpu = convert_cpu(requests['cpu']) if self.__requests_contains_key(requests, 'cpu') else 0
        memory = convert_memory(requests['memory']) if self.__requests_contains_key(requests, 'memory') else 0
        # None, not 0: an unset limit means "unbounded", which is exactly what the audit flags.
        limits = container.resources.limits
        memory_limit = convert_memory(limits['memory']) if self.__requests_contains_key(limits, 'memory') else None
        # Extended resources may be set as limits only; the request then defaults to the limit.
        extended = {**extract_extended(limits), **extract_extended(requests)}
        return ContainerResources(container.name, cpu, memory, memory_limit, extended)

    def extract_pod_requested_resources(self, pod) -> PodResources:
        name = pod.metadata.name
        node_name = pod.spec.node_name
        namespace = pod.metadata.namespace

        # Both are None on the API whenever unset — labels normalise to an
        # empty dict, while QoS stays None because it must never be guessed.
        labels = pod.metadata.labels or {}
        qos_class = pod.status.qos_class if pod.status is not None else None

        # --- Regular containers (run concurrently → sum) ---
        containers = [self.__extract_container_resources(c) for c in pod.spec.containers]

        # --- Init containers (run sequentially → max) ---
        # Native sidecars (restartPolicy: Always) start in order but keep running,
        # so they join the regular containers and add to every init container
        # started after them. Same rule as the scheduler's PodRequests.
        init_containers = []
        sidecars: dict = {}
        max_init: dict = {}
        for spec in pod.spec.init_containers or []:
            c = self.__extract_container_resources(spec)
            if spec.restart_policy == 'Always':
                containers.append(c)
                sidecars = _add(sidecars, _totals(c))
                max_init = _max(max_init, sidecars)
            else:
                init_containers.append(c)
                max_init = _max(max_init, _add(sidecars, _totals(c)))

        # --- Effective request (what the scheduler actually reserves) ---
        regular: dict = {}
        for c in containers:
            regular = _add(regular, _totals(c))
        effective = _max(regular, max_init)
        effective_cpu = effective.get('cpu', 0)
        effective_memory = effective.get('memory', 0)
        extended = {k: v for k, v in effective.items() if k not in ('cpu', 'memory') and v}

        owner_kind, owner_name = self.__extract_owner(pod, labels)
        affinity = pod.spec.affinity
        return PodResources(name, node_name, effective_cpu, effective_memory, containers, init_containers,
                            namespace, labels, qos_class, extended, dict(pod.spec.node_selector or {}),
                            self.__extract_tolerations(pod), owner_kind, owner_name,
                            self.__extract_node_affinity(pod),
                            _pod_affinity_terms(affinity.pod_affinity if affinity is not None else None),
                            _pod_affinity_terms(affinity.pod_anti_affinity if affinity is not None else None),
                            _topology_spread(pod, labels))

    def __extract_node_taints(self, node) -> list:
        """Every taint on the node, minus the one Kubernetes adds on cordon.

        PreferNoSchedule is kept: it does not mark the node, but the detail view
        still lists it.
        """
        taints = node.spec.taints or [] if node.spec is not None else []
        return [{'key': t.key, 'value': t.value, 'effect': t.effect}
                for t in taints if t.key != CORDON_TAINT]

    def __extract_node_conditions(self, node) -> dict:
        """Scheduling-relevant conditions, flattened to booleans."""
        conditions = node.status.conditions or [] if node.status is not None else []
        status_by_type = {c.type: c.status for c in conditions}
        result = {name: status_by_type.get(name) == 'True' for name in PRESSURE_CONDITIONS}
        # 'Unknown' means the API server stopped hearing from the kubelet, which
        # is not ready. A missing Ready key means the node reported no conditions
        # at all, so default to healthy rather than invent an outage.
        result['Ready'] = status_by_type.get('Ready', 'True') == 'True'
        return result

    def extract_node_resources(self, node) -> NodeResources:
        # Allocatable, not capacity: it excludes kube/system-reserved, so it is what
        # the scheduler can actually place pods into.
        allocatable = node.status.allocatable
        cpu = convert_cpu(allocatable['cpu'])
        memory = convert_memory(allocatable['memory'])
        unschedulable = bool(node.spec.unschedulable) if node.spec is not None else False
        # None on the API when unset, like a pod's labels.
        labels = dict(node.metadata.labels or {})
        # The pod slot count backs the simulator's pod-limit check; None when unreported.
        alloc_pods = int(parse_quantity(allocatable['pods'])) if 'pods' in allocatable else None
        return NodeResources(node.metadata.name, cpu, memory, unschedulable,
                             self.__extract_node_taints(node), self.__extract_node_conditions(node),
                             extract_extended(allocatable), labels, alloc_pods)
