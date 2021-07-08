import pytest
import bitmath  # type: ignore
from app.app import create_app
from unittest.mock import patch, MagicMock
from app.common.dtos import NodeResources, PodResources, ContainerResources


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
@patch('app.app.K8sClient')
def test_get_k8s_resources(k8s_client, parameter, test_client):
    # Given
    k8s_client_instance = MagicMock()
    k8s_client_instance.get_node_resources.return_value = [NodeResources('minikube', 2000, float(bitmath.GiB(1).kB))]
    k8s_client_instance.get_pod_resources.return_value = [PodResources('etcd', 'minikube', 150, float(bitmath.MiB(150).kB),
     [ContainerResources('etcd', 100, float(bitmath.MiB(100).kB)), ContainerResources('side', 50, float(bitmath.MiB(50).kB))])]
    k8s_client.return_value = k8s_client_instance

    # When
    response = test_client.get(f'/resources/{parameter}')
    json = response.json

    # Then
    assert response.status_code == 200
    assert json['groups'][0]['label'] == 'minikube'
    assert len(json['groups'][0]['groups']) == 2
    assert len(json['groups'][0]['groups'][0]['groups']) == 2


@patch('app.app.K8sClient')
def test_should_return_bad_request(k8s_client, test_client):
    # Given
    k8s_client_instance = MagicMock()
    k8s_client_instance.get_node_resources.return_value = [NodeResources('minikube', 2000, float(bitmath.GiB(1).kB))]
    k8s_client_instance.get_pod_resources.return_value = [PodResources('etcd', 'minikube', 150, float(bitmath.MiB(150).kB),
     [ContainerResources('etcd', 100, float(bitmath.MiB(100).kB)), ContainerResources('side', 50, float(bitmath.MiB(50).kB))])]
    k8s_client.return_value = k8s_client_instance
    resource_type = 'disk'

    # When
    response = test_client.get(f'/resources/{resource_type}')

    # Then
    assert response.status_code == 400


@patch('app.app.K8sClient')
def test_should_return_internal_server_error(k8s_client, test_client):
    # Given
    k8s_client_instance = MagicMock()
    k8s_client_instance.get_node_resources.side_effect = Exception('Cannot connect to k8s api')
    k8s_client_instance.get_pod_resources.return_value = [PodResources('etcd', 'minikube', 150, float(bitmath.MiB(150).kB),
     [ContainerResources('etcd', 100, float(bitmath.MiB(100).kB)), ContainerResources('side', 50, float(bitmath.MiB(50).kB))])]
    k8s_client.return_value = k8s_client_instance

    # When
    response = test_client.get('/resources/cpu')

    # Then
    assert response.status_code == 500
