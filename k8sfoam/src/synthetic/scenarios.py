"""The synthetic clusters behind `k8sfoams --synthetic` and the docs demo.

Each scenario is one kubeconfig-style context, named the way EKS and GKE name
theirs so the context picker tags it. Between them they trip every node health
marker, audit finding and QoS class the dashboard knows, so each feature has
something to show. The same seed always builds the same cluster.
"""
from typing import Callable, List, Tuple
from k8sfoam.src.synthetic.model import (HOSTNAME, TOLERATE_ALL, ZONE, ClusterModel, Container, NodePool,
                                         Workload)

DEFAULT_SEED = 42

SHOP = 'arn:aws:eks:eu-west-1:000000000000:cluster/demo-shop'
ML = 'gke_demo-ml_us-central1_ml-training'
PLATFORM = 'arn:aws:eks:us-east-1:000000000000:cluster/demo-platform'

GPU = 'nvidia.com/gpu'
GPU_TAINT = {'key': GPU, 'value': 'present', 'effect': 'NoSchedule'}
TOLERATE_GPU = [{'key': GPU, 'operator': 'Exists', 'effect': 'NoSchedule'}]


def _daemonsets(m: ClusterModel, names: Tuple[str, ...]) -> None:
    """Node agents every cluster runs, one pod per node, tolerating every taint."""
    catalog = {
        'aws-node': Workload('kube-system', 'aws-node', 'DaemonSet', [
            Container('aws-node', cpu='25m', memory='64Mi'),
            Container('aws-eks-nodeagent', cpu='25m', memory='64Mi')]),
        'kube-proxy': Workload('kube-system', 'kube-proxy', 'DaemonSet', [
            Container('kube-proxy', cpu='100m', memory='128Mi')]),
        'ebs-csi-node': Workload('kube-system', 'ebs-csi-node', 'DaemonSet', [
            Container('ebs-plugin', cpu='10m', memory='40Mi', memory_limit='256Mi'),
            Container('node-driver-registrar', cpu='10m', memory='40Mi', memory_limit='256Mi')]),
        'node-exporter': Workload('monitoring', 'node-exporter', 'DaemonSet', [
            Container('node-exporter', cpu='100m', memory='64Mi', memory_limit='128Mi')]),
        'fluent-bit': Workload('logging', 'fluent-bit', 'DaemonSet', [
            Container('fluent-bit', cpu='100m', memory='128Mi', memory_limit='512Mi')]),
        'datadog-agent': Workload('datadog', 'datadog-agent', 'DaemonSet', [
            Container('agent', cpu='200m', memory='256Mi', memory_limit='512Mi'),
            Container('trace-agent', cpu='100m', memory='128Mi', memory_limit='256Mi')]),
        'gke-metadata-server': Workload('kube-system', 'gke-metadata-server', 'DaemonSet', [
            Container('gke-metadata-server', cpu='100m', memory='100Mi', cpu_limit='100m', memory_limit='100Mi')]),
        'fluentbit-gke': Workload('kube-system', 'fluentbit-gke', 'DaemonSet', [
            Container('fluentbit', cpu='50m', memory='100Mi', memory_limit='250Mi'),
            Container('fluentbit-gke', cpu='50m', memory='100Mi', memory_limit='250Mi')]),
    }
    for name in names:
        w = catalog[name]
        w.tolerations = list(TOLERATE_ALL)
        m.deploy(w)


def _web(namespace: str, name: str, replicas: int, cpu: str, memory: str, limit: str, **kwargs) -> Workload:
    return Workload(namespace, name, 'Deployment', [Container(name, cpu=cpu, memory=memory, memory_limit=limit)],
                    replicas=replicas, **kwargs)


def _guaranteed(name: str, cpu: str, memory: str) -> Container:
    return Container(name, cpu=cpu, memory=memory, cpu_limit=cpu, memory_limit=memory)


def shop(seed: int = DEFAULT_SEED) -> ClusterModel:
    """A mid-sized EKS web shop: managed node groups over three zones, and a few bad habits."""
    m = ClusterModel(SHOP, 'eks', 'eu-west-1', seed, default_labels={'env': 'prod'})
    zones = ('eu-west-1a', 'eu-west-1b', 'eu-west-1c')
    critical = {'key': 'CriticalAddonsOnly', 'value': 'true', 'effect': 'NoSchedule'}
    analytics_taint = {'key': 'dedicated', 'value': 'analytics', 'effect': 'NoSchedule'}
    m.add_pool(NodePool('system', 2, 'm5.xlarge', '3920m', '14Gi', zones[:2], pods=58, taints=[critical]))
    general = m.add_pool(NodePool('general', 8, 'm5.2xlarge', '7910m', '29Gi', zones, pods=58))
    spot = m.add_pool(NodePool('spot', 5, 'm5.2xlarge', '7910m', '29Gi', zones, pods=58, spot=True))
    m.add_pool(NodePool('analytics', 1, 'r5.2xlarge', '7910m', '61Gi', zones[:1], pods=58,
                        taints=[analytics_taint]))

    _daemonsets(m, ('aws-node', 'kube-proxy', 'ebs-csi-node', 'node-exporter', 'fluent-bit'))

    on_system = {'node_selector': {'eks.amazonaws.com/nodegroup': 'system'},
                 'tolerations': [{'key': 'CriticalAddonsOnly', 'operator': 'Exists'}]}
    m.deploy(_web('kube-system', 'coredns', 2, '100m', '70Mi', '170Mi', **on_system))
    m.deploy(_web('kube-system', 'metrics-server', 1, '100m', '200Mi', '400Mi', **on_system))
    m.deploy(_web('ingress-nginx', 'ingress-nginx-controller', 3, '500m', '512Mi', '1Gi'))

    m.deploy(Workload('monitoring', 'prometheus', 'StatefulSet', [
        Container('prometheus', cpu='1', memory='6Gi', memory_limit='8Gi')], init_containers=[
        Container('config-reloader', cpu='10m', memory='32Mi', memory_limit='64Mi', sidecar=True)]))
    m.deploy(Workload('monitoring', 'alertmanager', 'StatefulSet', [
        Container('alertmanager', cpu='100m', memory='256Mi', memory_limit='512Mi')], replicas=2))
    m.deploy(_web('monitoring', 'grafana', 1, '250m', '512Mi', '1Gi'))

    # Service mesh: a native sidecar (restartPolicy: Always) plus a classic init container.
    mesh = [Container('istio-proxy', cpu='100m', memory='128Mi', memory_limit='256Mi', sidecar=True),
            Container('istio-init', cpu='100m', memory='64Mi', cpu_limit='100m', memory_limit='64Mi')]
    frontend = {'tier': 'frontend'}
    m.deploy(Workload('storefront', 'web', 'Deployment', [
        Container('web', cpu='250m', memory='512Mi', memory_limit='1Gi')], replicas=6, init_containers=mesh,
        labels=frontend))
    m.deploy(Workload('storefront', 'graphql-gateway', 'Deployment', [
        Container('gateway', cpu='500m', memory='1Gi', memory_limit='2Gi')], replicas=3, init_containers=mesh,
        labels=frontend))
    m.deploy(_web('storefront-staging', 'web', 2, '100m', '256Mi', '512Mi', labels={**frontend, 'env': 'staging'}))

    m.deploy(Workload('checkout', 'checkout-api', 'Deployment', [_guaranteed('checkout-api', '500m', '1Gi')],
                      replicas=3))
    # Every replica pinned to one zone: a zone outage takes payments down (spof-zone).
    m.deploy(Workload('checkout', 'payments', 'Deployment', [_guaranteed('payments', '500m', '1Gi')], replicas=3,
                      node_selector={ZONE: zones[0]}))
    m.deploy(_web('orders', 'orders-api', 4, '500m', '1Gi', '2Gi'))
    m.deploy(Workload('orders', 'kafka', 'StatefulSet', [
        Container('kafka', cpu='2', memory='8Gi', cpu_limit='2', memory_limit='8Gi')], replicas=3))
    m.deploy(_web('catalog', 'catalog-api', 4, '300m', '512Mi', '1Gi'))
    m.deploy(Workload('catalog', 'catalog-db', 'StatefulSet', [
        Container('postgres', cpu='2', memory='8Gi', cpu_limit='2', memory_limit='8Gi',
                  extended={'ephemeral-storage': '10Gi'})], replicas=3))
    # minAvailable equal to the replica count: no node running a replica can be drained.
    m.add_budget('catalog', 'catalog-db', {'app': 'catalog-db'}, min_available=3)
    m.deploy(_web('cart', 'cart-api', 4, '200m', '256Mi', '512Mi'))
    # A cache that only runs on spot capacity (spof-spot).
    m.deploy(Workload('cart', 'cart-redis', 'StatefulSet', [
        Container('redis', cpu='500m', memory='2Gi', memory_limit='2Gi')], replicas=3,
        node_selector={'eks.amazonaws.com/capacityType': 'SPOT'}))
    # Both replicas pinned to one node (spof-node).
    m.deploy(_web('search', 'search-indexer', 2, '1', '2Gi', '3Gi', node_selector={HOSTNAME: general[0]}))
    # CPU-heavy, memory-light: strands the node's memory (ratio-asymmetry).
    m.deploy(_web('recommendations', 'recs-model', 2, '3', '256Mi', '512Mi'))
    # Memory limit 8x the request (limit-overcommit).
    m.deploy(_web('media', 'image-resizer', 3, '200m', '256Mi', '2Gi'))
    m.deploy(_web('auth', 'auth-service', 3, '250m', '512Mi', '512Mi'))
    m.deploy(_web('notifications', 'notifier', 2, '100m', '256Mi', '512Mi'))
    # No CPU request and no limits (missing-requests, missing-limits).
    m.deploy(Workload('billing', 'invoice-renderer', 'Deployment', [Container('renderer', memory='512Mi')],
                      replicas=2))
    # Owns most of a dedicated node (monolith) and runs as a single replica.
    m.deploy(Workload('analytics', 'warehouse', 'StatefulSet', [
        Container('clickhouse', cpu='6500m', memory='52Gi', memory_limit='56Gi')],
        node_selector={'eks.amazonaws.com/nodegroup': 'analytics'},
        tolerations=[{'key': 'dedicated', 'operator': 'Equal', 'value': 'analytics', 'effect': 'NoSchedule'}]))
    m.deploy(Workload('batch', 'report-generator', 'Job', [Container('report', cpu='2', memory='4Gi',
                                                                     memory_limit='4Gi')]))
    m.deploy(Workload('batch', 'nightly-etl', 'Job', [Container('etl', cpu='1', memory='2Gi', memory_limit='3Gi')],
                      replicas=3))

    # A handful of small services whose sizes come from the seed.
    for name in ('reviews', 'wishlist', 'shipping', 'inventory', 'pricing', 'loyalty', 'coupons', 'returns'):
        cpu = m.rng.choice(('100m', '200m', '250m', '500m'))
        memory = m.rng.choice(('128Mi', '256Mi', '512Mi', '1Gi'))
        limit = m.rng.choice((memory, '1Gi', '2Gi'))
        m.deploy(_web(name, f'{name}-api', m.rng.randint(2, 4), cpu, memory, limit))

    # Someone's debug shell: no controller and no requests (naked-pod, BestEffort).
    m.deploy(Workload('default', 'debug-shell', None, [Container('shell')]))

    # Pending pods, last so the layout above stays the same. The backfill needs an
    # almost empty m5.2xlarge, which one more node of the general pool would be;
    # nothing runs arm64; the import waits for Kueue to admit it.
    m.deploy(Workload('batch', 'quarterly-backfill', 'Job', [
        Container('backfill', cpu='7', memory='24Gi', memory_limit='24Gi')], keep_pending=True))
    m.deploy(_web('storefront', 'web-arm-canary', 1, '250m', '512Mi', '1Gi',
                  node_selector={'kubernetes.io/arch': 'arm64'}, keep_pending=True))
    m.deploy(Workload('batch', 'catalog-import', 'Job', [Container('import', cpu='1', memory='2Gi', memory_limit='2Gi')],
                      scheduling_gates=['kueue.x-k8s.io/admission']))

    m.cordon(general[3])
    m.set_condition(spot[1], 'MemoryPressure', True)
    return m


def ml(seed: int = DEFAULT_SEED) -> ClusterModel:
    """A GKE training and inference cluster: tainted GPU pools, hugepages, and GPUs nobody can use."""
    m = ClusterModel(ML, 'gke', 'us-central1', seed)
    zones = ('us-central1-a', 'us-central1-b', 'us-central1-c')
    accelerator = 'cloud.google.com/gke-accelerator'
    m.add_pool(NodePool('default-pool', 3, 'e2-standard-8', '7910m', '29Gi', zones))
    a100 = m.add_pool(NodePool('gpu-a100', 4, 'a2-highgpu-2g', '23850m', '158Gi', zones[:2], taints=[GPU_TAINT],
                               extended={GPU: '2'}, labels={accelerator: 'nvidia-tesla-a100'}))
    m.add_pool(NodePool('gpu-l4-spot', 3, 'g2-standard-8', '7910m', '29Gi', zones, spot=True, taints=[GPU_TAINT],
                        extended={GPU: '1'}, labels={accelerator: 'nvidia-l4'}))
    m.add_pool(NodePool('highmem', 2, 'n2-highmem-16', '15890m', '122Gi', zones[:2],
                        extended={'hugepages-2Mi': '8Gi'}))

    _daemonsets(m, ('gke-metadata-server', 'fluentbit-gke'))
    for model in ('nvidia-tesla-a100', 'nvidia-l4'):
        m.deploy(Workload('kube-system', f'nvidia-gpu-device-plugin-{model.split("-")[-1]}', 'DaemonSet', [
            Container('nvidia-gpu-device-plugin', cpu='50m', memory='50Mi', memory_limit='50Mi')],
            node_selector={accelerator: model}, tolerations=list(TOLERATE_ALL)))

    m.deploy(_web('kube-system', 'kube-dns', 2, '100m', '70Mi', '210Mi'))
    m.deploy(_web('kube-system', 'konnectivity-agent', 3, '10m', '30Mi', '125Mi'))
    m.deploy(_web('mlops', 'mlflow', 1, '500m', '2Gi', '4Gi'))
    m.deploy(_web('mlops', 'airflow-scheduler', 2, '1', '2Gi', '4Gi'))
    m.deploy(_web('mlops', 'airflow-webserver', 1, '500m', '1Gi', '2Gi'))

    # CPU-only preprocessing squatting on an A100 node: its two GPUs are left
    # free with no CPU to drive them (stranded-devices). Placed before the GPU
    # jobs so they cannot take the node first.
    m.deploy(_web('data', 'spark-prep', 1, '21500m', '40Gi', '48Gi',
                  node_selector={HOSTNAME: a100[0]}, tolerations=list(TOLERATE_GPU)))
    m.deploy(Workload('data', 'feature-store', 'StatefulSet', [
        Container('feature-store', cpu='4', memory='32Gi', cpu_limit='4', memory_limit='32Gi',
                  extended={'hugepages-2Mi': '2Gi'})], replicas=2,
        node_selector={'cloud.google.com/gke-nodepool': 'highmem'}))

    on_a100 = {accelerator: 'nvidia-tesla-a100'}
    m.deploy(Workload('ml-training', 'llm-finetune', 'Job', [
        Container('trainer', cpu='20', memory='120Gi', memory_limit='140Gi', extended={GPU: '2'})],
        node_selector=on_a100, tolerations=list(TOLERATE_GPU)))
    m.deploy(Workload('ml-serving', 'embedder', 'Deployment', [
        Container('embedder', cpu='2', memory='12Gi', memory_limit='16Gi', extended={GPU: '1'})],
        node_selector=on_a100, tolerations=list(TOLERATE_GPU)))
    # A notebook started by hand: holds a GPU with nothing to bring it back (naked-pod).
    m.deploy(Workload('notebooks', 'jupyter-alice', None, [
        Container('notebook', cpu='1', memory='8Gi', extended={GPU: '1'})],
        node_selector=on_a100, tolerations=list(TOLERATE_GPU)))
    # All replicas on spot GPUs (spof-spot).
    m.deploy(Workload('ml-serving', 'llm-inference', 'Deployment', [
        Container('vllm', cpu='4', memory='16Gi', memory_limit='20Gi', extended={GPU: '1'})], replicas=3,
        node_selector={accelerator: 'nvidia-l4'}, tolerations=list(TOLERATE_GPU)))
    m.deploy(_web('ml-serving', 'inference-gateway', 2, '500m', '512Mi', '1Gi'))
    m.deploy(Workload('ml-training', 'eval-runner', 'Job', [
        Container('eval', cpu='2', memory='8Gi', memory_limit='8Gi')], replicas=4))
    return m


NAMESPACES = ('payments', 'orders', 'checkout', 'catalog', 'search', 'inventory', 'shipping', 'users', 'auth',
              'notifications', 'analytics', 'reporting', 'ml-features', 'ads', 'pricing', 'reviews', 'media',
              'gateway', 'billing', 'fraud', 'recommendations', 'messaging', 'support', 'partners', 'loyalty')
ROLES = ('api', 'worker', 'consumer', 'grpc', 'web', 'cache', 'scheduler', 'indexer', 'sync')
# (cpu, memory) request pairs; most services ask for roughly 1 core per 2-4Gi.
SIZES = (('50m', '128Mi'), ('100m', '256Mi'), ('250m', '512Mi'), ('250m', '1Gi'), ('500m', '1Gi'),
         ('500m', '2Gi'), ('1', '2Gi'), ('1', '4Gi'), ('2', '4Gi'))


def _platform_workload(m: ClusterModel, namespace: str, role: str) -> Workload:
    """One service of the shared platform; its size, replicas and habits come from the seed."""
    replicas = m.rng.choice((1, 2, 2, 3, 3, 3, 4, 4, 6, 8, 12, 16, 24))
    cpu, memory = m.rng.choice(SIZES)
    roll = m.rng.random()
    if roll < 0.15:
        container = Container(role, cpu=cpu, memory=memory, cpu_limit=cpu, memory_limit=memory)
    elif roll < 0.35:
        container = Container(role, cpu=cpu, memory=memory)
    elif roll < 0.45:
        container = Container(role, cpu=cpu, memory=memory, memory_limit='8Gi')
    elif roll < 0.47:
        container = Container(role)
    else:
        container = Container(role, cpu=cpu, memory=memory, memory_limit=memory)
    extra: dict = {}
    placement = m.rng.random()
    if placement < 0.08:
        extra = {'node_selector': {'karpenter.sh/capacity-type': 'spot'}}
    elif placement < 0.16:
        extra = {'node_selector': {'karpenter.sh/nodepool': 'memory'}}
        container.memory = container.memory_limit = '12Gi'
    kind = 'StatefulSet' if role == 'cache' else 'Deployment'
    return Workload(namespace, f'{namespace}-{role}', kind, [container], replicas=replicas, **extra)


def platform(seed: int = DEFAULT_SEED) -> ClusterModel:
    """A big shared EKS platform on Karpenter: about a hundred nodes and a few thousand pods."""
    m = ClusterModel(PLATFORM, 'karpenter', 'us-east-1', seed)
    zones = ('us-east-1a', 'us-east-1b', 'us-east-1c')
    m.add_pool(NodePool('system', 3, 'm6i.xlarge', '3920m', '14Gi', zones, pods=58))
    general = m.add_pool(NodePool('general', 30, 'm6i.2xlarge', '7910m', '29Gi', zones, pods=58))
    general += m.add_pool(NodePool('general', 15, 'm6i.4xlarge', '15890m', '58Gi', zones, pods=234))
    spot = m.add_pool(NodePool('spot', 35, 'c6i.4xlarge', '15890m', '28Gi', zones, pods=234, spot=True))
    m.add_pool(NodePool('memory', 15, 'r6i.4xlarge', '15890m', '120Gi', zones, pods=234))
    m.add_pool(NodePool('gpu', 4, 'g5.2xlarge', '7910m', '29Gi', zones[:2], pods=58, taints=[GPU_TAINT],
                        extended={GPU: '1'}))

    _daemonsets(m, ('aws-node', 'kube-proxy', 'ebs-csi-node', 'node-exporter', 'datadog-agent'))
    m.deploy(_web('kube-system', 'coredns', 4, '100m', '70Mi', '170Mi'))
    m.deploy(_web('karpenter', 'karpenter', 2, '1', '1Gi', '1Gi',
                  node_selector={'karpenter.sh/nodepool': 'system'}))

    for namespace in NAMESPACES:
        for role in m.rng.sample(ROLES, m.rng.randint(4, 8)):
            m.deploy(_platform_workload(m, namespace, role))

    m.deploy(Workload('ml-features', 'gpu-embedder', 'Deployment', [
        Container('embedder', cpu='4', memory='16Gi', memory_limit='16Gi', extended={GPU: '1'})], replicas=3,
        tolerations=list(TOLERATE_GPU)))

    m.cordon(general[5])
    m.cordon(general[36])
    m.set_condition(general[12], 'Ready', False)
    m.set_condition(spot[7], 'DiskPressure', True)
    m.set_condition(spot[20], 'PIDPressure', True)
    return m


# Context order is the picker's order; the first one is the active context.
SCENARIOS: Tuple[Callable[[int], ClusterModel], ...] = (shop, ml, platform)


def build_all(seed: int = DEFAULT_SEED) -> List[ClusterModel]:
    return [build(seed) for build in SCENARIOS]
