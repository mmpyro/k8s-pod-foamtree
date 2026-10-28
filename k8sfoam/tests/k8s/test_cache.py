import threading
import pytest
from unittest.mock import patch, MagicMock
from kubernetes.client.rest import ApiException  # type: ignore
from urllib3.exceptions import ProtocolError
from k8sfoam.src.k8s.cache import node_informer, pod_informer, ClusterCache, CacheRegistry
from k8sfoam.tests.common.mocks import create_node, create_pod, create_list


def event(kind, obj, resource_version):
    return {'type': kind, 'object': obj, 'raw_object': {'metadata': {'resourceVersion': resource_version}}}


def bookmark(resource_version):
    # The client leaves BOOKMARK objects as raw dicts.
    raw = {'kind': 'Pod', 'metadata': {'resourceVersion': resource_version}}
    return {'type': 'BOOKMARK', 'object': raw, 'raw_object': raw}


def names(resources):
    return sorted(r.name for r in resources)


def steps(*responses):
    """A side_effect that answers each call with the next response: exceptions
    are raised, callables are called, anything else is returned. A plain
    side_effect list would return a callable instead of calling it."""
    queue = list(responses)

    def respond(*args, **kwargs):
        response = queue.pop(0)
        if isinstance(response, Exception):
            raise response
        return response(*args, **kwargs) if callable(response) else response
    return respond


def run(informer):
    """Drive the informer loop until a scripted response stops it; fail instead of hanging."""
    thread = threading.Thread(target=informer.run, daemon=True)
    thread.start()
    thread.join(5)
    if thread.is_alive():
        informer.stop()
        pytest.fail('informer loop did not stop')


@pytest.fixture
def fake_watch():
    with patch('k8sfoam.src.k8s.cache.watch') as watch:
        yield watch.Watch.return_value


@pytest.fixture(autouse=True)
def no_backoff():
    with patch('k8sfoam.src.k8s.cache.MIN_BACKOFF', 0):
        yield


def test_watch_events_add_modify_and_delete_nodes(fake_watch):
    # Given
    v1 = MagicMock()
    v1.list_node.return_value = create_list([create_node('a', '1', '1Gi'), create_node('b', '1', '1Gi')],
                                            resource_version='10')
    fake_watch.stream.return_value = iter([
        event('ADDED', create_node('c', '1', '1Gi'), '11'),
        event('MODIFIED', create_node('a', '4', '1Gi'), '12'),
        event('DELETED', create_node('b', '1', '1Gi'), '13'),
    ])
    informer = node_informer(v1, 500)

    # When
    informer.relist()
    informer.watch()

    # Then
    nodes = {n.name: n for n in informer.snapshot(1)}
    assert sorted(nodes) == ['a', 'c']
    assert nodes['a'].cpu == 4000
    stream_kwargs = fake_watch.stream.call_args.kwargs
    assert fake_watch.stream.call_args.args == (v1.list_node,)
    assert stream_kwargs['resource_version'] == '10'
    assert stream_kwargs['allow_watch_bookmarks'] is True
    assert informer.resource_version == '13'


def test_pod_that_turns_succeeded_is_dropped(fake_watch):
    # Given
    v1 = MagicMock()
    v1.list_pod_for_all_namespaces.return_value = create_list([create_pod('job', 'n1'), create_pod('web', 'n1')])
    fake_watch.stream.return_value = iter([event('MODIFIED', create_pod('job', 'n1', phase='Succeeded'), '2')])
    informer = pod_informer(v1, 500)

    # When
    informer.relist()
    informer.watch()

    # Then
    assert names(informer.snapshot(1)) == ['web']
    assert fake_watch.stream.call_args.kwargs['field_selector'] == 'status.phase!=Succeeded,status.phase!=Failed'


def test_pods_are_keyed_by_namespace_and_name(fake_watch):
    # Given
    v1 = MagicMock()
    v1.list_pod_for_all_namespaces.return_value = create_list([create_pod('web', 'n1', namespace='a'),
                                                               create_pod('web', 'n1', namespace='b')])
    fake_watch.stream.return_value = iter([event('DELETED', create_pod('web', 'n1', namespace='a'), '2')])
    informer = pod_informer(v1, 500)

    # When
    informer.relist()
    informer.watch()

    # Then
    assert [p.namespace for p in informer.snapshot(1)] == ['b']


def test_bookmark_advances_the_resource_version_of_the_next_watch(fake_watch):
    # Given
    v1 = MagicMock()
    v1.list_node.return_value = create_list([create_node('a', '1', '1Gi')], resource_version='10')
    fake_watch.stream.side_effect = [iter([bookmark('99')]), iter([])]
    informer = node_informer(v1, 500)

    # When
    informer.relist()
    informer.watch()
    informer.watch()

    # Then — the store is untouched and the next WATCH resumes from the bookmark
    assert names(informer.snapshot(1)) == ['a']
    assert fake_watch.stream.call_args_list[1].kwargs['resource_version'] == '99'


def test_gone_relists_and_replaces_the_store(fake_watch):
    # Given
    v1 = MagicMock()
    informer = node_informer(v1, 500)

    def second_list(**kwargs):
        informer.stop()
        return create_list([create_node('b', '1', '1Gi')], resource_version='50')

    v1.list_node.side_effect = steps(create_list([create_node('a', '1', '1Gi')], resource_version='10'), second_list)
    fake_watch.stream.side_effect = ApiException(status=410, reason='Gone')

    # When
    run(informer)

    # Then
    assert v1.list_node.call_count == 2
    assert names(informer.snapshot(1)) == ['b']
    assert informer.resource_version == '50'


def test_dropped_connection_rewatches_without_relisting(fake_watch):
    # Given
    v1 = MagicMock()
    v1.list_node.return_value = create_list([create_node('a', '1', '1Gi')], resource_version='10')
    informer = node_informer(v1, 500)

    def second_watch(*args, **kwargs):
        informer.stop()
        return iter([])

    fake_watch.stream.side_effect = steps(ProtocolError('Connection broken'), second_watch)

    # When
    run(informer)

    # Then
    assert v1.list_node.call_count == 1
    assert fake_watch.stream.call_args_list[1].kwargs['resource_version'] == '10'


def test_failed_relist_makes_reads_raise_until_a_relist_succeeds(fake_watch):
    # Given
    v1 = MagicMock()
    v1.list_node.side_effect = [create_list([create_node('a', '1', '1Gi')]), Exception('connection refused'),
                                create_list([create_node('b', '1', '1Gi')])]
    informer = node_informer(v1, 500)
    informer.relist()

    # When — the API server goes away; stale nodes must not be served as live
    with pytest.raises(Exception, match='connection refused'):
        informer.relist()

    # Then
    with pytest.raises(Exception, match='connection refused'):
        informer.snapshot(1)
    informer.relist()
    assert names(informer.snapshot(1)) == ['b']


def test_initial_list_failure_is_retried_by_the_loop(fake_watch):
    # Given
    v1 = MagicMock()
    informer = node_informer(v1, 500)

    def stop_watching(*args, **kwargs):
        informer.stop()
        return iter([])

    v1.list_node.side_effect = [Exception('connection refused'), create_list([create_node('a', '1', '1Gi')])]
    fake_watch.stream.side_effect = stop_watching

    # When
    run(informer)

    # Then
    assert names(informer.snapshot(1)) == ['a']


def test_bad_watch_event_relists(fake_watch):
    # Given — re-watching would replay the same event forever, so a relist must surface it
    v1 = MagicMock()
    informer = node_informer(v1, 500)

    def second_list(**kwargs):
        informer.stop()
        return create_list([create_node('a', '1', '1Gi')])

    broken = create_node('broken', '1', '1Gi')
    broken.status.allocatable = {}
    v1.list_node.side_effect = steps(create_list([]), second_list)
    fake_watch.stream.return_value = iter([event('ADDED', broken, '2')])

    # When
    run(informer)

    # Then
    assert v1.list_node.call_count == 2


def test_only_real_store_changes_notify(fake_watch):
    # Given
    v1 = MagicMock()
    v1.list_node.return_value = create_list([create_node('a', '1', '1Gi')], resource_version='10')
    fake_watch.stream.return_value = iter([
        event('ADDED', create_node('c', '1', '1Gi'), '11'),
        event('MODIFIED', create_node('a', '1', '1Gi'), '12'),
        event('MODIFIED', create_node('a', '4', '1Gi'), '13'),
        bookmark('14'),
        event('DELETED', create_node('gone', '1', '1Gi'), '15'),
        event('DELETED', create_node('c', '1', '1Gi'), '16'),
    ])
    on_change = MagicMock()
    informer = node_informer(v1, 500, on_change=on_change)

    # When
    informer.relist()
    informer.watch()

    # Then — the first list, the add, the resize and the delete; not the no-op
    # update, the bookmark or the delete of an unknown node
    assert on_change.call_count == 4


def test_relist_notifies_on_the_first_failure_and_on_recovery(fake_watch):
    # Given
    v1 = MagicMock()
    same = create_list([create_node('a', '1', '1Gi')])
    v1.list_node.side_effect = [same, Exception('down'), Exception('down'), same, same]
    on_change = MagicMock()
    informer = node_informer(v1, 500, on_change=on_change)

    # When / Then
    informer.relist()
    assert on_change.call_count == 1
    for _ in range(2):
        with pytest.raises(Exception, match='down'):
            informer.relist()
    assert on_change.call_count == 2
    informer.relist()
    assert on_change.call_count == 3
    informer.relist()
    assert on_change.call_count == 3


@patch('k8sfoam.src.k8s.cache.pod_informer')
@patch('k8sfoam.src.k8s.cache.node_informer')
@patch('k8sfoam.src.k8s.cache.core_v1')
def test_cache_version_moves_when_either_informer_changes(core_v1, node_informer, pod_informer):
    # Given
    cache = ClusterCache('prod', 500)
    nodes_changed = node_informer.call_args.kwargs['on_change']
    pods_changed = pod_informer.call_args.kwargs['on_change']

    # When / Then — a quiet cache times out with the same version
    assert cache.wait_for_change(0, 0.01) == 0

    # When / Then — a waiter wakes up as soon as an informer reports a change
    timer = threading.Timer(0.05, pods_changed)
    timer.start()
    assert cache.wait_for_change(0, 5) == 1
    timer.join()
    nodes_changed()
    assert cache.version == 2


def test_snapshot_times_out_before_the_first_list():
    # Given
    informer = node_informer(MagicMock(), 500)

    # When / Then
    with pytest.raises(TimeoutError):
        informer.snapshot(0)


def blocking_stream(released):
    def stream(*args, **kwargs):
        released.wait(5)
        return iter([])
    return stream


@patch('k8sfoam.src.k8s.cache.core_v1')
def test_warm_cache_makes_no_api_calls_per_refresh(core_v1, fake_watch):
    # Given
    v1 = core_v1.return_value
    v1.list_node.return_value = create_list([create_node('a', '1', '1Gi')])
    v1.list_pod_for_all_namespaces.return_value = create_list([create_pod('web', 'a')])
    released = threading.Event()
    fake_watch.stream.side_effect = blocking_stream(released)
    cache = ClusterCache('prod', 500)
    cache.start()

    try:
        # When — a few browser refreshes, each asking for cpu and memory
        for _ in range(3):
            assert names(cache.get_node_resources()) == ['a']
            assert names(cache.get_pod_resources()) == ['web']

        # Then
        core_v1.assert_called_once_with('prod')
        assert v1.list_node.call_count == 1
        assert v1.list_pod_for_all_namespaces.call_count == 1
    finally:
        cache.stop()
        released.set()


@patch('k8sfoam.src.k8s.cache.core_v1')
def test_cache_stops_itself_when_the_initial_list_fails(core_v1, fake_watch):
    # Given
    core_v1.return_value.list_node.side_effect = Exception('Unauthorized')
    core_v1.return_value.list_pod_for_all_namespaces.side_effect = Exception('Unauthorized')
    cache = ClusterCache('prod', 500)
    cache.start()

    # When
    with pytest.raises(Exception, match='Unauthorized'):
        cache.get_node_resources()

    # Then — the registry will replace it on the next request
    assert cache.stopped


@pytest.fixture
def cluster_cache():
    with patch('k8sfoam.src.k8s.cache.ClusterCache') as cluster_cache:
        cluster_cache.side_effect = lambda context, page_size: MagicMock(stopped=False, context=context)
        yield cluster_cache


def test_registry_keeps_one_cache_per_context(cluster_cache):
    # Given
    registry = CacheRegistry(500)

    # When
    prod, dev, prod_again = registry.get('prod'), registry.get('dev'), registry.get('prod')
    registry.close()

    # Then
    assert prod is prod_again
    assert dev is not prod
    assert [c.args for c in cluster_cache.call_args_list] == [('prod', 500), ('dev', 500)]
    prod.start.assert_called_once()


def test_registry_sweep_stops_idle_caches(cluster_cache):
    # Given
    clock = MagicMock(return_value=0)
    registry = CacheRegistry(500, idle_timeout=60, clock=clock)
    prod = registry.get('prod')
    clock.return_value = 50
    dev = registry.get('dev')

    # When
    clock.return_value = 100
    registry.sweep()

    # Then
    prod.stop.assert_called_once()
    dev.stop.assert_not_called()
    assert registry.get('prod') is not prod
    registry.close()


def test_registry_replaces_a_stopped_cache(cluster_cache):
    # Given
    registry = CacheRegistry(500)
    prod = registry.get('prod')
    prod.stopped = True

    # When
    replacement = registry.get('prod')
    registry.close()

    # Then
    assert replacement is not prod
