import pytest
import bitmath  # type: ignore
from k8sfoam.src.app import create_app
from unittest.mock import patch, MagicMock
from k8sfoam.src.common.dtos import NodeResources, PodResources, ContainerResources


@pytest.fixture
def test_client():
    app = create_app()
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
    k8s_client.assert_called_with('minikube')
    assert response.json['fits'] == 1
    verdicts = {r['node']: r for r in response.json['nodes']}
    assert verdicts['a']['fits'] is True
    assert verdicts['b']['reasons'][0]['message'] == 'Insufficient CPU: requires 2000m, available 1000m'


@pytest.mark.parametrize('body', [{'cpu': 'lots'}, {'cpu': '1', 'nodeSelector': ['zone=a']},
                                  {'cpu': '1', 'tolerations': 'all'}, ['cpu']])
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
