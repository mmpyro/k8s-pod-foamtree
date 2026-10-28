import pytest
from k8sfoam.src.common.scheduler import free_capacity
from k8sfoam.src.synthetic.scenarios import ML, PLATFORM, SHOP, build_all, ml, platform, shop
from k8sfoam.src.utils.extractors import ResourcesExtractor
from k8sfoam.src.utils.mappers import FoamTreeMapper


def combined(m):
    extractor = ResourcesExtractor()
    nodes = [extractor.extract_node_resources(n) for n in m.nodes]
    pods = [extractor.extract_pod_requested_resources(p) for p in m.pods]
    return nodes, pods, FoamTreeMapper(nodes, pods).combined()


@pytest.fixture(scope='module')
def clusters():
    return {m.context: combined(m) for m in build_all()}


def test_the_same_seed_builds_the_same_clusters():
    # Given / When
    first, second = build_all(42), build_all(42)

    # Then
    for a, b in zip(first, second):
        assert [n.metadata.name for n in a.nodes] == [n.metadata.name for n in b.nodes]
        assert [(p.metadata.name, p.spec.node_name) for p in a.pods] == [(p.metadata.name, p.spec.node_name) for p in b.pods]


def test_another_seed_builds_other_clusters():
    # Given / When
    a, b = shop(1), shop(2)

    # Then
    assert [n.metadata.name for n in a.nodes] != [n.metadata.name for n in b.nodes]


def test_contexts_are_named_like_their_providers():
    # Given / When
    contexts = [m.context for m in build_all()]

    # Then — the first one is the active context
    assert contexts == [SHOP, ML, PLATFORM]
    assert 'eks' in SHOP and 'gke' in ML and 'eks' in PLATFORM


@pytest.mark.parametrize('context, warnings, findings', [
    (SHOP, {'cordoned', 'memory-pressure', 'tainted'},
     {'missing-requests', 'missing-limits', 'limit-overcommit', 'monolith', 'ratio-asymmetry', 'naked-pod',
      'single-replica', 'spof-node', 'spof-zone', 'spof-spot'}),
    (ML, {'tainted', 'stranded-devices'}, {'monolith', 'naked-pod', 'single-replica', 'spof-spot'}),
    (PLATFORM, {'cordoned', 'not-ready', 'disk-pressure', 'pid-pressure', 'tainted'},
     {'missing-requests', 'missing-limits', 'single-replica', 'spof-spot'}),
])
def test_each_cluster_shows_its_health_markers_and_audit_findings(clusters, context, warnings, findings):
    # Given
    _, _, payload = clusters[context]

    # When
    seen_warnings = {w for n in payload['nodes'] for w in n['warnings']}
    seen_findings = {f for n in payload['nodes'] for p in n['pods'] for f in p['findings']}

    # Then
    assert warnings <= seen_warnings
    assert findings <= seen_findings


def test_every_qos_class_is_in_the_shop(clusters):
    # Given
    _, _, payload = clusters[SHOP]

    # When
    classes = {p['qos'] for n in payload['nodes'] for p in n['pods']}

    # Then
    assert classes == {'BestEffort', 'Burstable', 'Guaranteed'}


def test_the_ml_cluster_offers_gpus_and_hugepages(clusters):
    # Given
    _, _, payload = clusters[ML]

    # Then
    assert {'nvidia.com/gpu', 'hugepages-2Mi', 'ephemeral-storage'} <= set(payload['resources'])


@pytest.mark.parametrize('build', [shop, ml])
def test_hand_built_clusters_leave_nothing_pending(build):
    # Given
    m = build()

    # When
    short = [w.name for w in m.workloads if w.kind not in ('DaemonSet', 'Node') and len(m.pods_of(w)) < w.replicas]

    # Then
    assert short == []


def test_the_platform_cluster_is_large(clusters):
    # Given
    nodes, pods, _ = clusters[PLATFORM]

    # Then
    assert len(nodes) > 100
    assert len(pods) > 1000


@pytest.mark.parametrize('context', [SHOP, ML, PLATFORM])
def test_scheduled_pods_fit_their_nodes(clusters, context):
    # Given
    nodes, pods, _ = clusters[context]

    # When
    free = free_capacity(nodes, pods)

    # Then — DaemonSet pods skip the capacity check, but the layout still leaves room for them
    assert all(room['cpu'] >= 0 and room['memory'] >= 0 and room['pods'] >= 0 for room in free.values())
    assert {p.node_name for p in pods} <= {n.name for n in nodes}


def test_platform_builds_from_any_seed():
    # Given / When
    m = platform(7)

    # Then
    assert len(m.nodes) == 102
