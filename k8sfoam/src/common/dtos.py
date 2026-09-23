from collections import namedtuple


# namespace/labels/qos_class back the frontend query bar (ns:, key=value, qos:).
# They are declared last with defaults so existing positional construction keeps working.
PodResources = namedtuple(
    'PodResources',
    'name node_name cpu memory containers init_containers namespace labels qos_class',
    defaults=('', None, None)
)
# memory_limit (kB, None when unset) backs the missing-limits audit rule.
ContainerResources = namedtuple('ContainerResources', 'name cpu memory memory_limit', defaults=(None,))
# unschedulable/taints/conditions back the node health markers (cordon, pressure,
# taints). Declared last with defaults so existing positional construction keeps working.
NodeResources = namedtuple(
    'NodeResources',
    'name cpu memory unschedulable taints conditions',
    defaults=(False, None, None)
)
