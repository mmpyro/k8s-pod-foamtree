import bitmath  # type: ignore
import pytest
from k8sfoam.src.utils.extractors import ResourcesExtractor
from pydash import py_ as _  # type: ignore
from k8sfoam.tests.common.mocks import (create_container, create_pod, create_node, create_taint, create_condition,
                                        create_toleration)
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


def test_should_extract_container_memory_limit():
    # Given
    extractor = ResourcesExtractor()
    pod = create_pod('web', 'master', containers=[create_container('web', '100m', '128Mi', memory_limit='256Mi')])

    # When
    pod_resources = extractor.extract_pod_requested_resources(pod)

    # Then
    assert _.head(pod_resources.containers).memory_limit == float(bitmath.MiB(256).kB)


def test_container_without_limits_has_no_memory_limit():
    # Given — the API reports limits as None when none are set
    extractor = ResourcesExtractor()
    pod = create_pod('web', 'master', containers=[create_container('web', '100m', '128Mi')])

    # When
    pod_resources = extractor.extract_pod_requested_resources(pod)

    # Then
    assert _.head(pod_resources.containers).memory_limit is None


# --- Quantity parsing ---

# Every form here is what the API server returns, which rewrites requests into
# canonical form: 0.1Gi comes back as millibytes, 100k stays 100k.
@pytest.mark.parametrize('memory, kb', [
    ('100k', 100.0),                   # lower-case k is the SI kilo
    ('107374182400m', 107374.1824),    # 0.1Gi: not a whole number of bytes
    ('1e3', 1.0),                      # exponent form
    ('128974848', 128974.848),         # plain bytes
    ('129M', 129000.0),
])
def test_should_parse_every_memory_quantity_form(memory, kb):
    # Given
    extractor = ResourcesExtractor()
    pod = create_pod('web', 'master', containers=[create_container('web', '100m', memory, memory_limit=memory)])

    # When
    pod_resources = extractor.extract_pod_requested_resources(pod)

    # Then
    assert pod_resources.memory == kb
    assert _.head(pod_resources.containers).memory_limit == kb


@pytest.mark.parametrize('cpu, millicores', [('250m', 250), ('0.5', 500), ('2', 2000), ('1.0005', 1001)])
def test_should_parse_cpu_to_millicores_rounding_up_like_the_scheduler(cpu, millicores):
    # Given
    extractor = ResourcesExtractor()
    node = create_node('minikube', cpu, '1Gi')

    # When
    node_resources = extractor.extract_node_resources(node)

    # Then
    assert node_resources.cpu == millicores


# --- Native sidecars (init containers with restartPolicy: Always) ---

def test_sidecar_runs_alongside_the_regular_containers():
    # Given — the proxy starts before the app and keeps running with it
    extractor = ResourcesExtractor()
    pod = create_pod('pod', 'node',
                     containers=[create_container('app', '100m', '100Mi')],
                     init_containers=[create_container('proxy', '50m', '50Mi', restart_policy='Always')])

    # When
    result = extractor.extract_pod_requested_resources(pod)

    # Then — summed like a regular container, and shown and audited as one
    assert result.cpu == 150
    assert result.memory == pytest.approx(float(bitmath.MiB(150).kB))
    assert [c.name for c in result.containers] == ['app', 'proxy']
    assert result.init_containers == []


def test_init_container_after_a_sidecar_runs_next_to_it():
    # Given — migrate (200m) runs while the proxy (50m) is already up
    extractor = ResourcesExtractor()
    pod = create_pod('pod', 'node',
                     containers=[create_container('app', '100m', '100Mi')],
                     init_containers=[create_container('proxy', '50m', '50Mi', restart_policy='Always'),
                                      create_container('migrate', '200m', '10Mi')])

    # When
    result = extractor.extract_pod_requested_resources(pod)

    # Then — max(app + proxy, proxy + migrate) per resource
    assert result.cpu == 250
    assert result.memory == pytest.approx(float(bitmath.MiB(150).kB))
    assert [c.name for c in result.init_containers] == ['migrate']


def test_init_container_before_a_sidecar_runs_alone():
    # Given — migrate finishes before the proxy starts
    extractor = ResourcesExtractor()
    pod = create_pod('pod', 'node',
                     containers=[create_container('app', '100m', '100Mi')],
                     init_containers=[create_container('migrate', '200m', '10Mi'),
                                      create_container('proxy', '50m', '50Mi', restart_policy='Always')])

    # When
    result = extractor.extract_pod_requested_resources(pod)

    # Then — max(app + proxy, migrate)
    assert result.cpu == 200
    assert result.memory == pytest.approx(float(bitmath.MiB(150).kB))


def test_should_extract_node_labels_and_pod_limit():
    # Given
    extractor = ResourcesExtractor()
    node = create_node('worker', '3800m', '15Gi', labels={'zone': 'a'}, pods='110')

    # When
    result = extractor.extract_node_resources(node)

    # Then
    assert result.labels == {'zone': 'a'}
    assert result.allocatable_pods == 110
    # pods is a slot count, not a requestable resource
    assert result.extended == {}


def test_node_without_pod_limit_or_labels_emits_neutral_values():
    # When
    result = ResourcesExtractor().extract_node_resources(create_node('worker', '4', '16Gi'))

    # Then
    assert result.labels == {}
    assert result.allocatable_pods is None


def test_should_extract_pod_scheduling_constraints():
    # Given
    extractor = ResourcesExtractor()
    pod = create_pod('web', 'node', containers=[create_container('app', '100m', '100Mi')],
                     node_selector={'zone': 'a'},
                     tolerations=[create_toleration('dedicated', 'Equal', 'gpu', 'NoSchedule')],
                     owner_kind='ReplicaSet')

    # When
    result = extractor.extract_pod_requested_resources(pod)

    # Then
    assert result.node_selector == {'zone': 'a'}
    assert result.tolerations == [{'key': 'dedicated', 'operator': 'Equal', 'value': 'gpu', 'effect': 'NoSchedule'}]
    assert result.owner_kind == 'ReplicaSet'


@pytest.mark.parametrize('owner_kind, annotations, expected', [
    (None, None, None),
    ('DaemonSet', None, 'DaemonSet'),
    ('Node', {'kubernetes.io/config.mirror': 'abc'}, 'Node'),
    (None, {'kubernetes.io/config.mirror': 'abc'}, 'Node'),
])
def test_should_extract_pod_owner_kind(owner_kind, annotations, expected):
    # Given
    pod = create_pod('p', 'node', containers=[create_container('app', '100m', '100Mi')],
                     owner_kind=owner_kind, annotations=annotations)

    # When
    result = ResourcesExtractor().extract_pod_requested_resources(pod)

    # Then
    assert result.owner_kind == expected
    assert result.node_selector == {}
    assert result.tolerations == []


def test_gpu_set_as_limit_only_counts_as_request():
    # Given — the API allows extended resources in limits alone
    extractor = ResourcesExtractor()
    pod = create_pod('train', 'gpu-node', containers=[
        create_container('train', '1', '1Gi', extra_limits={'nvidia.com/gpu': '2'})])

    # When
    pod_resources = extractor.extract_pod_requested_resources(pod)

    # Then
    assert pod_resources.containers[0].extended == {'nvidia.com/gpu': 2}
    assert pod_resources.extended == {'nvidia.com/gpu': 2}


def test_extended_request_wins_over_limit():
    # Given
    extractor = ResourcesExtractor()
    pod = create_pod('app', 'node', containers=[
        create_container('app', '1', '1Gi', extra_requests={'ephemeral-storage': '1G'},
                         extra_limits={'ephemeral-storage': '2G'})])

    # When
    pod_resources = extractor.extract_pod_requested_resources(pod)

    # Then
    assert pod_resources.extended == {'ephemeral-storage': bitmath.GB(1).kB}


@pytest.mark.parametrize("name, quantity, expected", [
    ('ephemeral-storage', '1Gi', bitmath.GiB(1).kB),
    ('hugepages-2Mi', '512Mi', bitmath.MiB(512).kB),
    ('hugepages-1Gi', '2Gi', bitmath.GiB(2).kB),
    ('amd.com/gpu', '1', 1),
])
def test_should_convert_extended_quantities(name, quantity, expected):
    # Given
    extractor = ResourcesExtractor()
    pod = create_pod('app', 'node', containers=[create_container('app', '1', '1Gi', extra_requests={name: quantity})])

    # When
    pod_resources = extractor.extract_pod_requested_resources(pod)

    # Then
    assert pod_resources.extended == {name: expected}


def test_extended_resources_follow_the_init_and_sidecar_rule():
    # Given — sidecar 1 GPU runs next to the 2-GPU init container, then next to the 1-GPU app
    extractor = ResourcesExtractor()
    pod = create_pod('app', 'node',
                     containers=[create_container('app', '1', '1Gi', extra_limits={'nvidia.com/gpu': '1'})],
                     init_containers=[
                         create_container('side', '0', '0', restart_policy='Always', extra_limits={'nvidia.com/gpu': '1'}),
                         create_container('warmup', '0', '0', extra_limits={'nvidia.com/gpu': '2'})])

    # When
    pod_resources = extractor.extract_pod_requested_resources(pod)

    # Then — max(app 1 + side 1, side 1 + warmup 2)
    assert pod_resources.extended == {'nvidia.com/gpu': 3}


def test_node_reads_allocatable_and_skips_non_requestable_keys():
    # Given
    extractor = ResourcesExtractor()
    node = create_node('gpu-node', '8', '32Gi', extended={
        'nvidia.com/gpu': '4', 'ephemeral-storage': '100G', 'hugepages-2Mi': '0',
        'pods': '110', 'attachable-volumes-aws-ebs': '39'})

    # When
    node_resources = extractor.extract_node_resources(node)

    # Then
    assert node_resources.cpu == 8000
    assert node_resources.extended == {'nvidia.com/gpu': 4, 'ephemeral-storage': bitmath.GB(100).kB, 'hugepages-2Mi': 0}
