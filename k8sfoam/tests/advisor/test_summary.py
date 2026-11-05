import copy
import json
import pytest
from k8sfoam.src.advisor.summary import TOP_N, build_summary, unredact
from k8sfoam.src.pricing import PriceBook
from k8sfoam.src.synthetic.client import SyntheticRegistry
from k8sfoam.src.synthetic.scenarios import ML, PLATFORM, SHOP
from k8sfoam.src.utils.mappers import FoamTreeMapper

# The payload must stay this small however big the cluster grows.
MAX_PAYLOAD_CHARS = 24000
SECRET = 'secret-'


@pytest.fixture(scope='module')
def combined():
    registry = SyntheticRegistry(churn_interval=0)
    try:
        clusters = {c: registry.get(c) for c in (SHOP, ML, PLATFORM)}
        return {c: FoamTreeMapper(cl.get_node_resources(), list(cl.get_pod_resources()), PriceBook(live=False)).combined()
                for c, cl in clusters.items()}
    finally:
        registry.close()


def _scaled(combined: dict, copies: int) -> dict:
    """The cluster `copies` times over, every node and namespace renamed, so nothing collapses."""
    nodes = []
    for i in range(copies):
        for node in copy.deepcopy(combined['nodes']):
            node['name'] = f"{node['name']}-{i}"
            for pod in node['pods']:
                pod['namespace'] = f"{pod['namespace']}-{i}"
            nodes.append(node)
    return {**combined, 'nodes': nodes}


def _marked(combined: dict) -> dict:
    """Every node, pool, namespace, workload and pod name prefixed with SECRET, so a leak is easy to spot."""
    marked = copy.deepcopy(combined)
    for node in marked['nodes']:
        node['name'] = SECRET + node['name']
        if node['topology']['nodePool']:
            node['topology']['nodePool'] = SECRET + node['topology']['nodePool']
        for pod in node['pods']:
            pod['name'], pod['namespace'] = SECRET + pod['name'], SECRET + pod['namespace']
            if pod['owner']:
                pod['owner']['name'] = SECRET + pod['owner']['name']
    return marked


@pytest.mark.parametrize('redact', [False, True])
def test_payload_is_bounded_on_a_5k_pod_cluster(combined, redact):
    big = _scaled(combined[PLATFORM], 4)
    assert sum(len(n['pods']) for n in big['nodes']) > 5000

    payload, _ = build_summary(big, redact)

    assert len(json.dumps(payload)) <= MAX_PAYLOAD_CHARS
    assert len(payload['namespaces']) == TOP_N
    assert payload['totals']['pods'] == sum(len(n['pods']) for n in big['nodes'])


def test_payload_is_deterministic(combined):
    shuffled = {**combined[SHOP], 'nodes': [{**n, 'pods': n['pods'][::-1]} for n in combined[SHOP]['nodes'][::-1]]}

    assert json.dumps(build_summary(combined[SHOP], True)) == json.dumps(build_summary(shuffled, True))


@pytest.mark.parametrize('context', [SHOP, ML, PLATFORM])
def test_redaction_round_trip(combined, context):
    cluster = _marked(combined[context])
    payload, mapping = build_summary(cluster, True)

    assert SECRET not in json.dumps(payload)
    assert SECRET in json.dumps(build_summary(cluster, False)[0])
    restored = unredact(payload, mapping)
    restored['redacted'] = False
    assert restored == build_summary(cluster, False)[0]


def test_unredact_restores_names_inside_free_text():
    mapping = {'ns-1': 'payments', 'workload-7': 'api'}

    answer = unredact({'title': 'Scale workload-7 in ns-1', 'affected': [{'namespace': 'ns-1', 'name': 'workload-7'}],
                       'other': 'ns-12 stays, node-3 unknown'}, mapping)

    assert answer == {'title': 'Scale api in payments', 'affected': [{'namespace': 'payments', 'name': 'api'}],
                      'other': 'ns-12 stays, node-3 unknown'}


def test_summary_reports_findings_devices_and_warnings(combined):
    payload, _ = build_summary(combined[ML], False)

    assert 'nvidia.com/gpu' in payload['devices']
    gpus = payload['devices']['nvidia.com/gpu']
    assert sum(gpus['nodesByFreeUnits'].values()) == gpus['nodes']
    assert payload['findings'] and all(f['top'] for f in payload['findings'].values())
    assert set(payload['qos']['pods']) <= {'Guaranteed', 'Burstable', 'BestEffort'}
