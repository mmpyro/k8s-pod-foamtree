from k8sfoam.src.common.dtos import NodeResources, PodResources
from k8sfoam.src.common.node_topology import SPOT, node_topology


# A pod reserving more than this share of a node leaves no room to reschedule
# it anywhere else, and a node drain takes the whole workload down with it.
MONOLITH_SHARE = 0.8

# How far apart a pod's CPU and memory shares of its node may drift before the
# leftover capacity on the other axis is effectively stranded.
RATIO_ASYMMETRY_FACTOR = 4

# Below this share on its dominant axis a pod is too small to strand anything,
# so an extreme ratio on a tiny sidecar is not worth reporting.
RATIO_MIN_SHARE = 0.10

# A workload needs this many replicas before their placement can be a single
# point of failure; with fewer there is nothing to spread.
SPOF_MIN_REPLICAS = 2

# Controllers whose replicas are interchangeable, so spreading them is the point.
# A DaemonSet is pinned one per node by design and a Job runs to completion.
SPOF_KINDS = ('Deployment', 'StatefulSet', 'ReplicaSet')

# Long-running services where one replica means every rollout, drain or eviction
# is downtime. Counted from the scheduled pods; spec.replicas is never read.
SINGLE_REPLICA_KINDS = ('Deployment', 'StatefulSet')


def _share(requested: float, capacity: float) -> float:
    return requested / capacity if capacity else 0.0


def pod_findings(pod: PodResources, node: NodeResources) -> list:
    """Best-practice violations for a pod on its node, in a fixed order.

    Returns render-ready slugs, like node_warnings — the rules live here so the
    2D and 3D views cannot disagree.
    """
    findings = []

    # Only regular containers: init containers finish before the pod runs, so
    # their requests and limits say nothing about steady-state hygiene.
    containers = pod.containers or []
    if any(not c.cpu or not c.memory for c in containers):
        findings.append('missing-requests')
    if any(c.memory_limit is None for c in containers):
        findings.append('missing-limits')

    cpu_share = _share(pod.cpu or 0, node.cpu or 0)
    mem_share = _share(pod.memory or 0, node.memory or 0)

    if cpu_share > MONOLITH_SHARE or mem_share > MONOLITH_SHARE:
        findings.append('monolith')

    # A zero on either axis is already a missing-requests finding, and has no ratio.
    if cpu_share > 0 and mem_share > 0:
        high, low = max(cpu_share, mem_share), min(cpu_share, mem_share)
        if high >= RATIO_MIN_SHARE and high / low >= RATIO_ASYMMETRY_FACTOR:
            findings.append('ratio-asymmetry')

    # Nothing recreates a pod without a controller after a drain or node failure.
    # A static pod is owned by its node ('Node'), and the kubelet restarts it.
    if pod.owner_kind is None:
        findings.append('naked-pod')

    return findings


def _spof(pods: list, nodes_by_name: dict, multi_zone: bool) -> list:
    """Failure domains every replica shares, so losing that one domain loses them all."""
    findings = []
    nodes = [nodes_by_name[p.node_name] for p in pods]
    topology = [node_topology(n.labels) for n in nodes]
    if len({n.name for n in nodes}) == 1:
        findings.append('spof-node')
    elif multi_zone:
        zones = {t['zone'] for t in topology}
        if len(zones) == 1 and None not in zones:
            findings.append('spof-zone')
    if all(t['capacityType'] == SPOT for t in topology):
        findings.append('spof-spot')
    return findings


def workload_findings(pods_by_workload: dict, nodes_by_name: dict) -> dict:
    """Rules that need every replica of a workload at once, in a fixed order.

    `pods_by_workload` maps (namespace, kind, name) to the workload's scheduled
    pods. Returns {(namespace, pod name): [slug, ...]}; each finding is added to
    every pod of the workload, after that pod's own pod_findings.
    """
    # On a single-zone or unlabelled cluster every workload shares its zone, and
    # there is no other zone to spread to, so that is not worth reporting.
    zones = {node_topology(n.labels)['zone'] for n in nodes_by_name.values()} - {None}
    multi_zone = len(zones) > 1

    result: dict = {}
    for (namespace, kind, _), pods in pods_by_workload.items():
        findings = []
        if kind in SINGLE_REPLICA_KINDS and len(pods) == 1:
            findings.append('single-replica')
        if kind in SPOF_KINDS and len(pods) >= SPOF_MIN_REPLICAS:
            findings += _spof(pods, nodes_by_name, multi_zone)
        if findings:
            for pod in pods:
                result[(namespace, pod.name)] = list(findings)
    return result
