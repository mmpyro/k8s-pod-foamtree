"""Dry-run scheduler: the kube-scheduler filter phase, emulated against free allocatable.

Backs two what-if questions — "can this pod fit anywhere?" and "where would this
node's pods go if it were drained?". It deliberately covers only the predicates
that decide most placements: node condition, cordon, nodeSelector, taints and
tolerations, pod count, CPU and memory requests. Affinity/anti-affinity, topology
spread, PodDisruptionBudgets, volume zone binding and extended resources are out
of scope, so a "fits" verdict is necessary rather than sufficient.

Units follow the rest of the backend: CPU in millicores, memory in decimal kB.
"""
from collections import Counter
from typing import Iterable, Optional
from k8sfoam.src.common.dtos import NodeResources, PodResources


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
REASON_ORDER = ('cordoned', 'not-ready', 'node-selector', 'taint',
                'insufficient-pods', 'insufficient-cpu', 'insufficient-memory')


def _allocatable(node: NodeResources) -> dict:
    """What the scheduler may fill, falling back to capacity on a node that omits it."""
    return {
        'cpu': node.allocatable_cpu if node.allocatable_cpu is not None else node.cpu or 0,
        'memory': node.allocatable_memory if node.allocatable_memory is not None else node.memory or 0,
        # No pod limit reported means no pod-count check, not a limit of zero.
        'pods': node.allocatable_pods,
    }


def free_capacity(nodes: Iterable[NodeResources], pods: Iterable[PodResources]) -> dict:
    """Allocatable minus the effective requests of every pod bound to each node."""
    free = {n.name: _allocatable(n) for n in nodes}
    for pod in pods:
        room = free.get(pod.node_name)
        if room is None:
            continue
        room['cpu'] -= pod.cpu or 0
        room['memory'] -= pod.memory or 0
        if room['pods'] is not None:
            room['pods'] -= 1
    return free


def fmt_cpu(millicores: float) -> str:
    return f'{max(0, round(millicores))}m'


def fmt_memory(kb: float) -> str:
    """Binary units, like `kubectl describe node`: kB are decimal, so convert via bytes."""
    mib = max(0.0, kb) * 1000 / (1024 * 1024)
    return f'{mib / 1024:.1f}Gi' if mib >= 1024 else f'{mib:.0f}Mi'


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


def _reason(slug: str, message: str) -> dict:
    return {'slug': slug, 'message': message}


def filter_node(pod: PodResources, node: NodeResources, free: dict) -> list:
    """Why `pod` cannot be placed on `node`, in filter order. Empty means it fits."""
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

    return reasons


def _public_free(free: dict) -> dict:
    return {'cpu': max(0, free['cpu']), 'memory': max(0.0, free['memory']),
            'pods': max(0, free['pods']) if free['pods'] is not None else None}


def simulate_fit(pod: PodResources, nodes: Iterable[NodeResources], pods: Iterable[PodResources]) -> dict:
    """Verdict per node for one hypothetical pod, against the cluster as it is now."""
    nodes = list(nodes)
    free = free_capacity(nodes, pods)
    results = []
    for node in nodes:
        reasons = filter_node(pod, node, free[node.name])
        results.append({'node': node.name, 'fits': not reasons, 'reasons': reasons,
                        'free': _public_free(free[node.name])})
    return {'fits': sum(r['fits'] for r in results), 'total': len(results), 'nodes': results}


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


def simulate_drain(node_name: str, nodes: Iterable[NodeResources], pods: Iterable[PodResources]) -> Optional[dict]:
    """Re-place every evictable pod of `node_name` onto the rest of the cluster.

    First-fit decreasing: the biggest pods (by dominant share) are placed first,
    each on the passing node with the most headroom left afterwards. Returns None
    when the node does not exist.
    """
    nodes, pods = list(nodes), list(pods)
    if not any(n.name == node_name for n in nodes):
        return None

    remaining = [n for n in nodes if n.name != node_name]
    free = free_capacity(remaining, pods)
    alloc = {n.name: _allocatable(n) for n in remaining}

    skipped, evicted = [], []
    for pod in (p for p in pods if p.node_name == node_name):
        why = DRAIN_SKIPPED_OWNERS.get(pod.owner_kind)
        if why:
            skipped.append({'pod': pod.name, 'namespace': pod.namespace, 'reason': why})
        else:
            evicted.append(pod)

    max_cpu = max((a['cpu'] for a in alloc.values()), default=0) or 1
    max_memory = max((a['memory'] for a in alloc.values()), default=0) or 1
    evicted.sort(key=lambda p: (-max((p.cpu or 0) / max_cpu, (p.memory or 0) / max_memory), p.name))

    placements, pending = [], []
    for pod in evicted:
        entry = {'pod': pod.name, 'namespace': pod.namespace, 'cpu': pod.cpu or 0, 'memory': pod.memory or 0,
                 # A naked pod is deleted by the drain and nothing recreates it.
                 'unmanaged': pod.owner_kind is None}
        verdicts = [(n, filter_node(pod, n, free[n.name])) for n in remaining]
        passing = [n for n, reasons in verdicts if not reasons]
        if not passing:
            pending.append({**entry, 'reasons': _unschedulable_summary([r for _, r in verdicts])})
            continue
        target = max(passing, key=lambda n: (_headroom(pod, alloc[n.name], free[n.name]), n.name))
        room = free[target.name]
        room['cpu'] -= pod.cpu or 0
        room['memory'] -= pod.memory or 0
        if room['pods'] is not None:
            room['pods'] -= 1
        placements.append({**entry, 'to': target.name})

    return {
        'node': node_name,
        'fits': not pending,
        'remainingNodes': len(remaining),
        'placements': placements,
        'pending': pending,
        'skipped': skipped,
    }
