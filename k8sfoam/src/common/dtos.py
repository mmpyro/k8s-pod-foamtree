from collections import namedtuple


# `extended` is a plain dict mapping Kubernetes extended-resource key → numeric
# quantity (integer count for countable resources such as GPUs/TPUs; decimal kB
# for storage-type resources such as `ephemeral-storage`).
PodResources = namedtuple('PodResources', 'name node_name cpu memory containers init_containers extended')
ContainerResources = namedtuple('ContainerResources', 'name cpu memory extended')
NodeResources = namedtuple('NodeResources', 'name cpu memory extended')
