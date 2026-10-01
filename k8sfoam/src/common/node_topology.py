from typing import Optional


# Where each topology fact lives, first match wins. The well-known labels come
# first; the deprecated beta ones still show up on clusters upgraded in place.
ZONE_LABELS = ('topology.kubernetes.io/zone', 'failure-domain.beta.kubernetes.io/zone')
REGION_LABELS = ('topology.kubernetes.io/region', 'failure-domain.beta.kubernetes.io/region')
INSTANCE_TYPE_LABELS = ('node.kubernetes.io/instance-type', 'beta.kubernetes.io/instance-type')
# Karpenter, EKS managed node groups, GKE and AKS each name the pool differently.
NODE_POOL_LABELS = (
    'karpenter.sh/nodepool',
    'eks.amazonaws.com/nodegroup',
    'cloud.google.com/gke-nodepool',
    'kubernetes.azure.com/agentpool',
    'agentpool',
)

SPOT = 'spot'
ON_DEMAND = 'on-demand'
CAPACITY_TYPES = (SPOT, ON_DEMAND)

# Provider spellings of the capacity type, all folded onto SPOT / ON_DEMAND so
# the frontend groups an EKS 'SPOT' node with a Karpenter 'spot' one.
_CAPACITY_VALUES = {
    'spot': SPOT,
    'on-demand': ON_DEMAND,
    'on_demand': ON_DEMAND,
    'ondemand': ON_DEMAND,
}


def _first(labels: dict, keys: tuple) -> Optional[str]:
    for key in keys:
        value = labels.get(key)
        if value:
            return value
    return None


def _capacity_type(labels: dict) -> Optional[str]:
    for key in ('karpenter.sh/capacity-type', 'eks.amazonaws.com/capacityType'):
        value = labels.get(key)
        if value:
            return _CAPACITY_VALUES.get(value.lower())
    # GKE and AKS only mark the spot nodes; an unmarked node there could just as
    # well be an unmanaged one, so it is left unknown rather than called on-demand.
    if labels.get('cloud.google.com/gke-spot') == 'true':
        return SPOT
    if labels.get('kubernetes.azure.com/scalesetpriority') == 'spot':
        return SPOT
    return None


def node_topology(labels: Optional[dict]) -> dict:
    """Placement facts read from a node's labels; None wherever a fact is not labelled.

    Keys are camelCase because the dict goes to the frontend as is.
    """
    labels = labels or {}
    return {
        'zone': _first(labels, ZONE_LABELS),
        'region': _first(labels, REGION_LABELS),
        'instanceType': _first(labels, INSTANCE_TYPE_LABELS),
        'nodePool': _first(labels, NODE_POOL_LABELS),
        'capacityType': _capacity_type(labels),
    }
