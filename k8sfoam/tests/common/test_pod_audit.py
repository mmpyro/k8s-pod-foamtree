from k8sfoam.src.common.dtos import ContainerResources, NodeResources, PodResources
from k8sfoam.src.common.pod_audit import pod_findings


# 4 cores / 16000 MB (in kB) — a 1:4 core:GB node.
NODE = NodeResources('worker', 4000, 16_000_000)


def pod(cpu: float, memory: float, memory_limit=1, init_containers=None) -> PodResources:
    containers = [ContainerResources('app', cpu, memory, memory_limit)]
    return PodResources('app', 'worker', cpu, memory, containers, init_containers or [])


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
