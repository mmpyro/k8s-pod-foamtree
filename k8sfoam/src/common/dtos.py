from collections import namedtuple


# namespace/labels/qos_class back the frontend query bar (ns:, key=value, qos:).
# node_selector/tolerations/owner_kind back the scheduling simulator.
# They are declared last with defaults so existing positional construction keeps working.
PodResources = namedtuple(
    'PodResources',
    'name node_name cpu memory containers init_containers namespace labels qos_class '
    'node_selector tolerations owner_kind',
    defaults=('', None, None, None, None, None)
)
# memory_limit (kB, None when unset) backs the missing-limits audit rule.
ContainerResources = namedtuple('ContainerResources', 'name cpu memory memory_limit', defaults=(None,))
# unschedulable/taints/conditions back the node health markers (cordon, pressure,
# taints). labels/allocatable_* back the scheduling simulator; allocatable falls
# back to capacity when unset. Declared last with defaults so existing positional
# construction keeps working.
NodeResources = namedtuple(
    'NodeResources',
    'name cpu memory unschedulable taints conditions labels allocatable_cpu allocatable_memory allocatable_pods',
    defaults=(False, None, None, None, None, None, None)
)
