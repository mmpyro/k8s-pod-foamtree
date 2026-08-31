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
    k8s_client_instance.get_node_resources.return_value = [NodeResources('minikube', 2000, float(bitmath.GiB(1).kB), {})]
    k8s_client_instance.get_pod_resources.return_value = [PodResources('etcd', 'minikube', 150, float(bitmath.MiB(150).kB),
     [ContainerResources('etcd', 100, float(bitmath.MiB(100).kB), {}), ContainerResources('side', 50, float(bitmath.MiB(50).kB), {})], [], {})]
    k8s_client.return_value = k8s_client_instance

    # When
    response = test_client.get(f'/resources/{parameter}')
    json = response.json

    # Then
    assert response.status_code == 200
    assert json['groups'][0]['label'] == 'minikube'
    assert len(json['groups'][0]['groups']) == 2
    assert len(json['groups'][0]['groups'][0]['groups']) == 2


@patch('k8sfoam.src.app.K8sClient')
def test_get_k8s_extended_resources(k8s_client, test_client):
    # Given
    k8s_client_instance = MagicMock()
    k8s_client_instance.get_node_resources.return_value = [NodeResources('gpu-node', 4000, float(bitmath.GiB(16).kB), {'nvidia.com/gpu': 4.0})]
    k8s_client_instance.get_pod_resources.return_value = [PodResources('gpu-pod', 'gpu-node', 1000, float(bitmath.GiB(4).kB),
     [ContainerResources('trainer', 1000, float(bitmath.GiB(4).kB), {'nvidia.com/gpu': 2.0})], [], {'nvidia.com/gpu': 2.0})]
    k8s_client.return_value = k8s_client_instance

    # When
    response = test_client.get('/resources/nvidia.com/gpu')
    json = response.json

    # Then
    assert response.status_code == 200
    assert json['groups'][0]['label'] == 'gpu-node'
    assert json['groups'][0]['weight'] == 4.0


@patch('k8sfoam.src.app.K8sClient')
def test_get_extended_resource_keys(k8s_client, test_client):
    # Given
    k8s_client_instance = MagicMock()
    k8s_client_instance.get_node_resources.return_value = [NodeResources('gpu-node', 4000, float(bitmath.GiB(16).kB), {'nvidia.com/gpu': 4.0})]
    k8s_client_instance.get_pod_resources.return_value = [PodResources('gpu-pod', 'gpu-node', 1000, float(bitmath.GiB(4).kB),
     [ContainerResources('trainer', 1000, float(bitmath.GiB(4).kB), {'nvidia.com/gpu': 2.0})], [], {'nvidia.com/gpu': 2.0})]
    k8s_client.return_value = k8s_client_instance

    # When
    response = test_client.get('/resources/extended-keys')
    json = response.json

    # Then
    assert response.status_code == 200
    assert json == ['nvidia.com/gpu']


@patch('k8sfoam.src.app.K8sClient')
def test_should_return_internal_server_error(k8s_client, test_client):
    # Given
    k8s_client_instance = MagicMock()
    k8s_client_instance.get_node_resources.side_effect = Exception('Cannot connect to k8s api')
    k8s_client_instance.get_pod_resources.return_value = [PodResources('etcd', 'minikube', 150, float(bitmath.MiB(150).kB),
     [ContainerResources('etcd', 100, float(bitmath.MiB(100).kB), {}), ContainerResources('side', 50, float(bitmath.MiB(50).kB), {})], [], {})]
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
