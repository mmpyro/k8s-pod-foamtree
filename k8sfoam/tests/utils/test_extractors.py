import bitmath  # type: ignore
from k8sfoam.src.utils.extractors import ResourcesExtractor
from pydash import py_ as _  # type: ignore
from k8sfoam.tests.common.mocks import create_container, create_pod, create_node
from unittest.mock import MagicMock


def test_should_extract_pod_resources_with_single_container_pod():
    # Given
    extractor = ResourcesExtractor()
    pod = create_pod('etcd', 'master', containers=[create_container('etcd', '100m', '1G')])

    # When
    pod_resources = extractor.extract_pod_requested_resources(pod)

    # Then
    assert pod_resources.name == 'etcd'
    assert pod_resources.node_name == 'master'
    assert _.head(pod_resources.containers).name == 'etcd'
    assert _.head(pod_resources.containers).cpu == 100
    assert _.head(pod_resources.containers).memory == bitmath.GB(1).kB
    assert pod_resources.cpu == 100
    assert pod_resources.memory == bitmath.GB(1).kB
    assert pod_resources.init_containers == []
    assert pod_resources.extended == {}


def test_should_extract_pod_resources_with_multi_container_pod():
    # Given
    extractor = ResourcesExtractor()
    pod = create_pod('etcd', 'master', containers=[create_container('etcd', '100m', '1G'), create_container('side', '50m', '100Mi')])

    # When
    pod_resources = extractor.extract_pod_requested_resources(pod)

    # Then
    assert pod_resources.name == 'etcd'
    assert pod_resources.node_name == 'master'
    assert _.head(pod_resources.containers).name == 'etcd'
    assert _.head(pod_resources.containers).cpu == 100
    assert _.head(pod_resources.containers).memory == bitmath.GB(1).kB
    assert pod_resources.containers[1].name == 'side'
    assert pod_resources.containers[1].cpu == 50
    assert pod_resources.containers[1].memory == bitmath.MiB(100).kB
    assert pod_resources.cpu == 150
    assert pod_resources.memory == bitmath.GB(1).kB + bitmath.MiB(100).kB
    assert pod_resources.init_containers == []


def test_should_extract_pod_resources_where_container_doesnt_have_resources_specified():
    # Given
    extractor = ResourcesExtractor()
    container = MagicMock()
    resources = MagicMock()
    container.resources = resources
    resources.request = None
    pod = create_pod('etcd', 'master', containers=[container])

    # When
    pod_resources = extractor.extract_pod_requested_resources(pod)

    # Then
    assert pod_resources.name == 'etcd'
    assert pod_resources.node_name == 'master'
    assert _.head(pod_resources.containers).cpu == 0
    assert _.head(pod_resources.containers).memory == 0


def test_should_extract_node_resources():
    # Given
    extractor = ResourcesExtractor()
    node = create_node('minikube', '2', '8162156Ki')

    # When
    node_resources = extractor.extract_node_resources(node)

    # Then
    assert node_resources.name == 'minikube'
    assert node_resources.cpu == 2000
    assert node_resources.extended == {}


# --- Init container tests ---

def test_effective_cpu_when_no_init_containers():
    # Regular: 100m + 200m = 300m — no init containers
    extractor = ResourcesExtractor()
    pod = create_pod('pod', 'node',
                     containers=[create_container('a', '100m', '100Mi'),
                                 create_container('b', '200m', '200Mi')])

    result = extractor.extract_pod_requested_resources(pod)

    assert result.cpu == 300
    assert result.init_containers == []


def test_effective_cpu_when_init_container_less_than_regular_sum():
    # Init: 200m < Regular sum: 300m → effective = 300m
    extractor = ResourcesExtractor()
    pod = create_pod('pod', 'node',
                     containers=[create_container('a', '100m', '100Mi'),
                                 create_container('b', '200m', '200Mi')],
                     init_containers=[create_container('init-a', '200m', '50Mi')])

    result = extractor.extract_pod_requested_resources(pod)

    assert result.cpu == 300
    assert len(result.init_containers) == 1
    assert result.init_containers[0].name == 'init-a'
    assert result.init_containers[0].cpu == 200


def test_effective_cpu_when_init_container_greater_than_regular_sum():
    # Init: 500m > Regular sum: 300m → effective = 500m
    extractor = ResourcesExtractor()
    pod = create_pod('pod', 'node',
                     containers=[create_container('a', '100m', '100Mi'),
                                 create_container('b', '200m', '200Mi')],
                     init_containers=[create_container('init-a', '500m', '50Mi')])

    result = extractor.extract_pod_requested_resources(pod)

    assert result.cpu == 500
    assert result.init_containers[0].cpu == 500


def test_effective_cpu_with_multiple_init_containers_takes_max():
    # Init containers: 500m and 300m → max = 500m; Regular: 100m → effective = 500m
    extractor = ResourcesExtractor()
    pod = create_pod('pod', 'node',
                     containers=[create_container('a', '100m', '100Mi')],
                     init_containers=[create_container('init-a', '500m', '50Mi'),
                                      create_container('init-b', '300m', '30Mi')])

    result = extractor.extract_pod_requested_resources(pod)

    assert result.cpu == 500
    assert len(result.init_containers) == 2


def test_effective_cpu_when_init_container_has_no_resource_requests():
    # Init container with no requests → treated as 0; Regular: 100m → effective = 100m
    extractor = ResourcesExtractor()
    init_container = MagicMock()
    init_resources = MagicMock()
    init_resources.requests = None
    init_container.resources = init_resources

    pod = create_pod('pod', 'node',
                     containers=[create_container('a', '100m', '100Mi')],
                     init_containers=[init_container])

    result = extractor.extract_pod_requested_resources(pod)

    assert result.cpu == 100
    assert len(result.init_containers) == 1
    assert result.init_containers[0].cpu == 0


def test_effective_memory_when_init_container_dominates():
    # Init: 500Mi > Regular: 100Mi + 100Mi = 200Mi → effective = 500Mi
    extractor = ResourcesExtractor()
    pod = create_pod('pod', 'node',
                     containers=[create_container('a', '100m', '100Mi'),
                                 create_container('b', '100m', '100Mi')],
                     init_containers=[create_container('init-a', '50m', '500Mi')])

    result = extractor.extract_pod_requested_resources(pod)

    assert result.memory == bitmath.MiB(500).kB


# --- Extended resource tests ---

def test_container_gpu_request_is_extracted():
    # A container requesting 2 NVIDIA GPUs should have the key in its extended dict.
    extractor = ResourcesExtractor()
    pod = create_pod('gpu-pod', 'gpu-node',
                     containers=[create_container('trainer', '4', '8Gi',
                                                  extended={'nvidia.com/gpu': '2'})])

    result = extractor.extract_pod_requested_resources(pod)

    assert result.extended.get('nvidia.com/gpu') == 2.0
    assert result.containers[0].extended.get('nvidia.com/gpu') == 2.0


def test_pod_gpu_sums_across_regular_containers():
    # Two containers each requesting 1 GPU → pod effective = 2 GPUs.
    extractor = ResourcesExtractor()
    pod = create_pod('multi-gpu', 'gpu-node',
                     containers=[
                         create_container('a', '2', '4Gi', extended={'nvidia.com/gpu': '1'}),
                         create_container('b', '2', '4Gi', extended={'nvidia.com/gpu': '1'}),
                     ])

    result = extractor.extract_pod_requested_resources(pod)

    assert result.extended.get('nvidia.com/gpu') == 2.0


def test_pod_gpu_init_container_dominates():
    # Init container requests 4 GPUs; regular only 1 → effective = 4.
    extractor = ResourcesExtractor()
    pod = create_pod('init-gpu', 'gpu-node',
                     containers=[create_container('app', '1', '1Gi', extended={'nvidia.com/gpu': '1'})],
                     init_containers=[create_container('init', '1', '1Gi', extended={'nvidia.com/gpu': '4'})])

    result = extractor.extract_pod_requested_resources(pod)

    assert result.extended.get('nvidia.com/gpu') == 4.0


def test_pod_gpu_regular_dominates_over_init():
    # Regular containers sum to 3 GPUs; init only needs 1 → effective = 3.
    extractor = ResourcesExtractor()
    pod = create_pod('reg-gpu', 'gpu-node',
                     containers=[
                         create_container('a', '1', '1Gi', extended={'nvidia.com/gpu': '2'}),
                         create_container('b', '1', '1Gi', extended={'nvidia.com/gpu': '1'}),
                     ],
                     init_containers=[create_container('init', '1', '1Gi', extended={'nvidia.com/gpu': '1'})])

    result = extractor.extract_pod_requested_resources(pod)

    assert result.extended.get('nvidia.com/gpu') == 3.0


def test_ephemeral_storage_is_converted_to_kb():
    # 10 GiB of ephemeral-storage should arrive as decimal kB.
    extractor = ResourcesExtractor()
    pod = create_pod('storage-pod', 'node',
                     containers=[create_container('app', '500m', '512Mi',
                                                  extended={'ephemeral-storage': '10Gi'})])

    result = extractor.extract_pod_requested_resources(pod)

    expected_kb = float(bitmath.GiB(10).kB)
    assert abs(result.extended.get('ephemeral-storage', 0) - expected_kb) < 1


def test_node_gpu_extracted_from_allocatable():
    # Node with 4 NVIDIA GPUs advertised in allocatable should surface in extended.
    extractor = ResourcesExtractor()
    node = create_node('gpu-node', '16', '64Gi', extended={'nvidia.com/gpu': '4'})

    result = extractor.extract_node_resources(node)

    assert result.extended.get('nvidia.com/gpu') == 4.0


def test_node_without_extended_resources_has_empty_extended():
    # Regular CPU/memory-only node should produce an empty extended dict.
    extractor = ResourcesExtractor()
    node = create_node('cpu-node', '8', '32Gi')

    result = extractor.extract_node_resources(node)

    assert result.extended == {}


def test_pod_without_extended_resources_has_empty_extended():
    # Regular pod (no GPU / storage requests) should produce an empty extended dict.
    extractor = ResourcesExtractor()
    pod = create_pod('normal-pod', 'node',
                     containers=[create_container('app', '200m', '256Mi')])

    result = extractor.extract_pod_requested_resources(pod)

    assert result.extended == {}
    assert result.containers[0].extended == {}
