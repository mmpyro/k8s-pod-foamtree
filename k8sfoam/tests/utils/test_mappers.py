import bitmath  # type: ignore
from k8sfoam.src.utils.mappers import FoamTreeMapper
from k8sfoam.src.common.dtos import NodeResources, PodResources, ContainerResources
from pydash import py_ as _  # type: ignore


def test_should_return_foam_tree_map_of_cpu_resources():
    # Given
    node = [NodeResources('minikube', 2000, float(bitmath.GB(1).kB))]
    containers = [ContainerResources('etcd', 100, float(bitmath.MB(100).kB)), ContainerResources('side', 50, float(bitmath.MB(50).kB))]
    pods = [PodResources('etcd', 'minikube', 150, float(bitmath.MB(150).kB), containers, [])]
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
    pods = [PodResources('etcd', 'minikube', 150, float(bitmath.MB(150).kB), containers, [])]
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


def test_init_containers_rendered_as_grey_children_in_cpu_foamtree():
    # Given — init container dominates: effective CPU = 500m
    node = [NodeResources('minikube', 2000, float(bitmath.GB(1).kB))]
    containers = [ContainerResources('app', 100, float(bitmath.MB(100).kB))]
    init_containers = [ContainerResources('init-db', 500, float(bitmath.MB(50).kB))]
    pods = [PodResources('my-pod', 'minikube', 500, float(bitmath.MB(100).kB), containers, init_containers)]
    mapper = FoamTreeMapper(node, pods)

    # When
    foamtree = mapper.transform_cpu_resources_to_foamtree()
    node_foam = _.find(foamtree['groups'], lambda item: item['label'] == 'minikube')
    pod_foam = _.find(node_foam['groups'], lambda item: item['label'] == 'my-pod')
    app_foam = _.find(pod_foam['groups'], lambda item: item['label'] == 'app')
    init_foam = _.find(pod_foam['groups'], lambda item: item['label'] == 'init-db (init)')

    # Then
    assert pod_foam['weight'] == 500
    assert app_foam['weight'] == 100
    assert init_foam is not None
    assert init_foam['weight'] == 500
    assert init_foam['color'] == '#aaaaaa'


def test_init_containers_with_zero_resource_not_rendered():
    # Given — init container has 0 CPU → should not appear as a child group
    node = [NodeResources('minikube', 2000, float(bitmath.GB(1).kB))]
    containers = [ContainerResources('app', 100, float(bitmath.MB(100).kB))]
    init_containers = [ContainerResources('init-noop', 0, 0)]
    pods = [PodResources('my-pod', 'minikube', 100, float(bitmath.MB(100).kB), containers, init_containers)]
    mapper = FoamTreeMapper(node, pods)

    # When
    foamtree = mapper.transform_cpu_resources_to_foamtree()
    node_foam = _.find(foamtree['groups'], lambda item: item['label'] == 'minikube')
    pod_foam = _.find(node_foam['groups'], lambda item: item['label'] == 'my-pod')
    init_foam = _.find(pod_foam['groups'], lambda item: 'init' in item['label'])

    # Then — zero-resource init containers must not pollute the foamtree
    assert init_foam is None


# --- Selector metadata carried onto every pod group ---

def test_pod_groups_carry_selector_metadata_in_cpu_foamtree():
    # Given
    node = [NodeResources('minikube', 2000, float(bitmath.GB(1).kB))]
    containers = [ContainerResources('app', 100, float(bitmath.MB(100).kB))]
    init_containers = [ContainerResources('init-db', 50, float(bitmath.MB(50).kB))]
    pods = [PodResources('web', 'minikube', 100, float(bitmath.MB(100).kB), containers, init_containers,
                         'kube-system', {'app': 'web'}, 'Guaranteed')]
    mapper = FoamTreeMapper(node, pods)

    # When
    foamtree = mapper.transform_cpu_resources_to_foamtree()
    node_foam = _.find(foamtree['groups'], lambda item: item['label'] == 'minikube')
    pod_foam = _.find(node_foam['groups'], lambda item: item['label'] == 'web')

    # Then — new keys are added, existing ones keep their shape
    assert pod_foam['weight'] == 100
    assert pod_foam['namespace'] == 'kube-system'
    assert pod_foam['labels'] == {'app': 'web'}
    assert pod_foam['qos'] == 'Guaranteed'
    assert pod_foam['hasInitContainers'] is True
    assert len(pod_foam['groups']) == 2


def test_pod_groups_carry_selector_metadata_in_memory_foamtree():
    # Given
    node = [NodeResources('minikube', 2000, float(bitmath.GB(1).kB))]
    containers = [ContainerResources('app', 100, float(bitmath.MB(100).kB))]
    pods = [PodResources('web', 'minikube', 100, float(bitmath.MB(100).kB), containers, [],
                         'default', {'env': 'prod'}, 'Burstable')]
    mapper = FoamTreeMapper(node, pods)

    # When
    foamtree = mapper.transform_memory_resources_to_foamtree()
    node_foam = _.find(foamtree['groups'], lambda item: item['label'] == 'minikube')
    pod_foam = _.find(node_foam['groups'], lambda item: item['label'] == 'web')

    # Then
    assert pod_foam['namespace'] == 'default'
    assert pod_foam['labels'] == {'env': 'prod'}
    assert pod_foam['qos'] == 'Burstable'
    assert pod_foam['hasInitContainers'] is False


def test_pod_groups_with_missing_labels_and_qos_emit_neutral_values():
    # Given — labels/qos are absent for pods the API reports without them
    node = [NodeResources('minikube', 2000, float(bitmath.GB(1).kB))]
    containers = [ContainerResources('app', 100, float(bitmath.MB(100).kB))]
    pods = [PodResources('web', 'minikube', 100, float(bitmath.MB(100).kB), containers, [],
                         'default', None, None)]
    mapper = FoamTreeMapper(node, pods)

    # When
    foamtree = mapper.transform_cpu_resources_to_foamtree()
    node_foam = _.find(foamtree['groups'], lambda item: item['label'] == 'minikube')
    pod_foam = _.find(node_foam['groups'], lambda item: item['label'] == 'web')

    # Then
    assert pod_foam['labels'] == {}
    assert pod_foam['qos'] is None


def test_empty_group_keeps_its_shape_and_carries_no_pod_metadata():
    # Given — the synthetic free-space group must stay exactly as it was
    node = [NodeResources('minikube', 2000, float(bitmath.GB(1).kB))]
    containers = [ContainerResources('app', 100, float(bitmath.MB(100).kB))]
    pods = [PodResources('web', 'minikube', 100, float(bitmath.MB(100).kB), containers, [],
                         'default', {'app': 'web'}, 'Burstable')]
    mapper = FoamTreeMapper(node, pods)

    # When
    foamtree = mapper.transform_cpu_resources_to_foamtree()
    node_foam = _.find(foamtree['groups'], lambda item: item['label'] == 'minikube')
    empty_foam = _.find(node_foam['groups'], lambda item: item['label'] == 'empty')

    # Then
    assert empty_foam == {'label': 'empty', 'weight': 1900, 'color': '#ffffff'}


# --- Node health metadata carried onto every node group ---

HEALTHY = {'MemoryPressure': False, 'DiskPressure': False, 'PIDPressure': False, 'Ready': True}


def test_node_group_carries_health_metadata_in_cpu_foamtree():
    # Given — a cordoned node also carrying a blocking taint
    taints = [{'key': 'gpu-only', 'value': None, 'effect': 'NoSchedule'}]
    node = [NodeResources('minikube', 2000, float(bitmath.GB(1).kB), True, taints,
                          {**HEALTHY, 'MemoryPressure': True})]
    pods = [PodResources('web', 'minikube', 100, float(bitmath.MB(100).kB),
                         [ContainerResources('app', 100, float(bitmath.MB(100).kB))], [])]
    mapper = FoamTreeMapper(node, pods)

    # When
    foamtree = mapper.transform_cpu_resources_to_foamtree()
    node_foam = _.find(foamtree['groups'], lambda item: item['label'] == 'minikube')

    # Then — existing keys keep their shape, new ones ride alongside
    assert node_foam['weight'] == 2000
    assert node_foam['unschedulable'] is True
    assert node_foam['taints'] == taints
    assert node_foam['conditions']['MemoryPressure'] is True
    assert node_foam['warnings'] == ['cordoned', 'memory-pressure', 'tainted']


def test_node_group_carries_health_metadata_in_memory_foamtree():
    # Given — mergeResources reads the CPU payload, but both must stay symmetric
    node = [NodeResources('minikube', 2000, float(bitmath.GB(1).kB), False, [],
                          {**HEALTHY, 'DiskPressure': True})]
    pods = [PodResources('web', 'minikube', 100, float(bitmath.MB(100).kB),
                         [ContainerResources('app', 100, float(bitmath.MB(100).kB))], [])]
    mapper = FoamTreeMapper(node, pods)

    # When
    foamtree = mapper.transform_memory_resources_to_foamtree()
    node_foam = _.find(foamtree['groups'], lambda item: item['label'] == 'minikube')

    # Then
    assert node_foam['unschedulable'] is False
    assert node_foam['warnings'] == ['disk-pressure']


def test_healthy_node_group_emits_no_warnings():
    # Given
    node = [NodeResources('minikube', 2000, float(bitmath.GB(1).kB), False, [], HEALTHY)]
    pods = [PodResources('web', 'minikube', 100, float(bitmath.MB(100).kB),
                         [ContainerResources('app', 100, float(bitmath.MB(100).kB))], [])]
    mapper = FoamTreeMapper(node, pods)

    # When
    foamtree = mapper.transform_cpu_resources_to_foamtree()
    node_foam = _.find(foamtree['groups'], lambda item: item['label'] == 'minikube')

    # Then
    assert node_foam['warnings'] == []
    assert node_foam['taints'] == []


def test_node_group_built_without_health_metadata_emits_neutral_values():
    # Given — positional construction from before this feature existed
    node = [NodeResources('minikube', 2000, float(bitmath.GB(1).kB))]
    pods = [PodResources('web', 'minikube', 100, float(bitmath.MB(100).kB),
                         [ContainerResources('app', 100, float(bitmath.MB(100).kB))], [])]
    mapper = FoamTreeMapper(node, pods)

    # When
    foamtree = mapper.transform_cpu_resources_to_foamtree()
    node_foam = _.find(foamtree['groups'], lambda item: item['label'] == 'minikube')

    # Then
    assert node_foam['unschedulable'] is False
    assert node_foam['taints'] == []
    assert node_foam['conditions'] == {}
    assert node_foam['warnings'] == []
