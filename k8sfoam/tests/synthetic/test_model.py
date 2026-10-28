from kubernetes.utils import parse_quantity  # type: ignore
from k8sfoam.src.synthetic.model import (HOSTNAME, TOLERATE_ALL, ClusterModel, Container, NodePool, Workload,
                                         effective_requests, qos_class)
from k8sfoam.src.utils.extractors import ResourcesExtractor

ZONES = ('zone-a', 'zone-b')


def model(nodes: int = 2, cpu: str = '4', memory: str = '16Gi', **pool) -> ClusterModel:
    m = ClusterModel('test', 'eks', 'eu-west-1', seed=1)
    m.add_pool(NodePool('general', nodes, 'm5.xlarge', cpu, memory, ZONES, **pool))
    return m


def web(replicas: int = 1, cpu: str = '1', memory: str = '1Gi', **kwargs) -> Workload:
    return Workload('default', 'web', 'Deployment', [Container('web', cpu=cpu, memory=memory)], replicas=replicas,
                    **kwargs)


def test_effective_requests_follow_the_extractor_rule():
    # Given — a native sidecar adds to the regular containers and to the init container started after it
    w = Workload('default', 'app', 'Deployment', [Container('app', cpu='500m', memory='1Gi')], init_containers=[
        Container('proxy', cpu='100m', memory='128Mi', sidecar=True),
        Container('migrate', cpu='2', memory='256Mi')])
    m = model()
    pod = m.deploy(w)[0]

    # When
    requests = effective_requests(w)
    extracted = ResourcesExtractor().extract_pod_requested_resources(pod)

    # Then
    assert requests == {'cpu': parse_quantity('2100m'), 'memory': parse_quantity('1Gi') + parse_quantity('128Mi')}
    assert extracted.cpu == 2100


def test_qos_class_matches_the_api_server():
    # Given / When / Then
    assert qos_class(Workload('d', 'a', None, [Container('a')])) == 'BestEffort'
    assert qos_class(Workload('d', 'a', None, [Container('a', cpu_limit='1', memory_limit='1Gi')])) == 'Guaranteed'
    assert qos_class(Workload('d', 'a', None, [Container('a', cpu='1', memory='1Gi', cpu_limit='1',
                                                         memory_limit='1Gi')])) == 'Guaranteed'
    assert qos_class(Workload('d', 'a', None, [Container('a', cpu='1', memory='1Gi', memory_limit='2Gi')])) == 'Burstable'


def test_pods_never_overfill_a_node():
    # Given — two 4-core nodes, ten 1-core replicas
    m = model(nodes=2, cpu='4')

    # When
    pods = m.deploy(web(replicas=10))

    # Then — the other two stay Pending, so they are not created
    assert len(pods) == 8
    assert {p.spec.node_name for p in pods} == {n.metadata.name for n in m.nodes}


def test_replicas_spread_over_nodes():
    # Given
    m = model(nodes=3)

    # When
    pods = m.deploy(web(replicas=3, cpu='100m'))

    # Then
    assert len({p.spec.node_name for p in pods}) == 3


def test_placement_honours_node_selector_taints_and_cordon():
    # Given — one tainted node, one cordoned node, one plain node
    m = model(nodes=1)
    tainted = m.add_pool(NodePool('gpu', 1, 'g5', '8', '32Gi', ZONES,
                                  taints=[{'key': 'dedicated', 'value': 'gpu', 'effect': 'NoSchedule'}]))[0]
    cordoned = m.add_pool(NodePool('old', 1, 'm4', '8', '32Gi', ZONES))[0]
    m.cordon(cordoned)
    plain = m.nodes[0].metadata.name

    # When
    anywhere = m.deploy(web(replicas=3, cpu='100m'))
    pinned = m.deploy(Workload('default', 'trainer', 'Job', [Container('t', cpu='1', memory='1Gi')],
                               node_selector={HOSTNAME: tainted},
                               tolerations=[{'key': 'dedicated', 'operator': 'Exists'}]))

    # Then
    assert {p.spec.node_name for p in anywhere} == {plain}
    assert [p.spec.node_name for p in pinned] == [tainted]


def test_daemonset_runs_on_every_node_it_tolerates():
    # Given
    m = model(nodes=2)
    tainted = m.add_pool(NodePool('gpu', 1, 'g5', '8', '32Gi', ZONES,
                                  taints=[{'key': 'dedicated', 'value': 'gpu', 'effect': 'NoSchedule'}]))[0]
    m.cordon(tainted)
    agent = Workload('kube-system', 'agent', 'DaemonSet', [Container('agent', cpu='100m', memory='64Mi')])
    picky = Workload('kube-system', 'picky', 'DaemonSet', [Container('picky', cpu='100m', memory='64Mi')])
    agent.tolerations = list(TOLERATE_ALL)

    # When
    everywhere = m.deploy(agent)
    untainted = m.deploy(picky)

    # Then
    assert len(everywhere) == 3
    assert tainted not in {p.spec.node_name for p in untainted} and len(untainted) == 2


def test_owners_resolve_like_a_real_cluster():
    # Given
    m = model()
    extractor = ResourcesExtractor()

    # When
    deployment = extractor.extract_pod_requested_resources(m.deploy(web())[0])
    statefulset = m.deploy(Workload('db', 'pg', 'StatefulSet', [Container('pg', cpu='1', memory='1Gi')], replicas=2))
    static = extractor.extract_pod_requested_resources(
        m.deploy(Workload('kube-system', 'etcd', 'Node', [Container('etcd', cpu='100m', memory='64Mi')]))[0])
    naked = extractor.extract_pod_requested_resources(m.deploy(Workload('default', 'shell', None, [Container('sh')]))[0])

    # Then
    assert (deployment.owner_kind, deployment.owner_name) == ('Deployment', 'web')
    assert [p.metadata.name for p in statefulset] == ['pg-0', 'pg-1']
    assert (static.owner_kind, static.owner_name) == ('Node', None)
    assert (naked.owner_kind, naked.owner_name) == (None, None)


def test_pod_labels_combine_app_defaults_and_the_workload():
    # Given
    m = ClusterModel('test', 'eks', 'eu-west-1', seed=1, default_labels={'env': 'prod'})
    m.add_pool(NodePool('general', 1, 'm5.xlarge', '4', '16Gi', ZONES))

    # When
    pod = m.deploy(web(labels={'env': 'staging', 'tier': 'frontend'}))[0]

    # Then
    assert pod.metadata.labels['app'] == 'web'
    assert pod.metadata.labels['env'] == 'staging'
    assert pod.metadata.labels['tier'] == 'frontend'


def test_node_labels_carry_topology_per_provider():
    # Given / When
    eks = model(spot=True).nodes[0].metadata.labels
    gke = ClusterModel('gke_p_r_c', 'gke', 'us-central1', seed=1)
    gke.add_pool(NodePool('pool', 1, 'e2', '4', '16Gi', ('us-central1-a',), spot=True))
    karpenter = ClusterModel('k', 'karpenter', 'us-east-1', seed=1)
    karpenter.add_pool(NodePool('general', 1, 'm6i', '4', '16Gi', ('us-east-1a',)))

    # Then
    assert eks['eks.amazonaws.com/capacityType'] == 'SPOT'
    assert gke.nodes[0].metadata.labels['cloud.google.com/gke-spot'] == 'true'
    assert karpenter.nodes[0].metadata.labels['karpenter.sh/capacity-type'] == 'on-demand'


def test_churn_keeps_deployments_within_two_replicas_of_their_size():
    # Given
    m = model(nodes=4, cpu='8', memory='32Gi')
    w = web(replicas=4, cpu='100m', memory='128Mi')
    m.deploy(w)

    # When
    sizes = []
    for _ in range(200):
        m.churn()
        sizes.append(len(m.pods_of(w)))

    # Then
    assert min(sizes) >= 2 and max(sizes) <= 6
    assert len(set(sizes)) > 1


def test_churn_brings_a_flapped_node_back():
    # Given — no scalable workload, so every step is about nodes
    m = model(nodes=3)

    # When
    events = [m.churn() for _ in range(100)]

    # Then
    flaps = [i for i, e in enumerate(events) if e.endswith('NotReady')]
    assert flaps
    assert all(events[i + 1].endswith('Ready again') for i in flaps if i + 1 < len(events))


def test_scaling_down_frees_room_for_other_pods():
    # Given — a full 4-core node
    m = model(nodes=1, cpu='4')
    w = web(replicas=4, cpu='1')
    m.deploy(w)

    # When — churn scales the Deployment down somewhere between 2 and 4 replicas
    for _ in range(50):
        m.churn()
    extra = m.deploy(Workload('default', 'batch', 'Job', [Container('b', cpu='1', memory='1Gi')], replicas=4))

    # Then — the new pods take exactly the cores the scale-down gave back
    assert len(m.pods_of(w)) + len(extra) == 4
