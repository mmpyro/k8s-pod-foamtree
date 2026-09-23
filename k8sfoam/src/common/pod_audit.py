from k8sfoam.src.common.dtos import NodeResources, PodResources


# A pod reserving more than this share of a node leaves no room to reschedule
# it anywhere else, and a node drain takes the whole workload down with it.
MONOLITH_SHARE = 0.8

# How far apart a pod's CPU and memory shares of its node may drift before the
# leftover capacity on the other axis is effectively stranded.
RATIO_ASYMMETRY_FACTOR = 4

# Below this share on its dominant axis a pod is too small to strand anything,
# so an extreme ratio on a tiny sidecar is not worth reporting.
RATIO_MIN_SHARE = 0.10


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

    return findings
