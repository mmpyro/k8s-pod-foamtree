import bitmath  # type: ignore
from app.k8s.k8s_client import K8sClient
from unittest.mock import patch, MagicMock
from tests.common.mocks import create_node, create_pod, create_container
from pydash import py_ as _  # type: ignore


@patch('app.k8s.k8s_client.config')
@patch('app.k8s.k8s_client.client')
def test_should_return_node_resources(client, config):
    # Given
    k8s_client = K8sClient()
    v1_client = MagicMock()
    v1_node_list = MagicMock()
    v1_node_list.items = [create_node('minikube', '2', '8162156Ki')]
    v1_client.list_node.return_value = v1_node_list
    client.CoreV1Api.return_value = v1_client

    # When
    node_resources = k8s_client.get_node_resources()

    # Then
    assert _.head(node_resources).name == 'minikube'
    assert _.head(node_resources).cpu == 2000
    assert _.head(node_resources).memory == bitmath.KiB(8162156).kB


@patch('app.k8s.k8s_client.config')
@patch('app.k8s.k8s_client.client')
def test_should_return_pod_resources(client, config):
    # Given
    k8s_client = K8sClient()
    v1_client = MagicMock()
    v1_pod_list = MagicMock()
    v1_pod_list.items = [create_pod('etcd', 'minikube', containers=[create_container('etcd', '100m', '1G')])]
    v1_client.list_pod_for_all_namespaces.return_value = v1_pod_list
    client.CoreV1Api.return_value = v1_client

    # When
    pod_resources = k8s_client.get_pod_resources()

    # Then
    pod = _.head(pod_resources)
    assert pod.name == 'etcd'
    assert pod.node_name == 'minikube'
    assert _.head(pod.containers).name == 'etcd'
    assert _.head(pod.containers).cpu == 100
    assert _.head(pod.containers).memory == bitmath.GB(1).kB
