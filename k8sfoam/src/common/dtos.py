from collections import namedtuple


PodResources = namedtuple('PodResources', 'name node_name cpu memory containers')
ContainerResources = namedtuple('ContainerResources', 'name cpu memory')
NodeResources = namedtuple('NodeResources', 'name cpu memory')
