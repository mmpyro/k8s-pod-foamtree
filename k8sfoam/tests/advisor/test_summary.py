import copy
import json
import pytest
from k8sfoam.src.advisor.summary import MENTIONED, TOP_N, build_summary, unredact, unredact_stream
from k8sfoam.src.common.scheduler import simulate_consolidation, simulate_pending
from k8sfoam.src.pricing import PriceBook
from k8sfoam.src.synthetic.client import SyntheticRegistry
from k8sfoam.src.synthetic.scenarios import ML, PLATFORM, SHOP
from k8sfoam.src.utils.mappers import FoamTreeMapper

# The payload must stay this small however big the cluster grows.
MAX_PAYLOAD_CHARS = 24000
MARKER = 'leak-'


@pytest.fixture(scope='module')
def clusters():
    registry = SyntheticRegistry(churn_interval=0)
    yield {c: registry.get(c) for c in (SHOP, ML, PLATFORM)}
    registry.close()


@pytest.fixture(scope='module')
def combined(clusters):
    return {c: FoamTreeMapper(cl.get_node_resources(), list(cl.get_pod_resources()), PriceBook(live=False)).combined()
            for c, cl in clusters.items()}


@pytest.fixture(scope='module')
def simulations(clusters):
    """(pending, consolidation) of SHOP, the cluster with pending and gated pods."""
    shop = clusters[SHOP]
    nodes, pods = shop.get_node_resources(), list(shop.get_pod_resources())
    return simulate_pending(nodes, pods), simulate_consolidation(nodes, pods, shop.get_disruption_budgets())


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
    """Every node, pool, namespace, workload and pod name prefixed with MARKER, so a leak is easy to spot."""
    marked = copy.deepcopy(combined)
    for node in marked['nodes']:
        node['name'] = MARKER + node['name']
        if node['topology']['nodePool']:
            node['topology']['nodePool'] = MARKER + node['topology']['nodePool']
        for pod in node['pods']:
            pod['name'], pod['namespace'] = MARKER + pod['name'], MARKER + pod['namespace']
            if pod['owner']:
                pod['owner']['name'] = MARKER + pod['owner']['name']
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

    assert MARKER not in json.dumps(payload)
    assert MARKER in json.dumps(build_summary(cluster, False)[0])
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


def test_summary_reports_the_simulations(combined, simulations):
    pending, consolidation = simulations
    assert pending['pending'] and pending['gated'] and consolidation['plan']

    payload, _ = build_summary(combined[SHOP], False, pending, consolidation)

    assert payload['pending']['unschedulable'] == len(pending['pending'])
    assert payload['pending']['gated'] == len(pending['gated'])
    assert payload['pending']['podsByReason'] and payload['pending']['namespaces']
    plan = payload['consolidation']['plan']
    assert plan['nodes'] == len(consolidation['plan']) and plan['names'] == [s['node'] for s in consolidation['plan']][:TOP_N]
    assert isinstance(plan['hourlySaving'], float) and plan['cpu'] > 0


def test_simulations_and_mentions_are_redacted(combined, simulations):
    cluster = combined[SHOP]
    node = cluster['nodes'][0]
    text = f"What runs on {node['name']} and in {node['pods'][0]['namespace']}?"

    payload, mapping = build_summary(cluster, True, *simulations, mentioned=text)
    real, _ = build_summary(cluster, False, *simulations, mentioned=text)

    assert [d['kind'] for d in payload['mentioned']] == ['Node', 'Namespace']
    assert all(n.startswith('node-') for n in payload['consolidation']['plan']['names'])
    assert all(n['namespace'].startswith('ns-') for n in payload['pending']['namespaces'])
    restored = unredact(payload, mapping)
    restored['redacted'] = False
    assert restored == real


def test_a_namespace_only_pending_pods_use_gets_a_placeholder(combined):
    pending = {'pending': [{'pod': 'p', 'namespace': 'ghost', 'reasons': [{'slug': 'taint', 'nodes': 3}]}],
               'placements': [], 'gated': [{'pod': 'g', 'namespace': 'gated-only', 'gates': ['x']}]}

    payload, mapping = build_summary(combined[SHOP], True, pending)

    placeholder = payload['pending']['namespaces'][0]['namespace']
    assert mapping[placeholder] == 'ghost' and 'gated-only' in mapping.values()


def test_mentioned_names_get_details_in_order_capped(combined):
    cluster = combined[SHOP]
    pod = next(p for n in cluster['nodes'] for p in n['pods'] if p['owner'] and p['owner']['kind'] == 'Deployment')
    nodes = [n['name'] for n in cluster['nodes'][:3]]
    text = f"Is {pod['owner']['name']} ok? Also {nodes[0]}, {nodes[1]} and {nodes[2]}."

    redacted, _ = build_summary(cluster, True, mentioned=text)
    real, _ = build_summary(cluster, False, mentioned=f"Why is pod {pod['name']} Burstable?")

    assert len(redacted['mentioned']) == MENTIONED
    workload = redacted['mentioned'][0]
    assert workload['kind'] == 'Deployment' and workload['name'].startswith('workload-')
    assert all(c['name'].startswith('container-') for c in workload['podRequests']['containers'])
    assert [d['kind'] for d in redacted['mentioned'][1:]] == ['Node', 'Node']
    # A pod's name stands for its workload; without redaction, container names are real.
    assert real['mentioned'][0]['name'] == pod['owner']['name']
    assert [c['name'] for c in real['mentioned'][0]['podRequests']['containers']] == [c['name'] for c in pod['containers']]
    assert 'mentioned' not in build_summary(cluster, False, mentioned='nothing to see here')[0]


def test_scope_keeps_only_the_selected_pods_and_their_nodes(combined, simulations):
    cluster = combined[SHOP]
    namespace = cluster['nodes'][0]['pods'][0]['namespace']
    scope = [f"{p['namespace']}/{p['name']}" for n in cluster['nodes'] for p in n['pods'] if p['namespace'] == namespace]

    payload, _ = build_summary(cluster, False, *simulations, scope=scope)

    assert payload['scope'] == {'pods': len(scope), 'ofPods': sum(len(n['pods']) for n in cluster['nodes'])}
    assert [n['namespace'] for n in payload['namespaces']] == [namespace]
    assert payload['totals']['nodes'] == sum(1 for n in cluster['nodes'] if any(p['namespace'] == namespace for p in n['pods']))
    assert 'pending' not in payload and 'consolidation' not in payload and payload['totals']['pendingPods'] is None


def test_unredact_stream_never_splits_a_placeholder():
    mapping = {'ns-1': 'payments', 'workload-7': 'api'}
    deltas = ['Scale work', 'load-7 in n', 's-1', '. Done', '']

    out = list(unredact_stream(deltas, mapping))

    assert ''.join(out) == unredact(''.join(deltas), mapping) == 'Scale api in payments. Done'
    assert not any('work' in part or 'ns-' in part for part in out)
