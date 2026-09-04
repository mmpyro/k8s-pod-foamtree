from collections import namedtuple


# namespace/labels/qos_class back the frontend query bar (ns:, key=value, qos:).
# They are declared last with defaults so existing positional construction keeps working.
PodResources = namedtuple(
    'PodResources',
    'name node_name cpu memory containers init_containers namespace labels qos_class',
    defaults=('', None, None)
)
ContainerResources = namedtuple('ContainerResources', 'name cpu memory')
# unschedulable/taints/conditions back the node health markers (cordon, pressure,
# taints). Declared last with defaults so existing positional construction keeps working.
NodeResources = namedtuple(
    'NodeResources',
    'name cpu memory unschedulable taints conditions',
    defaults=(False, None, None)
)
