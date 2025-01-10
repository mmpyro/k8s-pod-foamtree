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
