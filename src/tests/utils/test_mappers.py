import bitmath  # type: ignore
from app.utils.mappers import FoamTreeMapper
from app.common.dtos import NodeResources, PodResources, ContainerResources
from pydash import py_ as _  # type: ignore


def test_should_return_foam_tree_map_of_cpu_resources():
    # Given
    node = [NodeResources('minikube', 2000, float(bitmath.GB(1).kB))]
    containers = [ContainerResources('etcd', 100, float(bitmath.MB(100).kB)), ContainerResources('side', 50, float(bitmath.MB(50).kB))]
    pods = [PodResources('etcd', 'minikube', 150, float(bitmath.MB(150).kB), containers)]
    mapper = FoamTreeMapper(node, pods)

    # When
    foamtree = mapper.transform_cpu_resources_to_foamtree()
    node_foam = _.find(foamtree['groups'], lambda item: item['label'] == 'minikube')
    pod_foam = _.find(node_foam['groups'], lambda item: item['label'] == 'etcd')
    empty_foam = _.find(node_foam['groups'], lambda item: item['label'] == 'empty')
    etcd_foam = _.find(pod_foam['groups'], lambda item: item['label'] == 'etcd')
    side_foam = _.find(pod_foam['groups'], lambda item: item['label'] == 'side')

    # Then
    assert node_foam['weight'] == 2000
    assert pod_foam['weight'] == 150
    assert empty_foam['weight'] == 1850
    assert empty_foam['color'] == '#ffffff'
    assert etcd_foam['weight'] == 100
    assert side_foam['weight'] == 50


def test_should_return_foam_tree_map_of_memory_resources():
    # Given
    node = [NodeResources('minikube', 2000, float(bitmath.GB(1).kB))]
    containers = [ContainerResources('etcd', 100, float(bitmath.MB(100).kB)), ContainerResources('side', 50, float(bitmath.MB(50).kB))]
    pods = [PodResources('etcd', 'minikube', 150, float(bitmath.MB(150).kB), containers)]
    mapper = FoamTreeMapper(node, pods)
    not_allocated_memory = float(bitmath.GB(1).kB) - float(bitmath.MB(150).kB)

    # When
    foamtree = mapper.transform_memory_resources_to_foamtree()
    node_foam = _.find(foamtree['groups'], lambda item: item['label'] == 'minikube')
    pod_foam = _.find(node_foam['groups'], lambda item: item['label'] == 'etcd')
    empty_foam = _.find(node_foam['groups'], lambda item: item['label'] == 'empty')
    etcd_foam = _.find(pod_foam['groups'], lambda item: item['label'] == 'etcd')
    side_foam = _.find(pod_foam['groups'], lambda item: item['label'] == 'side')

    # Then
    assert node_foam['weight'] == float(bitmath.GB(1).kB)
    assert pod_foam['weight'] == float(bitmath.MB(150).kB)
    assert empty_foam['weight'] == not_allocated_memory
    assert empty_foam['color'] == '#ffffff'
    assert etcd_foam['weight'] == float(bitmath.MB(100).kB)
    assert side_foam['weight'] == float(bitmath.MB(50).kB)
