import pytest
import bitmath  # type: ignore
from k8sfoam.src.app import create_app, main
from unittest.mock import patch, MagicMock
from k8sfoam.src.common.dtos import NodeResources, PodResources, ContainerResources


@pytest.fixture
def test_client():
    # Per-request listing, so each test can patch K8sClient.
    app = create_app(watch=False)
    return app.test_client()


def test_healthcheck(test_client):
    # When
    response = test_client.get('/healthcheck')

    # Then
    assert response.status_code == 200


@pytest.mark.parametrize("parameter", ['cpu', 'memory'])
@patch('k8sfoam.src.app.K8sClient')
def test_get_k8s_resources(k8s_client, parameter, test_client):
    # Given
    k8s_client_instance = MagicMock()
    k8s_client_instance.get_node_resources.return_value = [NodeResources('minikube', 2000, float(bitmath.GiB(1).kB))]
    k8s_client_instance.get_pod_resources.return_value = [PodResources('etcd', 'minikube', 150, float(bitmath.MiB(150).kB),
     [ContainerResources('etcd', 100, float(bitmath.MiB(100).kB)), ContainerResources('side', 50, float(bitmath.MiB(50).kB))], [])]
    k8s_client.return_value = k8s_client_instance

    # When
    response = test_client.get(f'/resources/{parameter}')
    json = response.json

    # Then
    assert response.status_code == 200
    assert json['groups'][0]['label'] == 'minikube'
    assert len(json['groups'][0]['groups']) == 2
    assert len(json['groups'][0]['groups'][0]['groups']) == 2
    # Node health rides on the same payload the frontend already fetches
    assert json['groups'][0]['warnings'] == []
    assert json['groups'][0]['unschedulable'] is False


@patch('k8sfoam.src.app.K8sClient')
def test_should_return_bad_request(k8s_client, test_client):
    # Given
    k8s_client_instance = MagicMock()
    k8s_client_instance.get_node_resources.return_value = [NodeResources('minikube', 2000, float(bitmath.GiB(1).kB))]
    k8s_client_instance.get_pod_resources.return_value = [PodResources('etcd', 'minikube', 150, float(bitmath.MiB(150).kB),
     [ContainerResources('etcd', 100, float(bitmath.MiB(100).kB)), ContainerResources('side', 50, float(bitmath.MiB(50).kB))], [])]
    k8s_client.return_value = k8s_client_instance
    resource_type = 'disk'

    # When
    response = test_client.get(f'/resources/{resource_type}')

    # Then
    assert response.status_code == 400


@patch('k8sfoam.src.app.K8sClient')
def test_should_return_internal_server_error(k8s_client, test_client):
    # Given
    k8s_client_instance = MagicMock()
    k8s_client_instance.get_node_resources.side_effect = Exception('Cannot connect to k8s api')
    k8s_client_instance.get_pod_resources.return_value = [PodResources('etcd', 'minikube', 150, float(bitmath.MiB(150).kB),
     [ContainerResources('etcd', 100, float(bitmath.MiB(100).kB)), ContainerResources('side', 50, float(bitmath.MiB(50).kB))], [])]
    k8s_client.return_value = k8s_client_instance

    # When
    response = test_client.get('/resources/cpu')

    # Then
    assert response.status_code == 500


@patch('k8sfoam.src.app.K8sClient')
def test_should_return_internal_server_error_when_get_contexts(k8s_client, test_client):
    # Given
    k8s_client_instance = MagicMock()
    k8s_client_instance.get_contexts.side_effect = Exception('Cannot connect to k8s api')
    k8s_client.return_value = k8s_client_instance

    # When
    response = test_client.get('/contexts')

    # Then
    assert response.status_code == 500


@patch('k8sfoam.src.app.K8sClient')
def test_should_return_contexts_when_get_contexts(k8s_client, test_client):
    # Given
    k8s_client_instance = MagicMock()
    k8s_client_instance.get_contexts.return_value = [{'context': 'minikube', 'active': True}, {'context': 'minikube-test', 'active': False}]
    k8s_client.return_value = k8s_client_instance

    # When
    response = test_client.get('/contexts')
    json = response.json

    # Then
    assert response.status_code == 200
    assert json[0]['context'] == 'minikube'
    assert json[0]['active'] is True
    assert json[1]['context'] == 'minikube-test'
    assert json[1]['active'] is False


def simulation_cluster():
    k8s_client_instance = MagicMock()
    k8s_client_instance.get_node_resources.return_value = [NodeResources('a', 4000, float(bitmath.GiB(16).kB)),
                                                           NodeResources('b', 1000, float(bitmath.GiB(16).kB))]
    k8s_client_instance.get_pod_resources.return_value = [PodResources('web', 'a', 1000, float(bitmath.GiB(1).kB), [], [],
                                                                       owner_kind='ReplicaSet')]
    return k8s_client_instance


@patch('k8sfoam.src.app.K8sClient')
def test_simulate_fit_returns_per_node_verdicts(k8s_client, test_client):
    # Given
    k8s_client.return_value = simulation_cluster()

    # When
    response = test_client.post('/simulate/fit?context=minikube', json={'cpu': '2', 'memory': '1Gi'})

    # Then
    assert response.status_code == 200
    k8s_client.assert_called_with('minikube', 500)
    assert response.json['fits'] == 1
    verdicts = {r['node']: r for r in response.json['nodes']}
    assert verdicts['a']['fits'] is True
    assert verdicts['b']['reasons'][0]['message'] == 'Insufficient CPU: requires 2000m, available 1000m'


@patch('k8sfoam.src.app.K8sClient')
def test_simulate_fit_checks_extended_resources(k8s_client, test_client):
    # Given
    cluster = simulation_cluster()
    cluster.get_node_resources.return_value = [
        NodeResources('gpu', 4000, float(bitmath.GiB(16).kB), extended={'nvidia.com/gpu': 2}),
        NodeResources('cpu', 4000, float(bitmath.GiB(16).kB))]
    k8s_client.return_value = cluster

    # When
    response = test_client.post('/simulate/fit', json={'cpu': '1', 'extended': {'nvidia.com/gpu': '1'}})

    # Then
    assert response.status_code == 200
    verdicts = {r['node']: r for r in response.json['nodes']}
    assert verdicts['gpu']['fits'] is True
    assert verdicts['cpu']['reasons'][0]['message'] == 'Insufficient nvidia.com/gpu: requires 1, available 0'


@pytest.mark.parametrize('body', [{'cpu': 'lots'}, {'cpu': '1', 'nodeSelector': ['zone=a']},
                                  {'cpu': '1', 'tolerations': 'all'}, ['cpu'],
                                  {'cpu': '1', 'extended': {'pods': '1'}}, {'cpu': '1', 'extended': {'cpu': '1'}},
                                  {'cpu': '1', 'extended': {'nvidia.com/gpu': 'many'}}, {'extended': ['gpu']}])
@patch('k8sfoam.src.app.K8sClient')
def test_simulate_fit_rejects_malformed_spec(k8s_client, body, test_client):
    # When
    response = test_client.post('/simulate/fit', json=body)

    # Then
    assert response.status_code == 400
    k8s_client.assert_not_called()


@patch('k8sfoam.src.app.K8sClient')
def test_simulate_fit_returns_internal_server_error(k8s_client, test_client):
    # Given
    k8s_client.return_value.get_node_resources.side_effect = Exception('Cannot connect to k8s api')

    # When
    response = test_client.post('/simulate/fit', json={'cpu': '1'})

    # Then
    assert response.status_code == 500


@patch('k8sfoam.src.app.K8sClient')
def test_simulate_drain_returns_placements(k8s_client, test_client):
    # Given
    k8s_client.return_value = simulation_cluster()

    # When
    response = test_client.get('/simulate/drain/a')

    # Then
    assert response.status_code == 200
    assert response.json['fits'] is True
    assert response.json['placements'][0]['to'] == 'b'


@patch('k8sfoam.src.app.K8sClient')
def test_simulate_drain_unknown_node_returns_not_found(k8s_client, test_client):
    # Given
    k8s_client.return_value = simulation_cluster()

    # When
    response = test_client.get('/simulate/drain/ghost')

    # Then
    assert response.status_code == 404


@patch('k8sfoam.src.app.K8sClient')
def test_simulate_drain_returns_internal_server_error(k8s_client, test_client):
    # Given
    k8s_client.return_value.get_pod_resources.side_effect = Exception('Cannot connect to k8s api')

    # When
    response = test_client.get('/simulate/drain/a')

    # Then
    assert response.status_code == 500


@patch('k8sfoam.src.app.K8sClient')
def test_should_serve_a_vendor_prefixed_extended_resource(k8s_client, test_client):
    # Given
    k8s_client_instance = MagicMock()
    k8s_client_instance.get_node_resources.return_value = [
        NodeResources('gpu', 2000, float(bitmath.GiB(1).kB), extended={'nvidia.com/gpu': 4})]
    k8s_client_instance.get_pod_resources.return_value = [PodResources('train', 'gpu', 150, float(bitmath.MiB(150).kB),
     [ContainerResources('train', 150, float(bitmath.MiB(150).kB), extended={'nvidia.com/gpu': 1})], [],
     extended={'nvidia.com/gpu': 1})]
    k8s_client.return_value = k8s_client_instance

    # When
    response = test_client.get('/resources/nvidia.com/gpu')

    # Then
    assert response.status_code == 200
    assert response.json['groups'][0]['weight'] == 4
    assert response.json['groups'][0]['groups'][0]['weight'] == 1


@patch('k8sfoam.src.app.K8sClient')
def test_should_reject_an_extended_resource_no_node_allocates(k8s_client, test_client):
    # Given
    k8s_client_instance = MagicMock()
    k8s_client_instance.get_node_resources.return_value = [
        NodeResources('minikube', 2000, float(bitmath.GiB(1).kB), extended={'hugepages-2Mi': 0})]
    k8s_client_instance.get_pod_resources.return_value = []
    k8s_client.return_value = k8s_client_instance

    # When
    response = test_client.get('/resources/hugepages-2Mi')

    # Then
    assert response.status_code == 400
    assert b'[cpu, memory]' in response.data


@patch('k8sfoam.src.app.K8sClient')
@patch('k8sfoam.src.app.CacheRegistry')
def test_watch_mode_serves_resources_from_the_cache(registry, k8s_client):
    # Given
    registry.return_value.get.return_value = simulation_cluster()
    test_client = create_app(page_size=100).test_client()

    # When
    response = test_client.get('/resources/cpu?context=prod')

    # Then
    assert response.status_code == 200
    registry.assert_called_once_with(100)
    registry.return_value.get.assert_called_once_with('prod')
    k8s_client.assert_not_called()


@patch('k8sfoam.src.app.K8sClient')
@patch('k8sfoam.src.app.CacheRegistry')
def test_watch_mode_runs_simulations_on_the_cache(registry, k8s_client):
    # Given
    registry.return_value.get.return_value = simulation_cluster()
    test_client = create_app().test_client()

    # When
    fit = test_client.post('/simulate/fit?context=prod', json={'cpu': '1'})
    drain = test_client.get('/simulate/drain/a?context=prod')

    # Then
    assert fit.status_code == 200
    assert drain.status_code == 200
    assert registry.return_value.get.call_count == 2
    k8s_client.assert_not_called()


@patch('k8sfoam.src.app.CacheRegistry')
def test_watch_mode_reports_a_cache_failure(registry):
    # Given
    registry.return_value.get.return_value.get_node_resources.side_effect = Exception('Unauthorized')
    test_client = create_app().test_client()

    # When
    response = test_client.get('/resources/cpu')

    # Then
    assert response.status_code == 500
    assert response.data == b'Unauthorized'


@patch('k8sfoam.src.app.K8sClient')
@patch('k8sfoam.src.app.CacheRegistry')
def test_no_watch_lists_per_request_with_the_page_size(registry, k8s_client):
    # Given
    k8s_client.return_value = simulation_cluster()
    test_client = create_app(page_size=50, watch=False).test_client()

    # When
    response = test_client.get('/resources/memory?context=prod')

    # Then
    assert response.status_code == 200
    k8s_client.assert_called_once_with('prod', 50)
    registry.assert_not_called()


@pytest.mark.parametrize('argv, expected', [
    ([], {'page_size': 500, 'watch': True}),
    (['--page-size', '100', '--no-watch'], {'page_size': 100, 'watch': False}),
])
@patch('k8sfoam.src.app.create_app')
def test_main_passes_cache_options(create_app, argv, expected):
    # Given
    with patch('sys.argv', ['k8sfoams', *argv]):
        # When
        main()

    # Then
    create_app.assert_called_once_with(**expected)


@pytest.mark.parametrize('page_size', ['0', '-5', 'many'])
def test_main_rejects_a_page_size_below_one(page_size):
    # Given
    with patch('sys.argv', ['k8sfoams', '--page-size', page_size]):
        # When / Then
        with pytest.raises(SystemExit):
            main()
