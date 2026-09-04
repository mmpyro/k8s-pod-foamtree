from collections import namedtuple


# namespace/labels/qos_class back the frontend query bar (ns:, key=value, qos:).
# They are declared last with defaults so existing positional construction keeps working.
PodResources = namedtuple(
    'PodResources',
    'name node_name cpu memory containers init_containers namespace labels qos_class',
    defaults=('', None, None)
)
ContainerResources = namedtuple('ContainerResources', 'name cpu memory')
NodeResources = namedtuple('NodeResources', 'name cpu memory')
