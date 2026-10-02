from collections import namedtuple


# namespace/labels/qos_class back the frontend query bar (ns:, key=value, qos:).
# They are declared last with defaults so existing positional construction keeps working.
PodResources = namedtuple(
    'PodResources',
    'name node_name cpu memory containers init_containers namespace labels qos_class extended',
    defaults=('', None, None, None)
)
# `extended` on every DTO maps a non-cpu/memory resource name (nvidia.com/gpu,
# ephemeral-storage, hugepages-2Mi) to a count, or to kB for byte-sized ones.
# memory_limit (kB, None when unset) backs the missing-limits audit rule.
ContainerResources = namedtuple('ContainerResources', 'name cpu memory memory_limit extended', defaults=(None, None))
# unschedulable/taints/conditions back the node health markers (cordon, pressure,
# taints); labels back the zone / node pool grouping. Declared last with defaults so
# existing positional construction keeps working.
NodeResources = namedtuple(
    'NodeResources',
    'name cpu memory unschedulable taints conditions extended labels',
    defaults=(False, None, None, None, None)
)
