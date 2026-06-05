from collections import namedtuple


PodResources = namedtuple('PodResources', 'name node_name cpu memory containers init_containers')
ContainerResources = namedtuple('ContainerResources', 'name cpu memory')
NodeResources = namedtuple('NodeResources', 'name cpu memory')
