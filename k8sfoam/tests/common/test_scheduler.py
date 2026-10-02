import bitmath  # type: ignore
import pytest
from k8sfoam.src.common.dtos import NodeResources, PodResources
from k8sfoam.src.common.scheduler import (filter_node, free_capacity, simulate_drain, simulate_fit, tolerates,
                                          fmt_memory)


GI = float(bitmath.GiB(1).kB)


def node(name, cpu=4000, memory=16 * GI, **kw):
    return NodeResources(name, cpu, memory, **kw)


def pod(name, node_name=None, cpu=0, memory=0.0, owner_kind='ReplicaSet', **kw):
    return PodResources(name, node_name, cpu, memory, [], [], 'default', owner_kind=owner_kind, **kw)


def slugs(reasons):
    return [r['slug'] for r in reasons]


def test_free_capacity_subtracts_bound_pods_from_allocatable():
    # Given
    nodes = [node('a', cpu=3800, memory=15 * GI, allocatable_pods=110)]
    pods = [pod('p1', 'a', 1000, 2 * GI), pod('p2', 'a', 500, GI), pod('pending', None, 9999, GI)]

    # When
    free = free_capacity(nodes, pods)

    # Then
    assert free['a'] == {'cpu': 2300, 'memory': 12 * GI, 'pods': 108, 'extended': {}}


def test_free_capacity_without_pod_limit_skips_the_pod_count():
    # When
    free = free_capacity([node('a')], [])

    # Then
    assert free['a'] == {'cpu': 4000, 'memory': 16 * GI, 'pods': None, 'extended': {}}


def test_pod_fits_on_a_healthy_node_with_room():
    # Given
    n = node('a')

    # When
    reasons = filter_node(pod('new', cpu=1000, memory=GI), n, free_capacity([n], [])['a'])

    # Then
    assert reasons == []


def test_insufficient_cpu_reports_required_and_available():
    # Given
    n = node('a')
    free = free_capacity([n], [pod('busy', 'a', 2800)])['a']

    # When
    reasons = filter_node(pod('new', cpu=4000), n, free)

    # Then
    assert reasons == [{'slug': 'insufficient-cpu', 'message': 'Insufficient CPU: requires 4000m, available 1200m'}]


def test_insufficient_memory_reports_required_and_available():
    # Given
    n = node('a', memory=8 * GI)

    # When
    reasons = filter_node(pod('new', memory=16 * GI), n, free_capacity([n], [])['a'])

    # Then
    assert reasons == [{'slug': 'insufficient-memory',
                        'message': 'Insufficient memory: requires 16.0Gi, available 8.0Gi'}]


def test_insufficient_pods_when_pod_limit_is_reached():
    # Given
    n = node('a', allocatable_pods=1)

    # When
    reasons = filter_node(pod('new'), n, free_capacity([n], [pod('only', 'a')])['a'])

    # Then
    assert slugs(reasons) == ['insufficient-pods']


def test_cordoned_and_not_ready_nodes_are_filtered():
    # Given
    n = node('a', unschedulable=True, conditions={'Ready': False})

    # When
    reasons = filter_node(pod('new'), n, free_capacity([n], [])['a'])

    # Then
    assert slugs(reasons) == ['cordoned', 'not-ready']


def test_pod_tolerating_lifecycle_taints_still_fits_cordoned_not_ready_node():
    # Given — what every DaemonSet pod carries
    n = node('a', unschedulable=True, conditions={'Ready': False})
    tolerations = [{'key': 'node.kubernetes.io/unschedulable', 'operator': 'Exists', 'effect': 'NoSchedule'},
                   {'key': 'node.kubernetes.io/not-ready', 'operator': 'Exists'}]

    # When
    reasons = filter_node(pod('ds', tolerations=tolerations), n, free_capacity([n], [])['a'])

    # Then
    assert reasons == []


def test_node_selector_mismatch_and_missing_label():
    # Given
    n = node('a', labels={'zone': 'b'})

    # When
    reasons = filter_node(pod('new', node_selector={'zone': 'a', 'gpu': 'true'}), n, free_capacity([n], [])['a'])

    # Then
    assert [r['message'] for r in reasons] == [
        'Node selector mismatch: requires zone=a, node has zone=b',
        'Node selector mismatch: requires gpu=true, node has no gpu label',
    ]


def test_node_selector_match_fits():
    # Given
    n = node('a', labels={'zone': 'a', 'kubernetes.io/os': 'linux'})

    # When
    reasons = filter_node(pod('new', node_selector={'zone': 'a'}), n, free_capacity([n], [])['a'])

    # Then
    assert reasons == []


def test_untolerated_blocking_taint_is_reported_and_prefer_no_schedule_ignored():
    # Given
    n = node('a', taints=[{'key': 'dedicated', 'value': 'gpu', 'effect': 'NoSchedule'},
                          {'key': 'soft', 'value': None, 'effect': 'PreferNoSchedule'}])

    # When
    reasons = filter_node(pod('new'), n, free_capacity([n], [])['a'])

    # Then
    assert reasons == [{'slug': 'taint', 'message': 'Untolerated taint: dedicated=gpu:NoSchedule'}]


@pytest.mark.parametrize('toleration, expected', [
    ({'key': 'dedicated', 'operator': 'Equal', 'value': 'gpu', 'effect': 'NoSchedule'}, True),
    ({'key': 'dedicated', 'operator': 'Equal', 'value': 'cpu', 'effect': 'NoSchedule'}, False),
    ({'key': 'dedicated', 'value': 'gpu'}, True),
    ({'key': 'dedicated', 'operator': 'Exists'}, True),
    ({'key': 'dedicated', 'operator': 'Exists', 'effect': 'NoExecute'}, False),
    ({'key': 'other', 'operator': 'Exists'}, False),
    ({'operator': 'Exists'}, True),
])
def test_toleration_matching(toleration, expected):
    # Given
    taint = {'key': 'dedicated', 'value': 'gpu', 'effect': 'NoSchedule'}

    # Then
    assert tolerates(toleration, taint) is expected


def test_fmt_memory_switches_to_mebibytes_below_one_gibibyte():
    assert fmt_memory(float(bitmath.MiB(512).kB)) == '512Mi'
    assert fmt_memory(-5.0) == '0Mi'


def test_simulate_fit_returns_a_verdict_per_node():
    # Given
    nodes = [node('big', cpu=8000), node('small', cpu=1000), node('cordoned', cpu=8000, unschedulable=True)]

    # When
    result = simulate_fit(pod('new', cpu=4000), nodes, [])

    # Then
    assert result['fits'] == 1
    assert result['total'] == 3
    verdicts = {r['node']: r for r in result['nodes']}
    assert verdicts['big']['fits'] is True
    assert slugs(verdicts['small']['reasons']) == ['insufficient-cpu']
    assert slugs(verdicts['cordoned']['reasons']) == ['cordoned']
    assert verdicts['big']['free'] == {'cpu': 8000, 'memory': 16 * GI, 'pods': None, 'extended': {}}


def test_simulate_drain_unknown_node_returns_none():
    assert simulate_drain('ghost', [node('a')], []) is None


def test_simulate_drain_places_all_pods_when_there_is_room():
    # Given
    nodes = [node('a'), node('b'), node('c')]
    pods = [pod('web-1', 'a', 1000, GI), pod('web-2', 'a', 1000, GI), pod('db', 'b', 3000, 8 * GI)]

    # When
    result = simulate_drain('a', nodes, pods)

    # Then
    assert result['fits'] is True
    assert result['pending'] == []
    assert result['remainingNodes'] == 2
    # The emptier node c wins first; the second replica then goes where most room is left.
    assert [(p['pod'], p['to']) for p in result['placements']] == [('web-1', 'c'), ('web-2', 'c')]


def test_simulate_drain_reports_pods_that_would_go_pending():
    # Given
    nodes = [node('a'), node('b', cpu=2000), node('c', cpu=2000, unschedulable=True)]
    pods = [pod('big', 'a', 3000), pod('small', 'a', 1500)]

    # When
    result = simulate_drain('a', nodes, pods)

    # Then
    assert result['fits'] is False
    assert [p['pod'] for p in result['placements']] == ['small']
    assert result['pending'][0]['pod'] == 'big'
    assert result['pending'][0]['reasons'] == [{'slug': 'cordoned', 'nodes': 1}, {'slug': 'insufficient-cpu', 'nodes': 2}]


def test_simulate_drain_places_biggest_pods_first():
    # Given — only one of the two can move, and it must be the big one
    nodes = [node('a'), node('b', cpu=2000)]
    pods = [pod('small', 'a', 500), pod('big', 'a', 2000)]

    # When
    result = simulate_drain('a', nodes, pods)

    # Then
    assert [p['pod'] for p in result['placements']] == ['big']
    assert [p['pod'] for p in result['pending']] == ['small']


def test_simulate_drain_skips_daemonset_and_static_pods_and_flags_naked_pods():
    # Given
    nodes = [node('a'), node('b')]
    pods = [pod('fluentd', 'a', 100, owner_kind='DaemonSet'),
            pod('kube-proxy', 'a', 100, owner_kind='Node'),
            pod('debug', 'a', 100, owner_kind=None),
            pod('web', 'a', 100)]

    # When
    result = simulate_drain('a', nodes, pods)

    # Then
    assert [(s['pod'], s['reason']) for s in result['skipped']] == [('fluentd', 'daemonset'), ('kube-proxy', 'static')]
    assert {p['pod']: p['unmanaged'] for p in result['placements']} == {'debug': True, 'web': False}


def test_simulate_drain_on_single_node_cluster_leaves_everything_pending():
    # When
    result = simulate_drain('a', [node('a')], [pod('web', 'a', 100)])

    # Then
    assert result['fits'] is False
    assert result['remainingNodes'] == 0
    assert result['pending'][0]['reasons'] == []


GPU = 'nvidia.com/gpu'


def test_free_capacity_subtracts_extended_requests():
    # Given
    nodes = [node('gpu', extended={GPU: 4, 'ephemeral-storage': 100 * GI})]
    pods = [pod('train', 'gpu', extended={GPU: 3, 'ephemeral-storage': 30 * GI})]

    # When
    free = free_capacity(nodes, pods)

    # Then
    assert free['gpu']['extended'] == {GPU: 1, 'ephemeral-storage': 70 * GI}


def test_insufficient_gpu_reports_required_and_available():
    # Given
    n = node('gpu', extended={GPU: 4})
    free = free_capacity([n], [pod('train', 'gpu', extended={GPU: 4})])['gpu']

    # When
    reasons = filter_node(pod('new', extended={GPU: 1}), n, free)

    # Then
    assert reasons == [{'slug': 'insufficient-extended', 'message': f'Insufficient {GPU}: requires 1, available 0'}]


def test_node_without_the_extended_resource_has_none_available():
    # Given
    n = node('cpu-only')

    # When
    reasons = filter_node(pod('new', extended={GPU: 1}), n, free_capacity([n], [])['cpu-only'])

    # Then
    assert [r['message'] for r in reasons] == [f'Insufficient {GPU}: requires 1, available 0']


def test_byte_sized_extended_resource_is_formatted_in_binary_units():
    # Given
    n = node('a', extended={'ephemeral-storage': 5 * GI})

    # When
    reasons = filter_node(pod('new', extended={'ephemeral-storage': 20 * GI}), n, free_capacity([n], [])['a'])

    # Then
    assert [r['message'] for r in reasons] == ['Insufficient ephemeral-storage: requires 20.0Gi, available 5.0Gi']


def test_simulate_fit_sends_a_gpu_pod_only_to_the_gpu_node():
    # Given
    nodes = [node('cpu-1'), node('gpu-1', extended={GPU: 2})]

    # When
    result = simulate_fit(pod('train', extended={GPU: 1}), nodes, [])

    # Then
    assert result['fits'] == 1
    verdicts = {r['node']: r for r in result['nodes']}
    assert verdicts['gpu-1']['fits'] is True
    assert verdicts['gpu-1']['free']['extended'] == {GPU: 2}
    assert slugs(verdicts['cpu-1']['reasons']) == ['insufficient-extended']


def test_simulate_drain_leaves_gpu_pod_pending_without_another_gpu_node():
    # Given — plenty of CPU elsewhere, but no GPUs
    nodes = [node('gpu-1', extended={GPU: 1}), node('cpu-1', cpu=16000), node('cpu-2', cpu=16000)]
    pods = [pod('train', 'gpu-1', 1000, GI, extended={GPU: 1}), pod('web', 'gpu-1', 500)]

    # When
    result = simulate_drain('gpu-1', nodes, pods)

    # Then
    assert [p['pod'] for p in result['placements']] == ['web']
    assert result['pending'][0]['pod'] == 'train'
    assert result['pending'][0]['reasons'] == [{'slug': 'insufficient-extended', 'nodes': 2}]


def test_simulate_drain_consumes_gpus_as_it_places_pods():
    # Given — two GPU pods, one free GPU elsewhere
    nodes = [node('gpu-1', extended={GPU: 2}), node('gpu-2', extended={GPU: 1})]
    pods = [pod('train-a', 'gpu-1', 100, extended={GPU: 1}), pod('train-b', 'gpu-1', 50, extended={GPU: 1})]

    # When
    result = simulate_drain('gpu-1', nodes, pods)

    # Then
    assert [(p['pod'], p['to']) for p in result['placements']] == [('train-a', 'gpu-2')]
    assert [p['pod'] for p in result['pending']] == ['train-b']
