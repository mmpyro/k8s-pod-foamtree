import bitmath  # type: ignore
import pytest
from k8sfoam.src.k8s.k8s_client import K8sClient, core_v1, list_all, DEFAULT_PAGE_SIZE
from unittest.mock import patch, MagicMock, call
from k8sfoam.tests.common.mocks import create_node, create_pod, create_container, create_list
from pydash import py_ as _  # type: ignore

POD_SELECTOR = 'status.phase!=Succeeded,status.phase!=Failed'


@pytest.fixture(autouse=True)
def fresh_client_cache():
    core_v1.cache_clear()


@patch('k8sfoam.src.k8s.k8s_client.config')
@patch('k8sfoam.src.k8s.k8s_client.client')
def test_should_return_node_resources(client, config):
    # Given
    k8s_client = K8sClient()
    v1_client = MagicMock()
    v1_client.list_node.return_value = create_list([create_node('minikube', '2', '8162156Ki')])
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
    v1_client.list_pod_for_all_namespaces.return_value = create_list(
        [create_pod('etcd', 'minikube', containers=[create_container('etcd', '100m', '1G')])])
    client.CoreV1Api.return_value = v1_client

    # When
    pod_resources = [*k8s_client.get_pod_resources()]

    # Then
    v1_client.list_pod_for_all_namespaces.assert_called_once_with(
        limit=DEFAULT_PAGE_SIZE, _continue=None, field_selector=POD_SELECTOR)
    pod = _.head(pod_resources)
    assert pod.name == 'etcd'
    assert pod.node_name == 'minikube'
    assert _.head(pod.containers).name == 'etcd'
    assert _.head(pod.containers).cpu == 100
    assert _.head(pod.containers).memory == bitmath.GB(1).kB


@patch('k8sfoam.src.k8s.k8s_client.config')
@patch('k8sfoam.src.k8s.k8s_client.client')
def test_should_page_through_pods_with_continue_tokens(client, config):
    # Given — three pages chained by continue tokens
    v1_client = MagicMock()
    v1_client.list_pod_for_all_namespaces.side_effect = [
        create_list([create_pod('a', 'n1'), create_pod('b', 'n1')], next_page='t1'),
        create_list([create_pod('c', 'n2'), create_pod('d', 'n2')], next_page='t2'),
        create_list([create_pod('e', 'n3')]),
    ]
    client.CoreV1Api.return_value = v1_client

    # When
    pods = [*K8sClient(page_size=2).get_pod_resources()]

    # Then — every page is read, each with the token the previous one returned
    assert [p.name for p in pods] == ['a', 'b', 'c', 'd', 'e']
    assert v1_client.list_pod_for_all_namespaces.call_args_list == [
        call(limit=2, _continue=None, field_selector=POD_SELECTOR),
        call(limit=2, _continue='t1', field_selector=POD_SELECTOR),
        call(limit=2, _continue='t2', field_selector=POD_SELECTOR),
    ]


@patch('k8sfoam.src.k8s.k8s_client.config')
@patch('k8sfoam.src.k8s.k8s_client.client')
def test_should_page_through_nodes(client, config):
    # Given
    v1_client = MagicMock()
    v1_client.list_node.side_effect = [create_list([create_node('a', '1', '1Gi')], next_page='t1'),
                                       create_list([create_node('b', '1', '1Gi')])]
    client.CoreV1Api.return_value = v1_client

    # When
    nodes = [*K8sClient(page_size=1).get_node_resources()]

    # Then
    assert [n.name for n in nodes] == ['a', 'b']
    assert v1_client.list_node.call_args_list == [call(limit=1, _continue=None), call(limit=1, _continue='t1')]


def test_list_all_returns_the_snapshot_resource_version():
    # Given — every page of one paginated LIST shares the snapshot's version
    func = MagicMock(side_effect=[create_list(['a'], next_page='t1', resource_version='42'),
                                  create_list(['b'], resource_version='42')])

    # When
    items, resource_version = list_all(func, 1)

    # Then — the informer starts its WATCH from here
    assert items == ['a', 'b']
    assert resource_version == '42'


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
    v1_client = client.CoreV1Api.return_value
    v1_client.list_node.return_value = create_list([])
    v1_client.list_pod_for_all_namespaces.return_value = create_list([])
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
