"""Dry-run scheduler: the kube-scheduler filter phase, emulated against free allocatable.

Backs five what-if questions — "can this pod fit anywhere?", "where would this
node's pods go if it were drained?", "which nodes could be removed?", "can this
workload scale out by N replicas?" and "where would the Pending pods go?". It deliberately covers only the predicates
that decide most placements: node condition, cordon, nodeSelector, required node
affinity, taints and tolerations, pod count, CPU, memory and extended resource
requests (GPUs, ephemeral-storage, hugepages), DoNotSchedule topology spread, and
required inter-pod affinity and anti-affinity. Drains also honour
PodDisruptionBudgets. namespaceSelector on pod affinity terms and volume zone
binding are out of scope, so a "fits" verdict is necessary rather than sufficient.
Preferred affinity terms and ScheduleAnyway spread constraints only score nodes,
so they never filter one out.

Fit, drain and pending also take hypothetical nodes — copies of an existing node or a
custom shape — to compare node shapes before changing a node group.

Units follow the rest of the backend: CPU in millicores, memory and byte-sized
extended resources in decimal kB, other extended resources as plain counts.
"""
from collections import Counter
from typing import Collection, Iterable, Optional
from k8sfoam.src.common.dtos import DisruptionBudget, NodeResources, PodResources
from k8sfoam.src.common.node_topology import node_topology
from k8sfoam.src.common.resources import is_bytes


# Effects the filter phase honours. PreferNoSchedule only lowers a node's score.
FILTER_EFFECTS = ('NoSchedule', 'NoExecute')

# Taints the node lifecycle controller puts on cordoned and NotReady nodes. The
# extractor drops the cordon taint (spec.unschedulable says the same), so they are
# re-created here: a pod that tolerates them — every DaemonSet pod does — still fits.
CORDON_TAINT = {'key': 'node.kubernetes.io/unschedulable', 'value': None, 'effect': 'NoSchedule'}
NOT_READY_TAINT = {'key': 'node.kubernetes.io/not-ready', 'value': None, 'effect': 'NoSchedule'}

# Owners whose pods stay put during a drain: kubectl drain skips DaemonSet pods,
# and a static pod belongs to the kubelet, which keeps running it.
DRAIN_SKIPPED_OWNERS = {'DaemonSet': 'daemonset', 'Node': 'static'}

# Reason slugs in the order the filter evaluates them — the frontend labels them.
REASON_ORDER = ('cordoned', 'not-ready', 'node-selector', 'node-affinity', 'taint',
                'insufficient-pods', 'insufficient-cpu', 'insufficient-memory', 'insufficient-extended',
                'topology-spread', 'pod-affinity', 'pod-anti-affinity')

# Why a node cannot be removed even on its own. cluster-autoscaler refuses to
# scale down a node running a pod no controller would recreate, and the control
# plane is not a node group it manages.
BLOCKER_ORDER = ('control-plane', 'pending', 'pdb', 'unmanaged')
CONTROL_PLANE_LABELS = ('node-role.kubernetes.io/control-plane', 'node-role.kubernetes.io/master')

# Upper bound on the copies one replica simulation places; each copy filters every node.
MAX_REPLICAS = 100

# Upper bound on the hypothetical nodes one simulation adds, over all its entries.
MAX_EXTRA_NODES = 50

HOSTNAME_LABEL = 'kubernetes.io/hostname'
# Taints a fresh node does not start with: the lifecycle controller's condition
# taints, and the marks autoscalers put on a node they are about to remove.
TRANSIENT_TAINT_PREFIXES = ('node.kubernetes.io/', 'node.cloudprovider.kubernetes.io/')
TRANSIENT_TAINT_KEYS = ('ToBeDeletedByClusterAutoscaler', 'DeletionCandidateOfClusterAutoscaler',
                        'karpenter.sh/disrupted', 'karpenter.sh/disruption')


def _allocatable(node: NodeResources) -> dict:
    """What the scheduler may fill. The extractor already reads cpu/memory from allocatable."""
    return {
        'cpu': node.cpu or 0,
        'memory': node.memory or 0,
        # No pod limit reported means no pod-count check, not a limit of zero.
        'pods': node.allocatable_pods,
        # A resource the node does not advertise has zero allocatable.
        'extended': dict(node.extended or {}),
    }


def _reserve(room: dict, pod: PodResources) -> None:
    """Take a pod's effective requests out of a node's free capacity."""
    room['cpu'] -= pod.cpu or 0
    room['memory'] -= pod.memory or 0
    if room['pods'] is not None:
        room['pods'] -= 1
    for name, amount in (pod.extended or {}).items():
        room['extended'][name] = room['extended'].get(name, 0) - amount


def free_capacity(nodes: Iterable[NodeResources], pods: Iterable[PodResources]) -> dict:
    """Allocatable minus the effective requests of every pod bound to each node."""
    free = {n.name: _allocatable(n) for n in nodes}
    for pod in pods:
        room = free.get(pod.node_name)
        if room is not None:
            _reserve(room, pod)
    return free


def fmt_cpu(millicores: float) -> str:
    return f'{max(0, round(millicores))}m'


def fmt_memory(kb: float) -> str:
    """Binary units, like `kubectl describe node`: kB are decimal, so convert via bytes."""
    mib = max(0.0, kb) * 1000 / (1024 * 1024)
    return f'{mib / 1024:.1f}Gi' if mib >= 1024 else f'{mib:.0f}Mi'


def fmt_extended(name: str, amount: float) -> str:
    """kB as binary units for byte-sized resources, a plain count for devices."""
    if is_bytes(name):
        return fmt_memory(amount)
    amount = max(0, amount)
    return str(int(amount)) if amount == int(amount) else f'{amount:g}'


def _fmt_taint(taint: dict) -> str:
    value = f"={taint['value']}" if taint.get('value') else ''
    return f"{taint['key']}{value}:{taint['effect']}"


def tolerates(toleration: dict, taint: dict) -> bool:
    """Port of the API's Toleration.ToleratesTaint."""
    if toleration.get('effect') and toleration['effect'] != taint['effect']:
        return False
    # An empty key matches every key; paired with Exists it tolerates every taint.
    if toleration.get('key') and toleration['key'] != taint['key']:
        return False
    if (toleration.get('operator') or 'Equal') == 'Exists':
        return True
    return (toleration.get('value') or '') == (taint.get('value') or '')


def _tolerated(pod: PodResources, taint: dict) -> bool:
    return any(tolerates(t, taint) for t in pod.tolerations or [])


def requirement_matches(requirement: dict, value: Optional[str]) -> bool:
    """One NodeSelectorRequirement against a label or field value; None when the node has none."""
    operator, values = requirement['operator'], requirement.get('values') or []
    if operator == 'In':
        return value is not None and value in values
    if operator == 'NotIn':
        return value is None or value not in values
    if operator == 'Exists':
        return value is not None
    if operator == 'DoesNotExist':
        return value is None
    if operator in ('Gt', 'Lt'):
        # Both sides must parse as integers, like the API's NodeSelectorRequirement.
        try:
            actual, bound = int(value or ''), int(values[0])
        except (ValueError, IndexError):
            return False
        return actual > bound if operator == 'Gt' else actual < bound
    return False


def node_selector_term_matches(term: dict, node: NodeResources) -> bool:
    """Expressions and fields of a term are ANDed; an empty term matches no node."""
    expressions, fields = term.get('matchExpressions') or [], term.get('matchFields') or []
    if not expressions and not fields:
        return False
    labels = node.labels or {}
    if not all(requirement_matches(r, labels.get(r['key'])) for r in expressions):
        return False
    # metadata.name is the only field a node selector may match on.
    return all(r['key'] == 'metadata.name' and requirement_matches(r, node.name) for r in fields)


def _fmt_requirement(r: dict) -> str:
    values = r.get('values') or []
    if r['operator'] in ('In', 'NotIn'):
        return f"{r['key']} {r['operator']} ({','.join(values)})"
    return f"{r['key']} {r['operator']} {values[0]}" if values else f"{r['key']} {r['operator']}"


def _fmt_term(term: dict) -> str:
    requirements = [*(term.get('matchExpressions') or []), *(term.get('matchFields') or [])]
    return ', '.join(_fmt_requirement(r) for r in requirements)


def _fmt_terms(terms: list) -> str:
    return ' or '.join(_fmt_term(t) for t in terms)


def _selects_everything(selector: Optional[dict]) -> bool:
    return selector is not None and not selector.get('matchLabels') and not selector.get('matchExpressions')


def label_selector_matches(selector: Optional[dict], labels: dict) -> bool:
    """A LabelSelector; None matches no pod, an empty one every pod."""
    if selector is None:
        return False
    if any(labels.get(k) != v for k, v in (selector.get('matchLabels') or {}).items()):
        return False
    return all(requirement_matches(r, labels.get(r['key'])) for r in selector.get('matchExpressions') or [])


def pod_term_matches(term: dict, owner_namespace: str, pod: PodResources) -> bool:
    """Whether `pod` is one the PodAffinityTerm of a pod in `owner_namespace` selects.

    No namespaces and no namespaceSelector mean the owner's namespace; an empty
    namespaceSelector means every namespace. A non-empty namespaceSelector needs
    namespace labels, which are not read, so only the listed namespaces count.
    """
    if not _selects_everything(term.get('namespaceSelector')):
        if pod.namespace not in (term.get('namespaces') or [owner_namespace]):
            return False
    return label_selector_matches(term.get('labelSelector'), pod.labels or {})


def _fmt_selector(term: dict) -> str:
    selector = term.get('labelSelector')
    if selector is None:
        return 'no labels'
    parts = [f'{k}={v}' for k, v in (selector.get('matchLabels') or {}).items()]
    parts += [_fmt_requirement(r) for r in selector.get('matchExpressions') or []]
    return ', '.join(parts) or 'any labels'


class _Domains():
    """Where the pods one incoming pod's inter-pod rules care about run, by (topologyKey, value)."""

    def __init__(self, pod: PodResources):
        self.pod = pod
        # Domains holding a pod that matches every affinity term, as kube-scheduler counts them.
        self.affine: set = set()
        self.any_affine = False
        # Domain → the first pod there that one of the incoming pod's anti-affinity terms selects.
        self.repelled: dict = {}
        # topologyKey → value → the first pod there whose own anti-affinity term selects the incoming pod.
        self.repelling: dict = {}

    def add(self, sample: PodResources, members: list) -> None:
        """Account for `members`, (pod, node labels) pairs alike to `sample` in namespace, labels and anti-affinity."""
        pod = self.pod
        affinity = pod.pod_affinity or []
        affine = bool(affinity) and all(pod_term_matches(t, pod.namespace, sample) for t in affinity)
        repelled = [t['topologyKey'] for t in pod.pod_anti_affinity or [] if pod_term_matches(t, pod.namespace, sample)]
        repelling = [t['topologyKey'] for t in sample.pod_anti_affinity or []
                     if pod_term_matches(t, sample.namespace, pod)]
        if not (affine or repelled or repelling):
            return
        self.any_affine = self.any_affine or affine
        for other, labels in members:
            if affine:
                self.affine.update((t['topologyKey'], labels[t['topologyKey']]) for t in affinity
                                   if t['topologyKey'] in labels)
            for key in repelled:
                if key in labels:
                    self.repelled.setdefault((key, labels[key]), other)
            for key in repelling:
                if key in labels:
                    self.repelling.setdefault(key, {}).setdefault(labels[key], other)


def _fits_node_affinity(pod: PodResources, node: NodeResources) -> bool:
    labels = node.labels or {}
    if any(labels.get(k) != v for k, v in (pod.node_selector or {}).items()):
        return False
    terms = pod.node_affinity or []
    return not terms or any(node_selector_term_matches(t, node) for t in terms)


def _tolerates_node(pod: PodResources, node: NodeResources) -> bool:
    taints = [*(node.taints or []), *([CORDON_TAINT] if node.unschedulable else [])]
    return all(t.get('effect') not in FILTER_EFFECTS or _tolerated(pod, t) for t in taints)


class _Spread():
    """Pods matching each of one incoming pod's spread constraints, per topology domain.

    Like kube-scheduler, only nodes carrying every constraint's topology key hold
    domains, and nodeAffinityPolicy / nodeTaintsPolicy Honor leave out nodes the
    pod could not be scheduled to anyway.
    """

    def __init__(self, pod: PodResources, nodes: list):
        self.pod = pod
        constraints = pod.topology_spread or []
        keys = {c['topologyKey'] for c in constraints}
        labelled = [n for n in nodes if keys <= (n.labels or {}).keys()]
        # Per constraint: node name → its domain value, and domain value → matching pods.
        self.domain_of: list = []
        self.counts: list = []
        for c in constraints:
            eligible = [n for n in labelled if self.__counts_node(c, n)]
            domain_of = {n.name: n.labels[c['topologyKey']] for n in eligible}
            self.domain_of.append(domain_of)
            self.counts.append({value: 0 for value in domain_of.values()})

    def __counts_node(self, constraint: dict, node: NodeResources) -> bool:
        if constraint['nodeAffinityPolicy'] == 'Honor' and not _fits_node_affinity(self.pod, node):
            return False
        return constraint['nodeTaintsPolicy'] != 'Honor' or _tolerates_node(self.pod, node)

    def add(self, sample: PodResources, members: list) -> None:
        """Account for `members`, (pod, node labels) pairs alike to `sample` in namespace and labels."""
        if sample.namespace != self.pod.namespace:
            return
        for c, domain_of, counts in zip(self.pod.topology_spread or [], self.domain_of, self.counts):
            if not label_selector_matches(c['labelSelector'], sample.labels or {}):
                continue
            for other, _ in members:
                value = domain_of.get(other.node_name)
                if value is not None:
                    counts[value] += 1


def _likeness(pod: PodResources) -> tuple:
    """Pods with the same key look the same to every inter-pod term, e.g. replicas of one workload."""
    return pod.namespace, frozenset((pod.labels or {}).items()), repr(pod.pod_anti_affinity or [])


class Placement():
    """Which pod runs on which node, for the inter-pod affinity predicates.

    A simulation calls place() for every pod it binds, so the next pod sees it.
    Bound pods are grouped by likeness, so a term is matched once per group
    rather than once per pod. The per-pod domain index is built on first use and
    then kept up to date as pods are placed.
    """

    def __init__(self, nodes: Iterable[NodeResources], pods: Iterable[PodResources]):
        self.__nodes = list(nodes)
        self.__labels = {n.name: n.labels or {} for n in self.__nodes}
        self.__pods = [p for p in pods if p.node_name in self.__labels]
        self.__has_anti_affinity = any(p.pod_anti_affinity for p in self.__pods)
        self.__groups: Optional[dict] = None
        self.__domains: dict = {}
        self.__spreads: dict = {}
        self.__last_domains: tuple = (None, None)
        self.__last_spread: tuple = (None, None)

    @property
    def has_anti_affinity(self) -> bool:
        return self.__has_anti_affinity

    def __grouped(self) -> dict:
        """likeness → (sample pod, [(pod, node labels)]); built on first use, as most pods carry no terms."""
        if self.__groups is None:
            self.__groups = {}
            for p in self.__pods:
                self.__join(self.__groups, p)
        return self.__groups

    def __join(self, groups: dict, pod: PodResources) -> None:
        groups.setdefault(_likeness(pod), (pod, []))[1].append((pod, self.__labels[pod.node_name]))

    def place(self, pod: PodResources, node_name: str) -> None:
        bound = pod._replace(node_name=node_name)
        self.__pods.append(bound)
        self.__has_anti_affinity = self.__has_anti_affinity or bool(pod.pod_anti_affinity)
        if self.__groups is None:
            return
        self.__join(self.__groups, bound)
        member = [(bound, self.__labels[node_name])]
        for index in (*self.__domains.values(), *self.__spreads.values()):
            index.add(bound, member)

    def domains(self, pod: PodResources) -> _Domains:
        if self.__last_domains[0] is pod:
            return self.__last_domains[1]
        key = (*_likeness(pod), repr(pod.pod_affinity or []))
        domains = self.__domains.get(key)
        if domains is None:
            domains = _Domains(pod)
            for sample, members in self.__grouped().values():
                domains.add(sample, members)
            self.__domains[key] = domains
        self.__last_domains = (pod, domains)
        return domains

    def spread(self, pod: PodResources) -> _Spread:
        # A simulation filters one pod against every node in a row: skip the key for it.
        if self.__last_spread[0] is pod:
            return self.__last_spread[1]
        key = repr((pod.namespace, sorted((pod.labels or {}).items()), pod.topology_spread, pod.node_selector,
                    pod.node_affinity, pod.tolerations))
        spread = self.__spreads.get(key)
        if spread is None:
            spread = _Spread(pod, self.__nodes)
            for sample, members in self.__grouped().values():
                spread.add(sample, members)
            self.__spreads[key] = spread
        self.__last_spread = (pod, spread)
        return spread


def _spread_reasons(pod: PodResources, node: NodeResources, placement: Placement) -> list:
    constraints = pod.topology_spread or []
    if not constraints:
        return []
    labels = node.labels or {}
    missing = [c['topologyKey'] for c in constraints if c['topologyKey'] not in labels]
    if missing:
        return [_reason('topology-spread', f'Topology spread: node has no {missing[0]} label')]
    spread = placement.spread(pod)
    for c, counts in zip(constraints, spread.counts):
        key = c['topologyKey']
        # Fewer domains than minDomains: the missing ones count as empty.
        too_few = c.get('minDomains') and len(counts) < c['minDomains']
        minimum = 0 if too_few else min(counts.values(), default=0)
        count = counts.get(labels[key], 0)
        own = 1 if label_selector_matches(c['labelSelector'], pod.labels or {}) else 0
        skew = count + own - minimum
        if skew > c['maxSkew']:
            pods = f"{count} matching pod{'' if count == 1 else 's'}"
            return [_reason('topology-spread',
                            f"Topology spread: {key}={labels[key]} would have skew {skew}, over maxSkew "
                            f"{c['maxSkew']} ({pods} there, {minimum} in the emptiest {key})")]
    return []


def _inter_pod_reasons(pod: PodResources, node: NodeResources, placement: Placement) -> list:
    if not (pod.pod_affinity or pod.pod_anti_affinity or placement.has_anti_affinity):
        return []
    domains = placement.domains(pod)
    labels = node.labels or {}
    reasons = []

    affinity = pod.pod_affinity or []
    missing = [t['topologyKey'] for t in affinity if t['topologyKey'] not in labels]
    unmet = [t for t in affinity if (t['topologyKey'], labels.get(t['topologyKey'])) not in domains.affine]
    # The first pod of a group with affinity to itself would otherwise stay Pending forever.
    first = not domains.any_affine and all(pod_term_matches(t, pod.namespace, pod) for t in affinity)
    if missing:
        reasons.append(_reason('pod-affinity', f'Pod affinity: node has no {missing[0]} label'))
    elif unmet and not first:
        t = unmet[0]
        reasons.append(_reason('pod-affinity',
                               f"Pod affinity: no pod matching {_fmt_selector(t)} in the same {t['topologyKey']}"))

    for t in pod.pod_anti_affinity or []:
        key = t['topologyKey']
        other = domains.repelled.get((key, labels[key])) if key in labels else None
        if other is not None:
            reasons.append(_reason('pod-anti-affinity', f'Pod anti-affinity: {other.namespace}/{other.name} '
                                                        f'matches {_fmt_selector(t)} in the same {key}'))
            return reasons
    for key, by_value in domains.repelling.items():
        other = by_value.get(labels[key]) if key in labels else None
        if other is not None:
            reasons.append(_reason('pod-anti-affinity',
                                   f'Pod anti-affinity: {other.namespace}/{other.name} repels it in the same {key}'))
            break
    return reasons


def _reason(slug: str, message: str) -> dict:
    return {'slug': slug, 'message': message}


def filter_node(pod: PodResources, node: NodeResources, free: dict, placement: Optional[Placement] = None) -> list:
    """Why `pod` cannot be placed on `node`, in filter order. Empty means it fits.

    Without a `placement`, the inter-pod predicates see a cluster with no other pods.
    """
    reasons = []

    if node.unschedulable and not _tolerated(pod, CORDON_TAINT):
        reasons.append(_reason('cordoned', 'Node is cordoned (unschedulable)'))
    # Only an explicit False counts, matching node_warnings.
    if (node.conditions or {}).get('Ready') is False and not _tolerated(pod, NOT_READY_TAINT):
        reasons.append(_reason('not-ready', 'Node is not Ready'))

    labels = node.labels or {}
    for key, value in (pod.node_selector or {}).items():
        if labels.get(key) != value:
            has = f'node has {key}={labels[key]}' if key in labels else f'node has no {key} label'
            reasons.append(_reason('node-selector', f'Node selector mismatch: requires {key}={value}, {has}'))
    # Terms are ORed: one matching term is enough.
    terms = pod.node_affinity or []
    if terms and not any(node_selector_term_matches(t, node) for t in terms):
        reasons.append(_reason('node-affinity', f'Node affinity mismatch: requires {_fmt_terms(terms)}'))

    for taint in node.taints or []:
        if taint.get('effect') in FILTER_EFFECTS and not _tolerated(pod, taint):
            reasons.append(_reason('taint', f'Untolerated taint: {_fmt_taint(taint)}'))

    if free['pods'] is not None and free['pods'] < 1:
        reasons.append(_reason('insufficient-pods', 'Too many pods: node pod limit reached'))
    cpu, memory = pod.cpu or 0, pod.memory or 0
    if cpu > free['cpu']:
        reasons.append(_reason('insufficient-cpu',
                               f"Insufficient CPU: requires {fmt_cpu(cpu)}, available {fmt_cpu(free['cpu'])}"))
    if memory > free['memory']:
        reasons.append(_reason('insufficient-memory',
                               f"Insufficient memory: requires {fmt_memory(memory)}, available {fmt_memory(free['memory'])}"))
    for name, amount in sorted((pod.extended or {}).items()):
        available = free['extended'].get(name, 0)
        if amount > available:
            reasons.append(_reason('insufficient-extended',
                                   f'Insufficient {name}: requires {fmt_extended(name, amount)}, '
                                   f'available {fmt_extended(name, available)}'))

    placement = placement if placement is not None else Placement([node], [])
    reasons.extend(_spread_reasons(pod, node, placement))
    reasons.extend(_inter_pod_reasons(pod, node, placement))
    return reasons


def _public_free(free: dict) -> dict:
    return {'cpu': max(0, free['cpu']), 'memory': max(0.0, free['memory']),
            'pods': max(0, free['pods']) if free['pods'] is not None else None,
            'extended': {name: max(0, amount) for name, amount in free['extended'].items()}}


class UnknownTemplate(ValueError):
    """A hypothetical node's template names no node of the cluster."""


def _transient(taint: dict) -> bool:
    key = taint.get('key') or ''
    return key.startswith(TRANSIENT_TAINT_PREFIXES) or key in TRANSIENT_TAINT_KEYS


def hypothetical_nodes(specs: Iterable[dict], nodes: Iterable[NodeResources]) -> list:
    """Fake nodes for a what-if run, from {template | shape, count} entries.

    A template copies an existing node's allocatable, labels and taints, minus
    the taints a fresh node would not carry yet; a shape is a NodeResources
    whose name is ignored. Every copy is Ready and schedulable, gets its own
    kubernetes.io/hostname and is named hypothetical-<instance-type>-<n>, or
    hypothetical-custom-<n> without an instance-type label. The simulations
    also bind daemon_pods() to each. Raises UnknownTemplate for a template
    that names no node.
    """
    by_name = {n.name: n for n in nodes}
    made: list = []
    numbered: Counter = Counter()
    for spec in specs:
        template = spec.get('template')
        base: Optional[NodeResources] = spec.get('shape') if template is None else by_name.get(template)
        if base is None:
            raise UnknownTemplate(f'Template node not found: {template}')
        labels = dict(base.labels or {})
        kind = node_topology(labels)['instanceType'] or 'custom'
        taints = [t for t in base.taints or [] if not _transient(t)]
        for _ in range(spec['count']):
            numbered[kind] += 1
            name = f'hypothetical-{kind}-{numbered[kind]}'
            made.append(NodeResources(name, base.cpu or 0, base.memory or 0, False, list(taints), {'Ready': True},
                                      dict(base.extended or {}), {**labels, HOSTNAME_LABEL: name},
                                      base.allocatable_pods))
    return made


def daemon_pods(node: NodeResources, template: Optional[str], pods: Iterable[PodResources]) -> list:
    """The DaemonSet pods a fresh `node` would run, bound to it.

    A template's copy runs the template's own. A custom shape runs one pod of
    every DaemonSet whose pod selects and tolerates it.
    """
    if template is not None:
        own = [p for p in pods if p.node_name == template and p.owner_kind == 'DaemonSet']
    else:
        samples: dict = {}
        for p in sorted(pods, key=lambda p: p.name):
            if p.owner_kind == 'DaemonSet' and p.node_name is not None:
                samples.setdefault((p.namespace, p.owner_name), p)
        own = [p for p in samples.values() if _fits_node_affinity(p, node) and _tolerates_node(p, node)]
    return [p._replace(name=f'{p.owner_name or p.name}-{node.name}', node_name=node.name) for p in own]


def _describe_hypothetical(node: NodeResources, template: Optional[str], daemons: list) -> dict:
    """A fake node as the responses list it, so the UI can draw it sized by allocatable."""
    return {'name': node.name, 'template': template, 'cpu': node.cpu, 'memory': node.memory,
            'pods': node.allocatable_pods, 'extended': dict(node.extended or {}),
            'labels': dict(node.labels or {}), 'taints': list(node.taints or []),
            'topology': node_topology(node.labels),
            'daemonPods': [{'pod': p.name, 'namespace': p.namespace, 'cpu': p.cpu or 0, 'memory': p.memory or 0,
                            'extended': dict(p.extended or {})} for p in daemons]}


def _with_hypothetical(specs: list, nodes: list, pods: list) -> tuple:
    """The cluster plus the fake nodes of `specs` and their DaemonSet pods.

    Returns (nodes, pods, fake node names, the fake nodes as responses list them).
    """
    extra_nodes = hypothetical_nodes(specs, nodes)
    templates = [spec.get('template') for spec in specs for _ in range(spec['count'])]
    daemons: list = []
    described = []
    for node, template in zip(extra_nodes, templates):
        own = daemon_pods(node, template, pods)
        daemons += own
        described.append(_describe_hypothetical(node, template, own))
    return [*nodes, *extra_nodes], [*pods, *daemons], {n.name for n in extra_nodes}, described


def simulate_fit(pod: PodResources, nodes: Iterable[NodeResources], pods: Iterable[PodResources],
                 extra: Iterable[dict] = ()) -> dict:
    """Verdict per node for one hypothetical pod, against the cluster as it is now.

    `extra` are hypothetical_nodes() specs, added to the cluster for this run.
    """
    all_nodes, all_pods, fake, described = _with_hypothetical(list(extra), list(nodes), list(pods))
    free = free_capacity(all_nodes, all_pods)
    placement = Placement(all_nodes, all_pods)
    results = []
    for node in all_nodes:
        reasons = filter_node(pod, node, free[node.name], placement)
        results.append({'node': node.name, 'fits': not reasons, 'reasons': reasons,
                        'free': _public_free(free[node.name]), 'hypothetical': node.name in fake})
    return {'fits': sum(r['fits'] for r in results), 'total': len(results), 'nodes': results,
            'hypotheticalNodes': described}


def _unschedulable_summary(reasons_per_node: list) -> list:
    """Scheduler-style tally: how many nodes rejected the pod for each reason."""
    # A node counts once per slug, even when it fails e.g. two nodeSelector keys.
    counts = Counter(slug for reasons in reasons_per_node for slug in {r['slug'] for r in reasons})
    return [{'slug': slug, 'nodes': counts[slug]} for slug in REASON_ORDER if counts[slug]]


def _headroom(pod: PodResources, alloc: dict, free: dict) -> float:
    """Least-allocated score: the tighter axis's share left after placing the pod."""
    cpu = (free['cpu'] - (pod.cpu or 0)) / alloc['cpu'] if alloc['cpu'] else 0.0
    memory = (free['memory'] - (pod.memory or 0)) / alloc['memory'] if alloc['memory'] else 0.0
    return min(cpu, memory)


def _biggest_first(pods: list, alloc: dict) -> list:
    """First-fit decreasing order: by dominant share of the largest node, then by name."""
    max_cpu = max((a['cpu'] for a in alloc.values()), default=0) or 1
    max_memory = max((a['memory'] for a in alloc.values()), default=0) or 1
    return sorted(pods, key=lambda p: (-max((p.cpu or 0) / max_cpu, (p.memory or 0) / max_memory), p.name))


def _bind_best(pod: PodResources, nodes: list, free: dict, placement: Placement, alloc: dict) -> tuple:
    """Bind `pod` to the passing node with the most headroom left: (node name, []), or (None, reasons per node)."""
    verdicts = [(n, filter_node(pod, n, free[n.name], placement)) for n in nodes]
    passing = [n for n, reasons in verdicts if not reasons]
    if not passing:
        return None, [reasons for _, reasons in verdicts]
    target = max(passing, key=lambda n: (_headroom(pod, alloc[n.name], free[n.name]), n.name))
    _reserve(free[target.name], pod)
    placement.place(pod, target.name)
    return target.name, []


def simulate_pending(nodes: Iterable[NodeResources], pods: Iterable[PodResources], extra: Iterable[dict] = ()) -> dict:
    """Where the pods waiting for a node would go, packed like a drain's evicted pods.

    Biggest first, each on the passing node with the most headroom left. Pods
    held back by schedulingGates are listed, not simulated: the scheduler does
    not look at them until every gate is removed. `extra` are
    hypothetical_nodes() specs added to the cluster for this run.
    """
    all_nodes, all_pods, fake, described = _with_hypothetical(list(extra), list(nodes), list(pods))
    free = free_capacity(all_nodes, all_pods)
    placement = Placement(all_nodes, all_pods)
    alloc = {n.name: _allocatable(n) for n in all_nodes}

    unbound = [p for p in all_pods if p.node_name is None]
    gated = [{'pod': p.name, 'namespace': p.namespace, 'gates': list(p.scheduling_gates)}
             for p in unbound if p.scheduling_gates]
    placements, pending = [], []
    for pod in _biggest_first([p for p in unbound if not p.scheduling_gates], alloc):
        entry = {'pod': pod.name, 'namespace': pod.namespace, 'cpu': pod.cpu or 0, 'memory': pod.memory or 0}
        target, rejections = _bind_best(pod, all_nodes, free, placement, alloc)
        if target is None:
            pending.append({**entry, 'reasons': _unschedulable_summary(rejections)})
        else:
            placements.append({**entry, 'to': target, 'hypothetical': target in fake})

    return {
        'fits': not pending,
        'total': len(placements) + len(pending),
        'nodes': len(all_nodes),
        'placements': placements,
        'pending': pending,
        'gated': sorted(gated, key=lambda g: (g['namespace'], g['pod'])),
        'hypotheticalNodes': described,
    }


def simulate_drain(node_name: str, nodes: Iterable[NodeResources], pods: Iterable[PodResources],
                   pdbs: Optional[Iterable[DisruptionBudget]] = None, extra: Iterable[dict] = ()) -> Optional[dict]:
    """Re-place every evictable pod of `node_name` onto the rest of the cluster.

    First-fit decreasing: the biggest pods (by dominant share) are placed first,
    each on the passing node with the most headroom left afterwards. In that order,
    each eviction draws on the PodDisruptionBudgets selecting the pod; once one is
    used up, its remaining pods are blocked and stay. `pdbs` None means they could
    not be read, and the result says so. `extra` are hypothetical_nodes() specs the
    pods may also go to. Returns None when the node does not exist.
    """
    real = list(nodes)
    if not any(n.name == node_name for n in real):
        return None
    all_nodes, all_pods, fake, described = _with_hypothetical(list(extra), real, list(pods))
    result = _drain(node_name, all_nodes, all_pods, list(pdbs) if pdbs is not None else None, fake)
    return {**result, 'hypotheticalNodes': described}


def _drain(node_name: str, nodes: list, pods: list, pdbs: Optional[list] = None, fake: Collection = ()) -> dict:
    remaining = [n for n in nodes if n.name != node_name]
    free = free_capacity(remaining, pods)
    placement = Placement(remaining, pods)
    alloc = {n.name: _allocatable(n) for n in remaining}

    skipped, evicted = [], []
    for pod in (p for p in pods if p.node_name == node_name):
        why = DRAIN_SKIPPED_OWNERS.get(pod.owner_kind)
        if why:
            skipped.append({'pod': pod.name, 'namespace': pod.namespace, 'reason': why})
        else:
            evicted.append(pod)

    # Evictions from one drain draw on the same budgets; they refill only once the pods are healthy elsewhere.
    allowance = {(b.namespace, b.name): b.disruptions_allowed for b in pdbs or []}
    placements, pending, blocked = [], [], []
    for pod in _biggest_first(evicted, alloc):
        budgets = [b for b in pdbs or []
                   if b.namespace == pod.namespace and label_selector_matches(b.selector, pod.labels or {})]
        exhausted = next((b for b in budgets if allowance[(b.namespace, b.name)] < 1), None)
        if exhausted is not None:
            blocked.append({'pod': pod.name, 'namespace': pod.namespace, 'pdb': exhausted.name,
                            'disruptionsAllowed': exhausted.disruptions_allowed})
            continue
        for b in budgets:
            allowance[(b.namespace, b.name)] -= 1
        entry = {'pod': pod.name, 'namespace': pod.namespace, 'cpu': pod.cpu or 0, 'memory': pod.memory or 0,
                 # A naked pod is deleted by the drain and nothing recreates it.
                 'unmanaged': pod.owner_kind is None}
        target, rejections = _bind_best(pod, remaining, free, placement, alloc)
        if target is None:
            pending.append({**entry, 'reasons': _unschedulable_summary(rejections)})
            continue
        placements.append({**entry, 'to': target, 'hypothetical': target in fake})

    return {
        'node': node_name,
        'fits': not pending,
        'remainingNodes': len(remaining),
        'placements': placements,
        'pending': pending,
        'skipped': skipped,
        # Pods `kubectl drain` would keep retrying to evict; they stay on the node.
        'blocked': blocked,
        'pdbsChecked': pdbs is not None,
    }


def _blockers(node: NodeResources, drain: dict) -> list:
    """Why `node` cannot go even on its own, as {slug, pods}; pods is 0 for node-level blockers."""
    blockers = []
    if any(key in (node.labels or {}) for key in CONTROL_PLANE_LABELS):
        blockers.append({'slug': 'control-plane', 'pods': 0})
    if drain['pending']:
        blockers.append({'slug': 'pending', 'pods': len(drain['pending'])})
    if drain['blocked']:
        blockers.append({'slug': 'pdb', 'pods': len(drain['blocked'])})
    unmanaged = sum(p['unmanaged'] for p in drain['placements'] + drain['pending'])
    if unmanaged:
        blockers.append({'slug': 'unmanaged', 'pods': unmanaged})
    return blockers


def _utilisation(node: NodeResources, drain: dict) -> float:
    """Dominant share of the node's allocatable taken by the pods a drain would move."""
    moved = drain['placements'] + drain['pending']
    alloc = _allocatable(node)
    cpu = sum(p['cpu'] for p in moved) / alloc['cpu'] if alloc['cpu'] else 0.0
    memory = sum(p['memory'] for p in moved) / alloc['memory'] if alloc['memory'] else 0.0
    return max(cpu, memory)


def _rebind(pods: list, placements: list) -> list:
    """The pods as they would be bound once a drain's placements are carried out."""
    moves = {(p['namespace'], p['pod']): p['to'] for p in placements}
    return [p._replace(node_name=moves[(p.namespace, p.name)]) if (p.namespace, p.name) in moves else p
            for p in pods]


def simulate_consolidation(nodes: Iterable[NodeResources], pods: Iterable[PodResources],
                           pdbs: Optional[Iterable[DisruptionBudget]] = None) -> dict:
    """Which nodes could be emptied onto the rest of the cluster, alone and together.

    Every node first gets an independent drain verdict. Those cannot be added up —
    two nodes may each be removable alone but not together — so the removable ones
    are then drained one after another, least utilised first like cluster-autoscaler,
    each against the cluster left by the previous steps. Removing a node only takes
    capacity away, so a node that cannot go alone is never a candidate for the plan.
    Each step gets the budgets afresh: its evicted pods are running again before the
    next node goes. `pdbs` None means they could not be read.
    """
    nodes, pods = list(nodes), list(pods)
    pdbs = list(pdbs) if pdbs is not None else None

    verdicts = []
    for node in nodes:
        drain = _drain(node.name, nodes, pods, pdbs)
        moved = drain['placements'] + drain['pending']
        alloc = _allocatable(node)
        blockers = _blockers(node, drain)
        verdicts.append({
            'node': node.name,
            'removable': not blockers,
            'blockers': blockers,
            'evictable': len(moved),
            'cpu': sum(p['cpu'] for p in moved),
            'memory': sum(p['memory'] for p in moved),
            'utilisation': _utilisation(node, drain),
            'allocatable': {'cpu': alloc['cpu'], 'memory': alloc['memory'], 'extended': alloc['extended']},
        })

    candidates = sorted((v for v in verdicts if v['removable']), key=lambda v: (v['utilisation'], v['node']))
    removed: list = []
    plan, working = [], pods
    for v in candidates:
        left = [n for n in nodes if n.name not in removed]
        drain = _drain(v['node'], left, working, pdbs)
        if not drain['fits'] or drain['blocked']:
            v['conflictWith'] = list(removed)
            continue
        removed.append(v['node'])
        working = _rebind(working, drain['placements'])
        plan.append({'node': v['node'], 'placements': drain['placements'], 'skipped': drain['skipped'],
                     'freed': v['allocatable']})

    freed: dict = {'nodes': len(plan), 'cpu': 0, 'memory': 0.0, 'extended': {}}
    for step in plan:
        freed['cpu'] += step['freed']['cpu']
        freed['memory'] += step['freed']['memory']
        for name, amount in step['freed']['extended'].items():
            freed['extended'][name] = freed['extended'].get(name, 0) + amount

    for v in verdicts:
        v['inPlan'] = v['node'] in removed
        v.setdefault('conflictWith', None)

    return {'total': len(nodes), 'nodes': verdicts, 'plan': plan, 'freed': freed, 'pdbsChecked': pdbs is not None}


def simulate_replicas(namespace: str, kind: str, name: str, count: int,
                      nodes: Iterable[NodeResources], pods: Iterable[PodResources]) -> Optional[dict]:
    """Place `count` more copies of a workload's pod onto the cluster, one after another.

    The workload's first pod by name is the template: its effective requests,
    nodeSelector, node and pod affinity, and tolerations. Each copy goes to the
    passing node with the most headroom left, against the room and the pods the
    previous copies left behind. A copy that fits nowhere changes nothing, so no
    later copy would fit either, and placement stops there. The first k placements
    of a run are the run for k. Returns None when no pod belongs to the workload.
    """
    nodes, pods = list(nodes), list(pods)
    members = [p for p in pods if (p.namespace, p.owner_kind, p.owner_name) == (namespace, kind, name)]
    if not members:
        return None
    template = min(members, key=lambda p: p.name)
    free = free_capacity(nodes, pods)
    placement = Placement(nodes, pods)
    alloc = {n.name: _allocatable(n) for n in nodes}

    placements: list = []
    for i in range(count):
        copy = template._replace(name=f'{name}-sim-{i + 1}', node_name=None)
        passing = [n for n in nodes if not filter_node(copy, n, free[n.name], placement)]
        if not passing:
            break
        target = max(passing, key=lambda n: (_headroom(copy, alloc[n.name], free[n.name]), n.name))
        _reserve(free[target.name], copy)
        placement.place(copy, target.name)
        placements.append(target.name)

    placed = Counter(placements)
    # Whether each node could take one more copy: at the stop, why the next replica stays Pending.
    verdicts = [{'node': n.name, 'replicas': placed[n.name],
                 'reasons': filter_node(template, n, free[n.name], placement)} for n in nodes]
    fits = len(placements) == count
    return {
        'workload': {'namespace': namespace, 'kind': kind, 'name': name},
        'template': {'pod': template.name, 'cpu': template.cpu or 0, 'memory': template.memory or 0,
                     'extended': dict(template.extended or {}), 'nodeSelector': dict(template.node_selector or {}),
                     'tolerations': list(template.tolerations or []),
                     'nodeAffinity': list(template.node_affinity or []),
                     'podAffinity': list(template.pod_affinity or []),
                     'podAntiAffinity': list(template.pod_anti_affinity or []),
                     'topologySpread': list(template.topology_spread or [])},
        'requested': count,
        'placed': len(placements),
        'fits': fits,
        'total': len(nodes),
        'placements': placements,
        'nodes': verdicts,
        'pending': [] if fits else _unschedulable_summary([v['reasons'] for v in verdicts]),
    }
