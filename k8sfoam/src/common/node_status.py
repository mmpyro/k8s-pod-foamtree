from typing import Optional
from k8sfoam.src.common.resources import is_device


# Conditions the kubelet raises when a node is running out of something. All of
# them report 'True' when the node is *unhealthy*, unlike Ready.
PRESSURE_CONDITIONS = ('MemoryPressure', 'DiskPressure', 'PIDPressure')

# Effects that actually keep the scheduler away. PreferNoSchedule is a soft hint,
# so it is reported in the node detail but never marks the node.
BLOCKING_EFFECTS = ('NoSchedule', 'NoExecute')

# Kubernetes adds this taint itself when a node is cordoned. spec.unschedulable
# already reports the same fact, so counting the taint too would mark every
# cordoned node twice.
CORDON_TAINT = 'node.kubernetes.io/unschedulable'

# Slug per pressure condition, so the frontend never has to case-convert.
PRESSURE_SLUGS = {
    'MemoryPressure': 'memory-pressure',
    'DiskPressure': 'disk-pressure',
    'PIDPressure': 'pid-pressure',
}


def node_warnings(unschedulable: bool, taints: Optional[list], conditions: Optional[dict]) -> list:
    """Reasons a node is a bad scheduling target, worst first.

    Returns render-ready slugs the frontend maps straight to a severity and a
    label — the classification lives here so the two views cannot disagree.
    """
    conditions = conditions or {}
    warnings = []

    if unschedulable:
        warnings.append('cordoned')
    # Only an explicit False counts: a node reporting no Ready condition at all
    # (conditions unset) must not be slandered as down.
    if conditions.get('Ready') is False:
        warnings.append('not-ready')

    warnings.extend(PRESSURE_SLUGS[name] for name in PRESSURE_CONDITIONS if conditions.get(name))

    if any(taint.get('effect') in BLOCKING_EFFECTS for taint in (taints or [])):
        warnings.append('tainted')

    return warnings


# CPU or memory requested past this share leaves no room for another pod, so any
# free GPU (or other device) on the node cannot be scheduled.
STRANDED_SHARE = 0.9


def stranded_devices(node, used: dict) -> bool:
    """True when the node still has free devices but no CPU or memory left to run them.

    `used` holds the node's summed pod requests, keyed like `_totals` in the extractor.
    """
    free_devices = any(is_device(name) and allocatable > used.get(name, 0)
                       for name, allocatable in (node.extended or {}).items())
    if not free_devices:
        return False
    cpu_full = bool(node.cpu) and used.get('cpu', 0) >= STRANDED_SHARE * node.cpu
    memory_full = bool(node.memory) and used.get('memory', 0) >= STRANDED_SHARE * node.memory
    return cpu_full or memory_full
