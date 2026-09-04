import bitmath  # type: ignore
from k8sfoam.src.utils.extractors import ResourcesExtractor
from pydash import py_ as _  # type: ignore
from k8sfoam.tests.common.mocks import create_container, create_pod, create_node, create_taint, create_condition
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


# --- Selector metadata tests (namespace / labels / QoS) ---

def test_should_extract_pod_selector_metadata():
    # Given
    extractor = ResourcesExtractor()
    pod = create_pod('etcd', 'master', containers=[create_container('etcd', '100m', '1G')],
                     namespace='kube-system', labels={'app': 'etcd', 'tier': 'control-plane'},
                     qos_class='Guaranteed')

    # When
    pod_resources = extractor.extract_pod_requested_resources(pod)

    # Then
    assert pod_resources.namespace == 'kube-system'
    assert pod_resources.labels == {'app': 'etcd', 'tier': 'control-plane'}
    assert pod_resources.qos_class == 'Guaranteed'


def test_should_extract_pod_with_no_labels_as_empty_dict():
    # Given — pod.metadata.labels is None whenever no label is set
    extractor = ResourcesExtractor()
    pod = create_pod('etcd', 'master', containers=[create_container('etcd', '100m', '1G')], labels=None)

    # When
    pod_resources = extractor.extract_pod_requested_resources(pod)

    # Then
    assert pod_resources.labels == {}


def test_should_extract_pod_with_no_qos_class_as_none():
    # Given — pod.status.qos_class is None on pods the kubelet has not admitted yet
    extractor = ResourcesExtractor()
    pod = create_pod('etcd', 'master', containers=[create_container('etcd', '100m', '1G')], qos_class=None)

    # When
    pod_resources = extractor.extract_pod_requested_resources(pod)

    # Then
    assert pod_resources.qos_class is None


# --- Node health tests (cordon / taints / conditions) ---

def test_should_extract_healthy_node_as_schedulable():
    # Given
    extractor = ResourcesExtractor()
    node = create_node('minikube', '2', '8162156Ki')

    # When
    node_resources = extractor.extract_node_resources(node)

    # Then
    assert node_resources.unschedulable is False
    assert node_resources.taints == []
    assert node_resources.conditions == {'MemoryPressure': False, 'DiskPressure': False,
                                         'PIDPressure': False, 'Ready': True}


def test_should_extract_cordoned_node():
    # Given
    extractor = ResourcesExtractor()
    node = create_node('minikube', '2', '8162156Ki', unschedulable=True)

    # When
    node_resources = extractor.extract_node_resources(node)

    # Then
    assert node_resources.unschedulable is True


def test_should_extract_node_taints():
    # Given
    extractor = ResourcesExtractor()
    taints = [create_taint('nvidia.com/gpu', 'true', 'NoSchedule'),
              create_taint('spot', None, 'PreferNoSchedule')]
    node = create_node('gpu-1', '2', '8162156Ki', taints=taints)

    # When
    node_resources = extractor.extract_node_resources(node)

    # Then — PreferNoSchedule is kept; it never marks the node but the detail view lists it
    assert node_resources.taints == [{'key': 'nvidia.com/gpu', 'value': 'true', 'effect': 'NoSchedule'},
                                     {'key': 'spot', 'value': None, 'effect': 'PreferNoSchedule'}]


def test_should_drop_the_cordon_taint_kubernetes_adds_itself():
    # Given — cordoning sets both spec.unschedulable and this taint
    extractor = ResourcesExtractor()
    taints = [create_taint('node.kubernetes.io/unschedulable', None, 'NoSchedule'),
              create_taint('gpu-only', None, 'NoSchedule')]
    node = create_node('minikube', '2', '8162156Ki', unschedulable=True, taints=taints)

    # When
    node_resources = extractor.extract_node_resources(node)

    # Then — reporting it too would mark the node twice for one fact
    assert node_resources.taints == [{'key': 'gpu-only', 'value': None, 'effect': 'NoSchedule'}]


def test_should_extract_pressure_conditions_as_booleans():
    # Given
    extractor = ResourcesExtractor()
    conditions = [create_condition('MemoryPressure', 'True'),
                  create_condition('DiskPressure', 'False'),
                  create_condition('PIDPressure', 'True'),
                  create_condition('Ready', 'True')]
    node = create_node('minikube', '2', '8162156Ki', conditions=conditions)

    # When
    node_resources = extractor.extract_node_resources(node)

    # Then
    assert node_resources.conditions == {'MemoryPressure': True, 'DiskPressure': False,
                                         'PIDPressure': True, 'Ready': True}


def test_should_extract_unknown_ready_condition_as_not_ready():
    # Given — 'Unknown' means the API server stopped hearing from the kubelet
    extractor = ResourcesExtractor()
    node = create_node('minikube', '2', '8162156Ki', conditions=[create_condition('Ready', 'Unknown')])

    # When
    node_resources = extractor.extract_node_resources(node)

    # Then
    assert node_resources.conditions['Ready'] is False


def test_should_extract_node_reporting_no_conditions_as_ready():
    # Given — absent is not the same as False; never invent an outage
    extractor = ResourcesExtractor()
    node = create_node('minikube', '2', '8162156Ki', conditions=[])

    # When
    node_resources = extractor.extract_node_resources(node)

    # Then
    assert node_resources.conditions['Ready'] is True
    assert node_resources.conditions['MemoryPressure'] is False
