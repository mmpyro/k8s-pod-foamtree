import pytest
from k8sfoam.src.synthetic.client import SyntheticCluster, SyntheticRegistry
from k8sfoam.src.synthetic.scenarios import ML, PLATFORM, SHOP, shop


@pytest.fixture(scope='module')
def registry():
    return SyntheticRegistry(churn_interval=0)


def test_registry_lists_every_scenario_with_the_shop_active(registry):
    # Given / When
    contexts = registry.contexts()

    # Then
    assert contexts == [{'context': SHOP, 'active': True}, {'context': ML, 'active': False},
                        {'context': PLATFORM, 'active': False}]


def test_no_context_means_the_active_one(registry):
    # Given / When / Then
    assert registry.get(None) is registry.get(SHOP)
    assert registry.get(ML).context == ML


def test_an_unknown_context_is_refused(registry):
    # Given / When / Then
    with pytest.raises(LookupError, match='Unknown synthetic context: prod'):
        registry.get('prod')


def test_cluster_reads_like_a_cluster_cache():
    # Given
    cluster = SyntheticCluster(shop())

    # When
    nodes, pods = cluster.get_node_resources(), cluster.get_pod_resources()

    # Then — extracted DTOs, and copies the caller may keep
    assert len(nodes) == 16 and pods
    assert cluster.get_pod_resources() is not pods
    assert cluster.version == 0


def test_churn_bumps_the_version_and_wakes_watchers():
    # Given
    cluster = SyntheticCluster(shop())

    # When
    for _ in range(10):
        cluster.churn()

    # Then — a watcher behind returns at once; one up to date waits out its timeout
    assert cluster.version == 10
    assert cluster.wait_for_change(0, timeout=0) == 10
    assert cluster.wait_for_change(10, timeout=0.01) == 10


def test_registry_keeps_clusters_changing_in_the_background():
    # Given
    churning = SyntheticRegistry(churn_interval=0.05)

    # When
    try:
        version = churning.get(SHOP).wait_for_change(0, timeout=5)
    finally:
        churning.close()

    # Then
    assert version > 0
