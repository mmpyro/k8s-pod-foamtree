import bitmath  # type: ignore
from k8sfoam.src.utils.mappers import FoamTreeMapper
from k8sfoam.src.common.dtos import NodeResources, PodResources, ContainerResources
from pydash import py_ as _  # type: ignore


# ──────────────────────────────────────────────────────────────────────────────
# Helpers — build DTOs with the new `extended` field in one place so tests
# don't have to know the exact namedtuple signature.
# ──────────────────────────────────────────────────────────────────────────────

def node(name, cpu, memory, extended={}):
    return NodeResources(name, cpu, float(memory), extended)


def container(name, cpu, memory, extended={}):
    return ContainerResources(name, cpu, float(memory), extended)


def pod(name, node_name, cpu, memory, containers, init_containers=[], extended={}):
    return PodResources(name, node_name, cpu, float(memory), containers, init_containers, extended)


# ──────────────────────────────────────────────────────────────────────────────
# Core CPU / Memory tests (unchanged behaviour — just using new helpers)
# ──────────────────────────────────────────────────────────────────────────────

def test_should_return_foam_tree_map_of_cpu_resources():
    # Given
    nodes = [node('minikube', 2000, bitmath.GB(1).kB)]
    containers = [container('etcd', 100, bitmath.MB(100).kB), container('side', 50, bitmath.MB(50).kB)]
    pods = [pod('etcd', 'minikube', 150, bitmath.MB(150).kB, containers)]
    mapper = FoamTreeMapper(nodes, pods)

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
    nodes = [node('minikube', 2000, bitmath.GB(1).kB)]
    containers = [container('etcd', 100, bitmath.MB(100).kB), container('side', 50, bitmath.MB(50).kB)]
    pods = [pod('etcd', 'minikube', 150, bitmath.MB(150).kB, containers)]
    mapper = FoamTreeMapper(nodes, pods)
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
    nodes = [node('minikube', 2000, bitmath.GB(1).kB)]
    containers = [container('app', 100, bitmath.MB(100).kB)]
    init_containers = [container('init-db', 500, bitmath.MB(50).kB)]
    pods = [pod('my-pod', 'minikube', 500, bitmath.MB(100).kB, containers, init_containers)]
    mapper = FoamTreeMapper(nodes, pods)

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
    nodes = [node('minikube', 2000, bitmath.GB(1).kB)]
    containers = [container('app', 100, bitmath.MB(100).kB)]
    init_containers = [container('init-noop', 0, 0)]
    pods = [pod('my-pod', 'minikube', 100, bitmath.MB(100).kB, containers, init_containers)]
    mapper = FoamTreeMapper(nodes, pods)

    # When
    foamtree = mapper.transform_cpu_resources_to_foamtree()
    node_foam = _.find(foamtree['groups'], lambda item: item['label'] == 'minikube')
    pod_foam = _.find(node_foam['groups'], lambda item: item['label'] == 'my-pod')
    init_foam = _.find(pod_foam['groups'], lambda item: 'init' in item['label'])

    # Then — zero-resource init containers must not pollute the foamtree
    assert init_foam is None


# ──────────────────────────────────────────────────────────────────────────────
# Extended resource (GPU / TPU / ephemeral-storage) mapper tests
# ──────────────────────────────────────────────────────────────────────────────

def test_transform_extended_resources_gpu_single_pod():
    # Node has 4 GPUs; one pod requests 2.
    nodes = [node('gpu-node', 32000, bitmath.GiB(128).kB, extended={'nvidia.com/gpu': 4.0})]
    conts = [container('trainer', 8000, bitmath.GiB(32).kB, extended={'nvidia.com/gpu': 2.0})]
    pods = [pod('training-job', 'gpu-node', 8000, bitmath.GiB(32).kB, conts,
                extended={'nvidia.com/gpu': 2.0})]
    mapper = FoamTreeMapper(nodes, pods)

    foamtree = mapper.transform_extended_resources_to_foamtree('nvidia.com/gpu')
    node_foam = _.find(foamtree['groups'], lambda item: item['label'] == 'gpu-node')
    pod_foam = _.find(node_foam['groups'], lambda item: item['label'] == 'training-job')
    empty_foam = _.find(node_foam['groups'], lambda item: item['label'] == 'empty')

    assert node_foam['weight'] == 4.0
    assert pod_foam['weight'] == 2.0
    assert empty_foam['weight'] == 2.0
    assert empty_foam['color'] == '#ffffff'


def test_transform_extended_resources_node_without_gpu_shows_zero_weight():
    # Node has no GPUs; all pods contribute 0 → node weight should be 0.
    nodes = [node('cpu-node', 8000, bitmath.GiB(32).kB)]
    conts = [container('app', 200, bitmath.MiB(256).kB)]
    pods = [pod('app-pod', 'cpu-node', 200, bitmath.MiB(256).kB, conts)]
    mapper = FoamTreeMapper(nodes, pods)

    foamtree = mapper.transform_extended_resources_to_foamtree('nvidia.com/gpu')
    node_foam = _.find(foamtree['groups'], lambda item: item['label'] == 'cpu-node')

    assert node_foam['weight'] == 0


def test_transform_extended_resources_multiple_gpu_pods():
    # Node has 8 GPUs; two pods use 3 and 2 respectively.
    nodes = [node('gpu-node', 32000, bitmath.GiB(256).kB, extended={'nvidia.com/gpu': 8.0})]
    conts_a = [container('c1', 4000, bitmath.GiB(16).kB, extended={'nvidia.com/gpu': 3.0})]
    conts_b = [container('c2', 4000, bitmath.GiB(16).kB, extended={'nvidia.com/gpu': 2.0})]
    pods = [
        pod('job-a', 'gpu-node', 4000, bitmath.GiB(16).kB, conts_a, extended={'nvidia.com/gpu': 3.0}),
        pod('job-b', 'gpu-node', 4000, bitmath.GiB(16).kB, conts_b, extended={'nvidia.com/gpu': 2.0}),
    ]
    mapper = FoamTreeMapper(nodes, pods)

    foamtree = mapper.transform_extended_resources_to_foamtree('nvidia.com/gpu')
    node_foam = _.find(foamtree['groups'], lambda item: item['label'] == 'gpu-node')
    empty_foam = _.find(node_foam['groups'], lambda item: item['label'] == 'empty')
    pod_a = _.find(node_foam['groups'], lambda item: item['label'] == 'job-a')
    pod_b = _.find(node_foam['groups'], lambda item: item['label'] == 'job-b')

    assert pod_a['weight'] == 3.0
    assert pod_b['weight'] == 2.0
    assert empty_foam['weight'] == 3.0  # 8 - 3 - 2 = 3 free


def test_transform_extended_resources_pod_container_children_rendered():
    # Pod with two containers using GPUs — children must be emitted.
    nodes = [node('gpu-node', 16000, bitmath.GiB(64).kB, extended={'nvidia.com/gpu': 4.0})]
    conts = [
        container('c1', 2000, bitmath.GiB(8).kB, extended={'nvidia.com/gpu': 1.0}),
        container('c2', 2000, bitmath.GiB(8).kB, extended={'nvidia.com/gpu': 2.0}),
    ]
    pods = [pod('multi-gpu', 'gpu-node', 4000, bitmath.GiB(16).kB, conts,
                extended={'nvidia.com/gpu': 3.0})]
    mapper = FoamTreeMapper(nodes, pods)

    foamtree = mapper.transform_extended_resources_to_foamtree('nvidia.com/gpu')
    node_foam = _.find(foamtree['groups'], lambda item: item['label'] == 'gpu-node')
    pod_foam = _.find(node_foam['groups'], lambda item: item['label'] == 'multi-gpu')
    c1_foam = _.find(pod_foam['groups'], lambda item: item['label'] == 'c1')
    c2_foam = _.find(pod_foam['groups'], lambda item: item['label'] == 'c2')

    assert c1_foam['weight'] == 1.0
    assert c2_foam['weight'] == 2.0


def test_get_extended_resource_keys_returns_sorted_deduplicated_list():
    # Two nodes — one with GPU, one without.  Two pods — one with GPU, one with ephemeral.
    nodes = [
        node('gpu-node', 16000, bitmath.GiB(64).kB, extended={'nvidia.com/gpu': 4.0}),
        node('cpu-node', 8000, bitmath.GiB(32).kB),
    ]
    conts_gpu = [container('trainer', 4000, bitmath.GiB(16).kB, extended={'nvidia.com/gpu': 2.0})]
    conts_eph = [container('logger', 200, bitmath.MiB(512).kB, extended={'ephemeral-storage': 1024.0})]
    pods = [
        pod('gpu-pod', 'gpu-node', 4000, bitmath.GiB(16).kB, conts_gpu,
            extended={'nvidia.com/gpu': 2.0}),
        pod('log-pod', 'cpu-node', 200, bitmath.MiB(512).kB, conts_eph,
            extended={'ephemeral-storage': 1024.0}),
    ]
    mapper = FoamTreeMapper(nodes, pods)

    keys = mapper.get_extended_resource_keys()

    # Both keys present, sorted alphabetically, no duplicates
    assert keys == ['ephemeral-storage', 'nvidia.com/gpu']


def test_get_extended_resource_keys_empty_when_no_extended_resources():
    nodes = [node('plain-node', 4000, bitmath.GiB(16).kB)]
    conts = [container('app', 500, bitmath.MiB(256).kB)]
    pods = [pod('app-pod', 'plain-node', 500, bitmath.MiB(256).kB, conts)]
    mapper = FoamTreeMapper(nodes, pods)

    assert mapper.get_extended_resource_keys() == []
