import bitmath  # type: ignore
import pytest
from k8sfoam.src.k8s.k8s_client import K8sClient, core_v1
from unittest.mock import patch, MagicMock, call
from k8sfoam.tests.common.mocks import create_node, create_pod, create_container
from pydash import py_ as _  # type: ignore


@pytest.fixture(autouse=True)
def fresh_client_cache():
    core_v1.cache_clear()


@patch('k8sfoam.src.k8s.k8s_client.config')
@patch('k8sfoam.src.k8s.k8s_client.client')
def test_should_return_node_resources(client, config):
    # Given
    k8s_client = K8sClient()
    v1_client = MagicMock()
    v1_node_list = MagicMock()
    v1_node_list.items = [create_node('minikube', '2', '8162156Ki')]
    v1_client.list_node.return_value = v1_node_list
    client.CoreV1Api.return_value = v1_client

    # When
    node_resources = [*k8s_client.get_node_resources()]

    # Then
    assert _.head(node_resources).name == 'minikube'
    assert _.head(node_resources).cpu == 2000
    assert _.head(node_resources).memory == bitmath.KiB(8162156).kB


@patch('k8sfoam.src.k8s.k8s_client.config')
@patch('k8sfoam.src.k8s.k8s_client.client')
def test_should_return_pod_resources(client, config):
    # Given
    k8s_client = K8sClient()
    v1_client = MagicMock()
    v1_pod_list = MagicMock()
    v1_pod_list.items = [create_pod('etcd', 'minikube', containers=[create_container('etcd', '100m', '1G')])]
    v1_client.list_pod_for_all_namespaces.return_value = v1_pod_list
    client.CoreV1Api.return_value = v1_client

    # When
    pod_resources = [*k8s_client.get_pod_resources()]

    # Then
    v1_client.list_pod_for_all_namespaces.assert_called_once_with(field_selector='status.phase!=Succeeded,status.phase!=Failed')
    pod = _.head(pod_resources)
    assert pod.name == 'etcd'
    assert pod.node_name == 'minikube'
    assert _.head(pod.containers).name == 'etcd'
    assert _.head(pod.containers).cpu == 100
    assert _.head(pod.containers).memory == bitmath.GB(1).kB


@patch('k8sfoam.src.k8s.k8s_client.config')
def test_should_return_k8s_contexts(config):
    # Given
    config.list_kube_config_contexts.return_value = ([{'context': {'cluster': 'minikube', 'extensions': [{'extension': {'last-update': 'Fri, 09 Jul 2021 16:29:57 CEST', 'provider': 'minikube.sigs.k8s.io', 'version': 'v1.21.0'}, 'name': 'context_info'}], 'namespace': 'default', 'user': 'minikube'}, 'name': 'minikube'}, {'context': {'cluster': 'minikube-test', 'extensions': [{'extension': {'last-update': 'Fri, 09 Jul 2021 16:29:57 CEST', 'provider': 'minikube.sigs.k8s.io', 'version': 'v1.21.0'}, 'name': 'context_info'}], 'namespace': 'default', 'user': 'minikube-test'}, 'name': 'minikube-test'}], {'context': {'cluster': 'minikube', 'extensions': [{'extension': {'last-update': 'Fri, 09 Jul 2021 16:29:57 CEST', 'provider': 'minikube.sigs.k8s.io', 'version': 'v1.21.0'}, 'name': 'context_info'}], 'namespace': 'default', 'user': 'minikube'}, 'name': 'minikube'})  # noqa: E501
    k8s_client = K8sClient()

    # When
    contexts = k8s_client.get_contexts()

    # Then
    assert len(contexts) == 2
    assert contexts[0]['context'] == 'minikube'
    assert contexts[0]['active'] is True
    assert contexts[1]['context'] == 'minikube-test'
    assert contexts[1]['active'] is False


@patch('k8sfoam.src.k8s.k8s_client.config')
@patch('k8sfoam.src.k8s.k8s_client.client')
def test_should_build_one_api_client_per_context(client, config):
    # Given — every browser refresh asks twice (cpu, then memory)
    for context in ['prod', 'dev', 'prod', 'dev']:
        # When
        [*K8sClient(context).get_node_resources()]
        [*K8sClient(context).get_pod_resources()]

    # Then — the kubeconfig (and any exec plugin in it) is read once per context
    assert config.new_client_from_config.call_args_list == [call(context='prod'), call(context='dev')]
    config.load_kube_config.assert_not_called()


@patch('k8sfoam.src.k8s.k8s_client.config')
def test_listing_contexts_builds_no_api_client(config):
    # Given
    config.list_kube_config_contexts.return_value = ([{'name': 'prod'}], {'name': 'prod'})

    # When
    K8sClient().get_contexts()

    # Then — no credentials are fetched just to fill the context picker
    config.new_client_from_config.assert_not_called()
    config.load_kube_config.assert_not_called()
