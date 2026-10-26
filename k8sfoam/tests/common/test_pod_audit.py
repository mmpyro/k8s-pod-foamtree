from k8sfoam.src.common.dtos import ContainerResources, NodeResources, PodResources
from k8sfoam.src.common.pod_audit import pod_findings, workload_findings


# 4 cores / 16000 MB (in kB) — a 1:4 core:GB node.
NODE = NodeResources('worker', 4000, 16_000_000)


def pod(cpu: float, memory: float, memory_limit=1, init_containers=None, owner_kind='ReplicaSet') -> PodResources:
    containers = [ContainerResources('app', cpu, memory, memory_limit)]
    return PodResources('app', 'worker', cpu, memory, containers, init_containers or [], owner_kind=owner_kind)


def test_well_sized_pod_has_no_findings():
    # Given — 10% of both axes, limit set
    # When
    findings = pod_findings(pod(400, 1_600_000), NODE)

    # Then
    assert findings == []


def test_container_without_cpu_or_memory_request_is_flagged():
    # When / Then
    assert pod_findings(pod(0, 1_600_000), NODE) == ['missing-requests']
    assert pod_findings(pod(400, 0), NODE) == ['missing-requests']


def test_container_without_memory_limit_is_flagged():
    # When
    findings = pod_findings(pod(400, 1_600_000, memory_limit=None), NODE)

    # Then
    assert findings == ['missing-limits']


def test_init_containers_are_not_audited():
    # Given — an init container with nothing set must not taint a healthy pod
    init = [ContainerResources('init', 0, 0, None)]

    # When
    findings = pod_findings(pod(400, 1_600_000, init_containers=init), NODE)

    # Then
    assert findings == []


def test_pod_taking_most_of_a_node_is_a_monolith():
    # When / Then — either axis is enough
    assert 'monolith' in pod_findings(pod(3600, 14_400_000), NODE)
    assert 'monolith' in pod_findings(pod(3600, 13_000_000), NODE)


def test_exactly_eighty_percent_is_not_a_monolith():
    # When
    findings = pod_findings(pod(3200, 12_800_000), NODE)

    # Then
    assert findings == []


def test_lopsided_pod_is_flagged_for_ratio_asymmetry():
    # Given — 50% of the CPU but only 5% of the memory
    # When
    findings = pod_findings(pod(2000, 800_000), NODE)

    # Then
    assert findings == ['ratio-asymmetry']


def test_small_lopsided_pod_is_not_flagged():
    # Given — 5% of the CPU vs 0.1% of the memory: extreme ratio, strands nothing
    # When
    findings = pod_findings(pod(200, 16_000), NODE)

    # Then
    assert findings == []


def test_node_without_capacity_does_not_crash():
    # When
    findings = pod_findings(pod(400, 1_600_000), NodeResources('ghost', 0, 0))

    # Then
    assert findings == []


def test_every_finding_is_reported_in_order():
    # Given — no memory limit, 90% of the CPU, 10% of the memory
    # When
    findings = pod_findings(pod(3600, 1_600_000, memory_limit=None), NODE)

    # Then
    assert findings == ['missing-limits', 'monolith', 'ratio-asymmetry']


def test_pod_without_a_controller_is_naked():
    # When
    findings = pod_findings(pod(400, 1_600_000, owner_kind=None), NODE)

    # Then
    assert findings == ['naked-pod']


def test_static_and_controller_owned_pods_are_not_naked():
    # When / Then — the kubelet owns a static pod and restarts it
    assert pod_findings(pod(400, 1_600_000, owner_kind='Node'), NODE) == []
    assert pod_findings(pod(400, 1_600_000, owner_kind='ReplicaSet'), NODE) == []


def worker(name: str, zone=None, capacity=None) -> NodeResources:
    labels = {}
    if zone:
        labels['topology.kubernetes.io/zone'] = zone
    if capacity:
        labels['karpenter.sh/capacity-type'] = capacity
    return NodeResources(name, 4000, 16_000_000, labels=labels)


def replicas(node_names: list, kind='Deployment', name='web') -> dict:
    pods = [PodResources(f'{name}-{i}', n, 100, 100_000, [], [], 'default', owner_kind=kind, owner_name=name)
            for i, n in enumerate(node_names)]
    return {('default', kind, name): pods}


def by_name(*nodes: NodeResources) -> dict:
    return {n.name: n for n in nodes}


def test_replicas_on_one_node_are_a_single_point_of_failure():
    # Given
    nodes = by_name(worker('a', 'z1'), worker('b', 'z2'))

    # When
    findings = workload_findings(replicas(['a', 'a', 'a']), nodes)

    # Then — reported on every replica, and the node finding hides the zone one
    assert findings == {('default', f'web-{i}'): ['spof-node'] for i in range(3)}


def test_replicas_in_one_zone_of_a_multi_zone_cluster_are_flagged():
    # Given
    nodes = by_name(worker('a', 'z1'), worker('b', 'z1'), worker('c', 'z2'))

    # When
    findings = workload_findings(replicas(['a', 'b']), nodes)

    # Then
    assert findings[('default', 'web-0')] == ['spof-zone']


def test_replicas_across_zones_are_not_flagged():
    # Given
    nodes = by_name(worker('a', 'z1'), worker('c', 'z2'))

    # When / Then
    assert workload_findings(replicas(['a', 'c']), nodes) == {}


def test_single_zone_or_unlabelled_cluster_never_reports_spof_zone():
    # Given — minikube / kind have one zone, or none at all
    for nodes in (by_name(worker('a', 'z1'), worker('b', 'z1')), by_name(worker('a'), worker('b'))):
        # When / Then
        assert workload_findings(replicas(['a', 'b']), nodes) == {}


def test_replica_on_an_unlabelled_node_is_not_called_one_zone():
    # Given — the zone of b is unknown, so the replicas may well be spread
    nodes = by_name(worker('a', 'z1'), worker('b'), worker('c', 'z2'))

    # When / Then
    assert workload_findings(replicas(['a', 'b']), nodes) == {}


def test_replicas_only_on_spot_are_flagged():
    # Given
    nodes = by_name(worker('a', capacity='spot'), worker('b', capacity='spot'), worker('c', capacity='on-demand'))

    # When / Then
    assert workload_findings(replicas(['a', 'b']), nodes)[('default', 'web-0')] == ['spof-spot']
    assert workload_findings(replicas(['a', 'c']), nodes) == {}


def test_spot_is_reported_alongside_the_placement_finding():
    # Given
    nodes = by_name(worker('a', capacity='spot'))

    # When / Then
    assert workload_findings(replicas(['a', 'a']), nodes)[('default', 'web-0')] == ['spof-node', 'spof-spot']


def test_statefulsets_and_bare_replicasets_are_checked_too():
    # Given
    nodes = by_name(worker('a'))

    # When / Then
    for kind in ('StatefulSet', 'ReplicaSet'):
        assert workload_findings(replicas(['a', 'a'], kind=kind), nodes)[('default', 'web-0')] == ['spof-node']


def test_daemonsets_and_jobs_are_not_spread_checked():
    # Given
    nodes = by_name(worker('a', capacity='spot'))

    # When / Then
    for kind in ('DaemonSet', 'Job'):
        assert workload_findings(replicas(['a', 'a'], kind=kind), nodes) == {}


def test_deployment_with_one_pod_is_a_single_replica():
    # Given
    nodes = by_name(worker('a'), worker('b'))

    # When / Then
    assert workload_findings(replicas(['a']), nodes) == {('default', 'web-0'): ['single-replica']}
    assert workload_findings(replicas(['a', 'b']), nodes) == {}


def test_statefulset_with_one_pod_is_a_single_replica():
    # When
    findings = workload_findings(replicas(['a'], kind='StatefulSet'), by_name(worker('a')))

    # Then
    assert findings == {('default', 'web-0'): ['single-replica']}


def test_one_pod_jobs_daemonsets_and_bare_replicasets_are_not_single_replicas():
    # When / Then
    for kind in ('Job', 'DaemonSet', 'ReplicaSet'):
        assert workload_findings(replicas(['a'], kind=kind), by_name(worker('a'))) == {}


def test_one_replica_is_not_a_spread_problem():
    # Given
    nodes = by_name(worker('a', capacity='spot'))

    # When
    findings = workload_findings(replicas(['a']), nodes)

    # Then
    assert not any(f.startswith('spof-') for fs in findings.values() for f in fs)
