import bitmath  # type: ignore
import pytest
from collections import Counter
from k8sfoam.src.common.dtos import DisruptionBudget, NodeResources, PodResources
from k8sfoam.src.common.scheduler import (UnknownTemplate, filter_node, free_capacity, hypothetical_nodes,
                                          simulate_consolidation, simulate_drain, simulate_fit, simulate_pending,
                                          simulate_replicas,
                                          tolerates, fmt_memory)


GI = float(bitmath.GiB(1).kB)


def node(name, cpu=4000, memory=16 * GI, **kw):
    return NodeResources(name, cpu, memory, **kw)


def pod(name, node_name=None, cpu=0, memory=0.0, owner_kind='ReplicaSet', **kw):
    return PodResources(name, node_name, cpu, memory, [], [], 'default', owner_kind=owner_kind, **kw)


def slugs(reasons):
    return [r['slug'] for r in reasons]


def test_free_capacity_subtracts_bound_pods_from_allocatable():
    # Given
    nodes = [node('a', cpu=3800, memory=15 * GI, allocatable_pods=110)]
    pods = [pod('p1', 'a', 1000, 2 * GI), pod('p2', 'a', 500, GI), pod('pending', None, 9999, GI)]

    # When
    free = free_capacity(nodes, pods)

    # Then
    assert free['a'] == {'cpu': 2300, 'memory': 12 * GI, 'pods': 108, 'extended': {}}


def test_free_capacity_without_pod_limit_skips_the_pod_count():
    # When
    free = free_capacity([node('a')], [])

    # Then
    assert free['a'] == {'cpu': 4000, 'memory': 16 * GI, 'pods': None, 'extended': {}}


def test_pod_fits_on_a_healthy_node_with_room():
    # Given
    n = node('a')

    # When
    reasons = filter_node(pod('new', cpu=1000, memory=GI), n, free_capacity([n], [])['a'])

    # Then
    assert reasons == []


def test_insufficient_cpu_reports_required_and_available():
    # Given
    n = node('a')
    free = free_capacity([n], [pod('busy', 'a', 2800)])['a']

    # When
    reasons = filter_node(pod('new', cpu=4000), n, free)

    # Then
    assert reasons == [{'slug': 'insufficient-cpu', 'message': 'Insufficient CPU: requires 4000m, available 1200m'}]


def test_insufficient_memory_reports_required_and_available():
    # Given
    n = node('a', memory=8 * GI)

    # When
    reasons = filter_node(pod('new', memory=16 * GI), n, free_capacity([n], [])['a'])

    # Then
    assert reasons == [{'slug': 'insufficient-memory',
                        'message': 'Insufficient memory: requires 16.0Gi, available 8.0Gi'}]


def test_insufficient_pods_when_pod_limit_is_reached():
    # Given
    n = node('a', allocatable_pods=1)

    # When
    reasons = filter_node(pod('new'), n, free_capacity([n], [pod('only', 'a')])['a'])

    # Then
    assert slugs(reasons) == ['insufficient-pods']


def test_cordoned_and_not_ready_nodes_are_filtered():
    # Given
    n = node('a', unschedulable=True, conditions={'Ready': False})

    # When
    reasons = filter_node(pod('new'), n, free_capacity([n], [])['a'])

    # Then
    assert slugs(reasons) == ['cordoned', 'not-ready']


def test_pod_tolerating_lifecycle_taints_still_fits_cordoned_not_ready_node():
    # Given — what every DaemonSet pod carries
    n = node('a', unschedulable=True, conditions={'Ready': False})
    tolerations = [{'key': 'node.kubernetes.io/unschedulable', 'operator': 'Exists', 'effect': 'NoSchedule'},
                   {'key': 'node.kubernetes.io/not-ready', 'operator': 'Exists'}]

    # When
    reasons = filter_node(pod('ds', tolerations=tolerations), n, free_capacity([n], [])['a'])

    # Then
    assert reasons == []


def test_node_selector_mismatch_and_missing_label():
    # Given
    n = node('a', labels={'zone': 'b'})

    # When
    reasons = filter_node(pod('new', node_selector={'zone': 'a', 'gpu': 'true'}), n, free_capacity([n], [])['a'])

    # Then
    assert [r['message'] for r in reasons] == [
        'Node selector mismatch: requires zone=a, node has zone=b',
        'Node selector mismatch: requires gpu=true, node has no gpu label',
    ]


def test_node_selector_match_fits():
    # Given
    n = node('a', labels={'zone': 'a', 'kubernetes.io/os': 'linux'})

    # When
    reasons = filter_node(pod('new', node_selector={'zone': 'a'}), n, free_capacity([n], [])['a'])

    # Then
    assert reasons == []


def expr(key, operator, *values):
    return {'key': key, 'operator': operator, 'values': list(values)}


def term(*expressions, fields=()):
    return {'matchExpressions': list(expressions), 'matchFields': list(fields)}


@pytest.mark.parametrize('requirement, expected', [
    (expr('zone', 'In', 'a', 'b'), True),
    (expr('zone', 'In', 'c'), False),
    (expr('zone', 'NotIn', 'c'), True),
    (expr('zone', 'NotIn', 'a'), False),
    (expr('spot', 'NotIn', 'true'), True),
    (expr('zone', 'Exists'), True),
    (expr('spot', 'Exists'), False),
    (expr('spot', 'DoesNotExist'), True),
    (expr('zone', 'DoesNotExist'), False),
    (expr('cores', 'Gt', '4'), True),
    (expr('cores', 'Gt', '8'), False),
    (expr('cores', 'Lt', '16'), True),
    (expr('cores', 'Lt', '8'), False),
    (expr('zone', 'Gt', '1'), False),
    (expr('spot', 'Lt', '1'), False),
])
def test_node_affinity_operators(requirement, expected):
    # Given
    n = node('a', labels={'zone': 'a', 'cores': '8'})

    # When
    reasons = filter_node(pod('new', node_affinity=[term(requirement)]), n, free_capacity([n], [])['a'])

    # Then
    assert (reasons == []) is expected


def test_node_affinity_ands_expressions_within_a_term():
    # Given
    n = node('a', labels={'zone': 'a', 'disk': 'hdd'})
    affinity = [term(expr('zone', 'In', 'a'), expr('disk', 'In', 'ssd'))]

    # When
    reasons = filter_node(pod('new', node_affinity=affinity), n, free_capacity([n], [])['a'])

    # Then
    assert reasons == [{'slug': 'node-affinity',
                        'message': 'Node affinity mismatch: requires zone In (a), disk In (ssd)'}]


def test_node_affinity_ors_terms():
    # Given
    nodes = [node('a', labels={'zone': 'a'}), node('b', labels={'zone': 'b'}), node('c', labels={'zone': 'c'})]
    affinity = [term(expr('zone', 'In', 'a')), term(expr('zone', 'In', 'b'))]

    # When
    result = simulate_fit(pod('new', node_affinity=affinity), nodes, [])

    # Then
    verdicts = {r['node']: r for r in result['nodes']}
    assert result['fits'] == 2
    assert verdicts['c']['reasons'] == [{'slug': 'node-affinity',
                                         'message': 'Node affinity mismatch: requires zone In (a) or zone In (b)'}]


def test_node_affinity_match_fields_on_the_node_name():
    # Given
    nodes = [node('a'), node('b')]
    affinity = [term(fields=[expr('metadata.name', 'In', 'b')])]

    # When
    result = simulate_fit(pod('new', node_affinity=affinity), nodes, [])

    # Then
    assert [r['node'] for r in result['nodes'] if r['fits']] == ['b']


def test_node_affinity_is_checked_right_after_the_node_selector():
    # Given
    n = node('a', labels={'zone': 'b'}, taints=[{'key': 'dedicated', 'value': 'gpu', 'effect': 'NoSchedule'}])
    p = pod('new', node_selector={'zone': 'a'}, node_affinity=[term(expr('zone', 'In', 'a'))])

    # When
    reasons = filter_node(p, n, free_capacity([n], [])['a'])

    # Then
    assert slugs(reasons) == ['node-selector', 'node-affinity', 'taint']


def test_simulate_drain_respects_node_affinity_of_evicted_pods():
    # Given — c has the most room, but the pod may only run in zone a
    nodes = [node('a1', labels={'zone': 'a'}), node('a2', cpu=2000, labels={'zone': 'a'}),
             node('c', cpu=16000, labels={'zone': 'c'})]
    pods = [pod('web', 'a1', 1000, node_affinity=[term(expr('zone', 'In', 'a'))]),
            pod('db', 'a1', 3000, node_affinity=[term(expr('zone', 'In', 'a'))])]

    # When
    result = simulate_drain('a1', nodes, pods)

    # Then
    assert [(p['pod'], p['to']) for p in result['placements']] == [('web', 'a2')]
    assert result['pending'][0]['pod'] == 'db'
    assert result['pending'][0]['reasons'] == [{'slug': 'node-affinity', 'nodes': 1},
                                               {'slug': 'insufficient-cpu', 'nodes': 1}]


HOST = 'kubernetes.io/hostname'
ZONE = 'topology.kubernetes.io/zone'


def host(name, zone=None, **kw):
    labels = {HOST: name, **({ZONE: zone} if zone else {})}
    return node(name, labels=labels, **kw)


def pod_term(topology_key, namespaces=(), namespace_selector=None, **match_labels):
    return {'labelSelector': {'matchLabels': match_labels, 'matchExpressions': []},
            'namespaces': list(namespaces), 'namespaceSelector': namespace_selector, 'topologyKey': topology_key}


def test_hostname_anti_affinity_forbids_a_second_replica_on_a_node():
    # Given
    nodes = [host('a'), host('b'), host('c')]
    anti = [pod_term(HOST, app='web')]
    pods = [replica('web-1', 'a', 100, labels={'app': 'web'}, pod_anti_affinity=anti)]

    # When
    result = replicas(3, nodes, pods)

    # Then — one copy per free node, then nowhere is left
    assert sorted(result['placements']) == ['b', 'c']
    assert result['pending'] == [{'slug': 'pod-anti-affinity', 'nodes': 3}]
    assert by_node(result)['a']['reasons'] == [{
        'slug': 'pod-anti-affinity',
        'message': f'Pod anti-affinity: default/web-1 matches app=web in the same {HOST}'}]


def test_zone_pod_affinity_needs_a_matching_pod_in_the_zone():
    # Given
    nodes = [host('a1', 'a'), host('a2', 'a'), host('b1', 'b'), host('bare')]
    pods = [pod('cache', 'a1', labels={'app': 'cache'})]
    web = pod('web', labels={'app': 'web'}, pod_affinity=[pod_term(ZONE, app='cache')])

    # When
    result = simulate_fit(web, nodes, pods)

    # Then
    verdicts = by_node(result)
    assert [n for n, v in verdicts.items() if v['fits']] == ['a1', 'a2']
    assert verdicts['b1']['reasons'] == [{'slug': 'pod-affinity',
                                          'message': f'Pod affinity: no pod matching app=cache in the same {ZONE}'}]
    assert verdicts['bare']['reasons'] == [{'slug': 'pod-affinity', 'message': f'Pod affinity: node has no {ZONE} label'}]


def test_first_pod_of_a_self_affine_group_may_go_anywhere_with_the_topology():
    # Given — nothing matches yet, and the pod matches its own term
    nodes = [host('a1', 'a'), host('bare')]
    web = pod('web', labels={'app': 'web'}, pod_affinity=[pod_term(ZONE, app='web')])

    # When
    result = simulate_fit(web, nodes, [])

    # Then
    assert [n for n, v in by_node(result).items() if v['fits']] == ['a1']


def test_existing_pods_anti_affinity_rejects_the_incoming_pod():
    # Given — the incoming pod has no terms of its own
    nodes = [host('a', 'z1'), host('b', 'z1'), host('c', 'z2')]
    pods = [pod('db', 'a', labels={'app': 'db'}, pod_anti_affinity=[pod_term(ZONE, app='web')])]

    # When
    result = simulate_fit(pod('web', labels={'app': 'web'}), nodes, pods)

    # Then
    verdicts = by_node(result)
    assert [n for n, v in verdicts.items() if v['fits']] == ['c']
    assert verdicts['b']['reasons'] == [{'slug': 'pod-anti-affinity',
                                         'message': f'Pod anti-affinity: default/db repels it in the same {ZONE}'}]


def test_pod_terms_are_scoped_to_namespaces():
    # Given — a matching pod in another namespace
    nodes = [host('a'), host('b')]
    other = PodResources('web-x', 'a', 0, 0.0, [], [], 'other', labels={'app': 'web'}, owner_kind='ReplicaSet')

    # When
    own_namespace = simulate_fit(pod('web', pod_anti_affinity=[pod_term(HOST, app='web')]), nodes, [other])
    listed = simulate_fit(pod('web', pod_anti_affinity=[pod_term(HOST, ['other'], app='web')]), nodes, [other])
    every = simulate_fit(pod('web', pod_anti_affinity=[pod_term(HOST, namespace_selector={}, app='web')]),
                         nodes, [other])

    # Then
    assert own_namespace['fits'] == 2
    assert listed['fits'] == 1
    assert every['fits'] == 1


def test_simulate_drain_keeps_anti_affine_pods_apart_as_it_places_them():
    # Given — two evicted replicas that must not share a node
    nodes = [host('a'), host('b', cpu=8000), host('c')]
    anti = [pod_term(HOST, app='web')]
    pods = [pod('web-1', 'a', 1000, labels={'app': 'web'}, pod_anti_affinity=anti),
            pod('web-2', 'a', 900, labels={'app': 'web'}, pod_anti_affinity=anti)]

    # When
    result = simulate_drain('a', nodes, pods)

    # Then — b has room for both, but takes only the first
    assert [(p['pod'], p['to']) for p in result['placements']] == [('web-1', 'b'), ('web-2', 'c')]


def test_inter_pod_affinity_on_a_5k_pod_cluster_runs_in_reasonable_time():
    # Given — 100 nodes in 3 zones, 50 workloads with one hostname-anti-affine replica per node
    import time
    nodes = [host(f'n{i}', f'z{i % 3}', cpu=64000, memory=256 * GI) for i in range(100)]
    pods = [pod(f'app{a}-{i}', f'n{i}', 100, GI, labels={'app': f'app{a}'},
                pod_anti_affinity=[pod_term(HOST, app=f'app{a}')],
                pod_affinity=[pod_term(ZONE, app=f'app{a}')])
            for i in range(100) for a in range(50)]
    assert len(pods) == 5000
    started = time.monotonic()

    # When
    fit = simulate_fit(pod('new', 100, labels={'app': 'app0'}, pod_anti_affinity=[pod_term(HOST, app='app0')]),
                       nodes, pods)
    drain = simulate_drain('n0', nodes, pods)

    # Then
    assert time.monotonic() - started < 10
    assert fit['fits'] == 0
    assert len(drain['pending']) == 50
    assert drain['pending'][0]['reasons'] == [{'slug': 'pod-anti-affinity', 'nodes': 99}]


def spread(max_skew=1, key=ZONE, min_domains=None, node_affinity_policy='Honor', node_taints_policy='Ignore',
           **match_labels):
    return {'maxSkew': max_skew, 'topologyKey': key, 'minDomains': min_domains,
            'labelSelector': {'matchLabels': match_labels or {'app': 'web'}, 'matchExpressions': []},
            'nodeAffinityPolicy': node_affinity_policy, 'nodeTaintsPolicy': node_taints_policy}


def web(name, node_name=None, constraints=None, **kw):
    return pod(name, node_name, labels={'app': 'web'}, topology_spread=constraints or [spread()], **kw)


def test_topology_spread_with_max_skew_1_across_three_zones():
    # Given
    nodes = [host('a1', 'a'), host('b1', 'b'), host('c1', 'c')]
    pods = [web('web-1', 'a1'), web('web-2', 'b1')]

    # When
    result = simulate_fit(web('new'), nodes, pods)

    # Then
    verdicts = by_node(result)
    assert [n for n, v in verdicts.items() if v['fits']] == ['c1']
    assert verdicts['a1']['reasons'] == [{
        'slug': 'topology-spread',
        'message': f'Topology spread: {ZONE}=a would have skew 2, over maxSkew 1 '
                   f'(1 matching pod there, 0 in the emptiest {ZONE})'}]


def test_topology_spread_rejects_nodes_without_the_topology_label_and_does_not_count_them():
    # Given — the bare node holds no zone, so it is no empty domain either
    nodes = [host('a1', 'a'), host('b1', 'b'), host('bare')]
    pods = [web('web-1', 'a1'), web('web-2', 'bare')]

    # When
    result = simulate_fit(web('new'), nodes, pods)

    # Then
    verdicts = by_node(result)
    assert [n for n, v in verdicts.items() if v['fits']] == ['b1']
    assert verdicts['bare']['reasons'] == [{'slug': 'topology-spread',
                                            'message': f'Topology spread: node has no {ZONE} label'}]


def test_topology_spread_min_domains_counts_missing_domains_as_empty():
    # Given — two zones, one pod each
    nodes = [host('a1', 'a'), host('b1', 'b')]
    pods = [web('web-1', 'a1'), web('web-2', 'b1')]

    # When
    enough = simulate_fit(web('new', constraints=[spread(min_domains=2)]), nodes, pods)
    too_few = simulate_fit(web('new', constraints=[spread(min_domains=3)]), nodes, pods)

    # Then
    assert enough['fits'] == 2
    assert too_few['fits'] == 0


def test_topology_spread_node_affinity_policy_decides_which_domains_count():
    # Given — the pod may only run in zones a and b; zone c is empty
    nodes = [host('a1', 'a'), host('b1', 'b'), host('c1', 'c')]
    pods = [web('web-1', 'a1'), web('web-2', 'b1')]
    affinity = [term(expr(ZONE, 'In', 'a', 'b'))]

    # When
    honour = simulate_fit(web('new', node_affinity=affinity), nodes, pods)
    ignore = simulate_fit(web('new', constraints=[spread(node_affinity_policy='Ignore')], node_affinity=affinity),
                          nodes, pods)

    # Then
    assert honour['fits'] == 2
    assert ignore['fits'] == 0


def test_topology_spread_node_taints_policy_honour_skips_tainted_domains():
    # Given — zone c only has a node the pod does not tolerate
    nodes = [host('a1', 'a'), host('b1', 'b'),
             host('c1', 'c', taints=[{'key': 'dedicated', 'value': 'gpu', 'effect': 'NoSchedule'}])]
    pods = [web('web-1', 'a1'), web('web-2', 'b1')]

    # When
    honour = simulate_fit(web('new', constraints=[spread(node_taints_policy='Honor')]), nodes, pods)
    ignore = simulate_fit(web('new'), nodes, pods)

    # Then
    assert honour['fits'] == 2
    assert ignore['fits'] == 0


def test_topology_spread_only_counts_matching_pods_in_the_namespace():
    # Given
    nodes = [host('a1', 'a'), host('b1', 'b')]
    other_ns = PodResources('web-x', 'a1', 0, 0.0, [], [], 'other', labels={'app': 'web'}, owner_kind='ReplicaSet')
    pods = [other_ns, pod('api', 'a1', labels={'app': 'api'})]

    # When
    result = simulate_fit(web('new'), nodes, pods)

    # Then
    assert result['fits'] == 2


def test_simulate_replicas_spread_copies_evenly_across_zones():
    # Given — zone a has the most room, but the copies must spread
    nodes = [host('a1', 'a', cpu=16000), host('a2', 'a', cpu=16000), host('b1', 'b'), host('c1', 'c')]
    pods = [replica('web-1', 'a1', 100, labels={'app': 'web'}, topology_spread=[spread()])]

    # When
    result = replicas(5, nodes, pods)

    # Then — six pods in all, two per zone
    zone_of = {'a1': 'a', 'a2': 'a', 'b1': 'b', 'c1': 'c'}
    assert Counter(zone_of[n] for n in ['a1', *result['placements']]) == {'a': 2, 'b': 2, 'c': 2}


def budget(name, allowed, namespace='default', **match_labels):
    return DisruptionBudget(name, namespace, {'matchLabels': match_labels, 'matchExpressions': []}, allowed)


def test_simulate_drain_blocks_pods_beyond_the_disruption_budget():
    # Given — the budget allows one eviction, two matching pods sit on the node
    nodes = [node('a'), node('b')]
    pods = [pod('db-0', 'a', 1000, labels={'app': 'db'}), pod('db-1', 'a', 500, labels={'app': 'db'}),
            pod('web', 'a', 100, labels={'app': 'web'})]

    # When
    result = simulate_drain('a', nodes, pods, [budget('db-pdb', 1, app='db')])

    # Then — the bigger pod goes first and uses the budget up
    assert result['pdbsChecked'] is True
    assert [p['pod'] for p in result['placements']] == ['db-0', 'web']
    assert result['blocked'] == [{'pod': 'db-1', 'namespace': 'default', 'pdb': 'db-pdb', 'disruptionsAllowed': 1}]
    assert result['fits'] is True


def test_simulate_drain_without_a_matching_budget_evicts_everything():
    # Given — a budget for another namespace and one for other labels
    nodes = [node('a'), node('b')]
    pods = [pod('db-0', 'a', labels={'app': 'db'})]
    pdbs = [budget('other-ns', 0, namespace='other', app='db'), budget('web-pdb', 0, app='web')]

    # When
    with_budgets = simulate_drain('a', nodes, pods, pdbs)
    none = simulate_drain('a', nodes, pods, [])

    # Then
    for result in (with_budgets, none):
        assert result['blocked'] == []
        assert result['pdbsChecked'] is True
        assert [p['pod'] for p in result['placements']] == ['db-0']


def test_simulate_drain_reports_pdbs_not_checked_when_they_could_not_be_read():
    # When
    result = simulate_drain('a', [node('a'), node('b')], [pod('db-0', 'a', labels={'app': 'db'})], None)

    # Then
    assert result['pdbsChecked'] is False
    assert result['blocked'] == []


def test_simulate_consolidation_blocks_a_node_with_pods_held_by_a_budget():
    # Given
    nodes = [node('a'), node('b')]
    pods = [pod('db-0', 'a', 100, labels={'app': 'db'})]

    # When
    result = simulate_consolidation(nodes, pods, [budget('db-pdb', 0, app='db')])

    # Then
    verdicts = by_node(result)
    assert verdicts['a']['removable'] is False
    assert verdicts['a']['blockers'] == [{'slug': 'pdb', 'pods': 1}]
    assert [step['node'] for step in result['plan']] == ['b']
    assert result['pdbsChecked'] is True


def test_untolerated_blocking_taint_is_reported_and_prefer_no_schedule_ignored():
    # Given
    n = node('a', taints=[{'key': 'dedicated', 'value': 'gpu', 'effect': 'NoSchedule'},
                          {'key': 'soft', 'value': None, 'effect': 'PreferNoSchedule'}])

    # When
    reasons = filter_node(pod('new'), n, free_capacity([n], [])['a'])

    # Then
    assert reasons == [{'slug': 'taint', 'message': 'Untolerated taint: dedicated=gpu:NoSchedule'}]


@pytest.mark.parametrize('toleration, expected', [
    ({'key': 'dedicated', 'operator': 'Equal', 'value': 'gpu', 'effect': 'NoSchedule'}, True),
    ({'key': 'dedicated', 'operator': 'Equal', 'value': 'cpu', 'effect': 'NoSchedule'}, False),
    ({'key': 'dedicated', 'value': 'gpu'}, True),
    ({'key': 'dedicated', 'operator': 'Exists'}, True),
    ({'key': 'dedicated', 'operator': 'Exists', 'effect': 'NoExecute'}, False),
    ({'key': 'other', 'operator': 'Exists'}, False),
    ({'operator': 'Exists'}, True),
])
def test_toleration_matching(toleration, expected):
    # Given
    taint = {'key': 'dedicated', 'value': 'gpu', 'effect': 'NoSchedule'}

    # Then
    assert tolerates(toleration, taint) is expected


def test_fmt_memory_switches_to_mebibytes_below_one_gibibyte():
    assert fmt_memory(float(bitmath.MiB(512).kB)) == '512Mi'
    assert fmt_memory(-5.0) == '0Mi'


def test_simulate_fit_returns_a_verdict_per_node():
    # Given
    nodes = [node('big', cpu=8000), node('small', cpu=1000), node('cordoned', cpu=8000, unschedulable=True)]

    # When
    result = simulate_fit(pod('new', cpu=4000), nodes, [])

    # Then
    assert result['fits'] == 1
    assert result['total'] == 3
    verdicts = {r['node']: r for r in result['nodes']}
    assert verdicts['big']['fits'] is True
    assert slugs(verdicts['small']['reasons']) == ['insufficient-cpu']
    assert slugs(verdicts['cordoned']['reasons']) == ['cordoned']
    assert verdicts['big']['free'] == {'cpu': 8000, 'memory': 16 * GI, 'pods': None, 'extended': {}}


def test_simulate_drain_unknown_node_returns_none():
    assert simulate_drain('ghost', [node('a')], []) is None


def test_simulate_drain_places_all_pods_when_there_is_room():
    # Given
    nodes = [node('a'), node('b'), node('c')]
    pods = [pod('web-1', 'a', 1000, GI), pod('web-2', 'a', 1000, GI), pod('db', 'b', 3000, 8 * GI)]

    # When
    result = simulate_drain('a', nodes, pods)

    # Then
    assert result['fits'] is True
    assert result['pending'] == []
    assert result['remainingNodes'] == 2
    # The emptier node c wins first; the second replica then goes where most room is left.
    assert [(p['pod'], p['to']) for p in result['placements']] == [('web-1', 'c'), ('web-2', 'c')]


def test_simulate_drain_reports_pods_that_would_go_pending():
    # Given
    nodes = [node('a'), node('b', cpu=2000), node('c', cpu=2000, unschedulable=True)]
    pods = [pod('big', 'a', 3000), pod('small', 'a', 1500)]

    # When
    result = simulate_drain('a', nodes, pods)

    # Then
    assert result['fits'] is False
    assert [p['pod'] for p in result['placements']] == ['small']
    assert result['pending'][0]['pod'] == 'big'
    assert result['pending'][0]['reasons'] == [{'slug': 'cordoned', 'nodes': 1}, {'slug': 'insufficient-cpu', 'nodes': 2}]


def test_simulate_drain_places_biggest_pods_first():
    # Given — only one of the two can move, and it must be the big one
    nodes = [node('a'), node('b', cpu=2000)]
    pods = [pod('small', 'a', 500), pod('big', 'a', 2000)]

    # When
    result = simulate_drain('a', nodes, pods)

    # Then
    assert [p['pod'] for p in result['placements']] == ['big']
    assert [p['pod'] for p in result['pending']] == ['small']


def test_simulate_drain_skips_daemonset_and_static_pods_and_flags_naked_pods():
    # Given
    nodes = [node('a'), node('b')]
    pods = [pod('fluentd', 'a', 100, owner_kind='DaemonSet'),
            pod('kube-proxy', 'a', 100, owner_kind='Node'),
            pod('debug', 'a', 100, owner_kind=None),
            pod('web', 'a', 100)]

    # When
    result = simulate_drain('a', nodes, pods)

    # Then
    assert [(s['pod'], s['reason']) for s in result['skipped']] == [('fluentd', 'daemonset'), ('kube-proxy', 'static')]
    assert {p['pod']: p['unmanaged'] for p in result['placements']} == {'debug': True, 'web': False}


def test_simulate_drain_on_single_node_cluster_leaves_everything_pending():
    # When
    result = simulate_drain('a', [node('a')], [pod('web', 'a', 100)])

    # Then
    assert result['fits'] is False
    assert result['remainingNodes'] == 0
    assert result['pending'][0]['reasons'] == []


GPU = 'nvidia.com/gpu'


def test_free_capacity_subtracts_extended_requests():
    # Given
    nodes = [node('gpu', extended={GPU: 4, 'ephemeral-storage': 100 * GI})]
    pods = [pod('train', 'gpu', extended={GPU: 3, 'ephemeral-storage': 30 * GI})]

    # When
    free = free_capacity(nodes, pods)

    # Then
    assert free['gpu']['extended'] == {GPU: 1, 'ephemeral-storage': 70 * GI}


def test_insufficient_gpu_reports_required_and_available():
    # Given
    n = node('gpu', extended={GPU: 4})
    free = free_capacity([n], [pod('train', 'gpu', extended={GPU: 4})])['gpu']

    # When
    reasons = filter_node(pod('new', extended={GPU: 1}), n, free)

    # Then
    assert reasons == [{'slug': 'insufficient-extended', 'message': f'Insufficient {GPU}: requires 1, available 0'}]


def test_node_without_the_extended_resource_has_none_available():
    # Given
    n = node('cpu-only')

    # When
    reasons = filter_node(pod('new', extended={GPU: 1}), n, free_capacity([n], [])['cpu-only'])

    # Then
    assert [r['message'] for r in reasons] == [f'Insufficient {GPU}: requires 1, available 0']


def test_byte_sized_extended_resource_is_formatted_in_binary_units():
    # Given
    n = node('a', extended={'ephemeral-storage': 5 * GI})

    # When
    reasons = filter_node(pod('new', extended={'ephemeral-storage': 20 * GI}), n, free_capacity([n], [])['a'])

    # Then
    assert [r['message'] for r in reasons] == ['Insufficient ephemeral-storage: requires 20.0Gi, available 5.0Gi']


def test_simulate_fit_sends_a_gpu_pod_only_to_the_gpu_node():
    # Given
    nodes = [node('cpu-1'), node('gpu-1', extended={GPU: 2})]

    # When
    result = simulate_fit(pod('train', extended={GPU: 1}), nodes, [])

    # Then
    assert result['fits'] == 1
    verdicts = {r['node']: r for r in result['nodes']}
    assert verdicts['gpu-1']['fits'] is True
    assert verdicts['gpu-1']['free']['extended'] == {GPU: 2}
    assert slugs(verdicts['cpu-1']['reasons']) == ['insufficient-extended']


def test_simulate_drain_leaves_gpu_pod_pending_without_another_gpu_node():
    # Given — plenty of CPU elsewhere, but no GPUs
    nodes = [node('gpu-1', extended={GPU: 1}), node('cpu-1', cpu=16000), node('cpu-2', cpu=16000)]
    pods = [pod('train', 'gpu-1', 1000, GI, extended={GPU: 1}), pod('web', 'gpu-1', 500)]

    # When
    result = simulate_drain('gpu-1', nodes, pods)

    # Then
    assert [p['pod'] for p in result['placements']] == ['web']
    assert result['pending'][0]['pod'] == 'train'
    assert result['pending'][0]['reasons'] == [{'slug': 'insufficient-extended', 'nodes': 2}]


def test_simulate_drain_consumes_gpus_as_it_places_pods():
    # Given — two GPU pods, one free GPU elsewhere
    nodes = [node('gpu-1', extended={GPU: 2}), node('gpu-2', extended={GPU: 1})]
    pods = [pod('train-a', 'gpu-1', 100, extended={GPU: 1}), pod('train-b', 'gpu-1', 50, extended={GPU: 1})]

    # When
    result = simulate_drain('gpu-1', nodes, pods)

    # Then
    assert [(p['pod'], p['to']) for p in result['placements']] == [('train-a', 'gpu-2')]
    assert [p['pod'] for p in result['pending']] == ['train-b']


def by_node(result):
    return {v['node']: v for v in result['nodes']}


def test_simulate_consolidation_plans_removals_least_utilised_first():
    # Given — every node could go alone, but the cluster runs out of room after two
    nodes = [node('a'), node('b'), node('c'), node('d')]
    pods = [pod('p-a', 'a', 500), pod('p-b', 'b', 600), pod('p-c', 'c', 700), pod('p-d', 'd', 3000)]

    # When
    result = simulate_consolidation(nodes, pods)

    # Then
    assert result['total'] == 4
    assert all(v['removable'] for v in result['nodes'])
    assert [step['node'] for step in result['plan']] == ['a', 'b']
    # a's pod lands on b, so b's drain carries it along.
    assert [(p['pod'], p['to']) for p in result['plan'][0]['placements']] == [('p-a', 'b')]
    assert sorted(p['pod'] for p in result['plan'][1]['placements']) == ['p-a', 'p-b']
    assert result['freed'] == {'nodes': 2, 'cpu': 8000, 'memory': 32 * GI, 'extended': {}}
    verdicts = by_node(result)
    assert [verdicts[n]['inPlan'] for n in 'abcd'] == [True, True, False, False]
    assert verdicts['c']['conflictWith'] == ['a', 'b']
    assert verdicts['a']['conflictWith'] is None


def test_simulate_consolidation_flags_nodes_removable_alone_but_not_together():
    # Given — any one node's pod fits in the others' headroom, two do not
    nodes = [node('a'), node('b'), node('c')]
    pods = [pod('p-a', 'a', 2000), pod('p-b', 'b', 2000), pod('p-c', 'c', 2000)]

    # When
    result = simulate_consolidation(nodes, pods)

    # Then
    verdicts = by_node(result)
    assert all(v['removable'] for v in verdicts.values())
    assert [step['node'] for step in result['plan']] == ['a']
    assert verdicts['b']['conflictWith'] == ['a']
    assert verdicts['c']['conflictWith'] == ['a']


def test_simulate_consolidation_blocks_a_node_running_a_naked_pod():
    # Given
    nodes = [node('a'), node('b')]
    pods = [pod('debug', 'a', 100, owner_kind=None)]

    # When
    result = simulate_consolidation(nodes, pods)

    # Then
    verdicts = by_node(result)
    assert verdicts['a']['removable'] is False
    assert verdicts['a']['blockers'] == [{'slug': 'unmanaged', 'pods': 1}]
    assert [step['node'] for step in result['plan']] == ['b']


def test_simulate_consolidation_blocks_a_node_whose_pods_would_go_pending():
    # Given
    nodes = [node('a'), node('b', cpu=2000)]
    pods = [pod('big', 'a', 3000)]

    # When
    result = simulate_consolidation(nodes, pods)

    # Then
    verdicts = by_node(result)
    assert verdicts['a']['blockers'] == [{'slug': 'pending', 'pods': 1}]
    assert verdicts['a']['evictable'] == 1
    assert verdicts['a']['cpu'] == 3000
    assert verdicts['b']['removable'] is True


def test_simulate_consolidation_blocks_control_plane_nodes():
    # Given — only static pods, which a drain leaves alone
    nodes = [node('cp', labels={'node-role.kubernetes.io/control-plane': ''}), node('worker')]
    pods = [pod('kube-apiserver', 'cp', 250, owner_kind='Node')]

    # When
    result = simulate_consolidation(nodes, pods)

    # Then
    assert by_node(result)['cp']['blockers'] == [{'slug': 'control-plane', 'pods': 0}]
    assert [step['node'] for step in result['plan']] == ['worker']


def test_simulate_consolidation_does_not_count_daemonset_and_static_pods():
    # Given
    nodes = [node('a'), node('b')]
    pods = [pod('fluentd', 'a', 100, owner_kind='DaemonSet'), pod('kube-proxy', 'a', 100, owner_kind='Node'),
            pod('web', 'b', 1000)]

    # When
    result = simulate_consolidation(nodes, pods)

    # Then
    verdicts = by_node(result)
    assert verdicts['a']['removable'] is True
    assert verdicts['a']['evictable'] == 0
    assert result['plan'][0]['node'] == 'a'
    assert [s['reason'] for s in result['plan'][0]['skipped']] == ['daemonset', 'static']


def test_simulate_consolidation_consumes_gpus_across_steps():
    # Given — gpu-3 has two free GPUs, one per training pod
    nodes = [node('gpu-1', extended={GPU: 1}), node('gpu-2', extended={GPU: 1}), node('gpu-3', extended={GPU: 2})]
    pods = [pod('train-a', 'gpu-1', 100, extended={GPU: 1}), pod('train-b', 'gpu-2', 100, extended={GPU: 1}),
            pod('web', 'gpu-3', 2000)]

    # When
    result = simulate_consolidation(nodes, pods)

    # Then
    assert [step['node'] for step in result['plan']] == ['gpu-1', 'gpu-2']
    assert [p['to'] for step in result['plan'] for p in step['placements']] == ['gpu-3', 'gpu-3']
    assert result['freed']['extended'] == {GPU: 2}
    assert by_node(result)['gpu-3']['conflictWith'] == ['gpu-1', 'gpu-2']


def test_simulate_consolidation_leaves_the_input_pods_bound_where_they_are():
    # Given
    nodes = [node('a'), node('b')]
    pods = [pod('web', 'a', 100)]

    # When
    simulate_consolidation(nodes, pods)

    # Then
    assert pods[0].node_name == 'a'


def test_simulate_consolidation_of_an_empty_cluster():
    assert simulate_consolidation([], [], []) == {
        'total': 0, 'nodes': [], 'plan': [], 'freed': {'nodes': 0, 'cpu': 0, 'memory': 0.0, 'extended': {}},
        'pdbsChecked': True}


def replica(name, node_name=None, cpu=0, memory=0.0, **kw):
    return pod(name, node_name, cpu, memory, owner_kind='Deployment', owner_name='web', **kw)


def replicas(count, nodes, pods):
    return simulate_replicas('default', 'Deployment', 'web', count, nodes, pods)


def test_simulate_replicas_places_each_copy_on_the_most_headroom():
    # Given
    nodes = [node('a', cpu=8000), node('b', cpu=6000)]
    pods = [replica('web-1', 'a', 1000)]

    # When
    result = replicas(3, nodes, pods)

    # Then — b is emptier at first; once it has taken a copy, a has more room left
    assert result['placements'] == ['b', 'a', 'b']
    assert result['fits'] is True
    assert (result['requested'], result['placed'], result['total']) == (3, 3, 2)
    assert result['pending'] == []
    assert {v['node']: v['replicas'] for v in result['nodes']} == {'a': 1, 'b': 2}
    assert result['workload'] == {'namespace': 'default', 'kind': 'Deployment', 'name': 'web'}


def test_simulate_replicas_stop_reports_a_scheduler_tally():
    # Given
    nodes = [node('a', cpu=2000), node('b', cpu=2000), node('c', cpu=8000, unschedulable=True)]
    pods = [replica('web-1', 'a', 1000)]

    # When
    result = replicas(4, nodes, pods)

    # Then
    assert result['fits'] is False
    assert result['placed'] == 3
    assert result['placements'] == ['b', 'b', 'a']
    assert result['pending'] == [{'slug': 'cordoned', 'nodes': 1}, {'slug': 'insufficient-cpu', 'nodes': 2}]
    assert slugs(by_node(result)['c']['reasons']) == ['cordoned']


def test_simulate_replicas_honour_node_selector_and_tolerations():
    # Given
    toleration = {'key': 'dedicated', 'operator': 'Equal', 'value': 'gpu', 'effect': 'NoSchedule'}
    nodes = [node('gpu', labels={'pool': 'gpu'}, taints=[{'key': 'dedicated', 'value': 'gpu', 'effect': 'NoSchedule'}]),
             node('tainted', labels={'pool': 'gpu'}, taints=[{'key': 'other', 'value': None, 'effect': 'NoSchedule'}]),
             node('general')]
    pods = [replica('web-1', 'gpu', 100, node_selector={'pool': 'gpu'}, tolerations=[toleration])]

    # When
    result = replicas(2, nodes, pods)

    # Then
    assert result['placements'] == ['gpu', 'gpu']
    assert result['template'] == {'pod': 'web-1', 'cpu': 100, 'memory': 0.0, 'extended': {},
                                  'nodeSelector': {'pool': 'gpu'}, 'tolerations': [toleration], 'nodeAffinity': [],
                                  'podAffinity': [], 'podAntiAffinity': [], 'topologySpread': []}
    verdicts = by_node(result)
    assert slugs(verdicts['tainted']['reasons']) == ['taint']
    assert slugs(verdicts['general']['reasons']) == ['node-selector']


def test_simulate_replicas_consumes_gpus_across_copies():
    # Given — one free GPU on each node
    nodes = [node('g1', extended={GPU: 2}), node('g2', extended={GPU: 1})]
    pods = [replica('web-1', 'g1', 100, extended={GPU: 1})]

    # When
    result = replicas(3, nodes, pods)

    # Then
    assert result['placements'] == ['g2', 'g1']
    assert result['pending'] == [{'slug': 'insufficient-extended', 'nodes': 2}]


def test_simulate_replicas_template_is_the_first_pod_by_name():
    # Given — a Pending replica has the same spec, so it counts too
    pods = [replica('web-b', 'a', 2000), replica('web-a', None, 1000)]

    # When
    result = replicas(1, [node('a')], pods)

    # Then
    assert result['template']['pod'] == 'web-a'
    assert result['template']['cpu'] == 1000


def test_simulate_replicas_is_prefix_stable():
    # Given
    nodes = [node('a', cpu=8000, memory=32 * GI), node('b', cpu=6000), node('c', cpu=3000, memory=8 * GI)]
    pods = [replica('web-1', 'a', 700, 1.5 * GI), pod('db', 'b', 2500, 6 * GI)]

    # When
    full = replicas(100, nodes, pods)

    # Then
    assert full['fits'] is False
    for count in (1, 5, full['placed'], full['placed'] + 1):
        assert replicas(count, nodes, pods)['placements'] == full['placements'][:count]


def test_simulate_replicas_unknown_workload_returns_none():
    # Given
    pods = [replica('web-1', 'a', 100), pod('api-1', 'a', 100, owner_name='api')]

    # Then
    assert simulate_replicas('default', 'Deployment', 'api', 1, [node('a')], pods) is None
    assert simulate_replicas('other', 'Deployment', 'web', 1, [node('a')], pods) is None
    assert simulate_replicas('default', 'StatefulSet', 'web', 1, [node('a')], pods) is None


def test_simulate_replicas_leaves_the_input_pods_untouched():
    # Given
    pods = [replica('web-1', 'a', 100)]
    before = list(pods)

    # When
    replicas(3, [node('a'), node('b')], pods)

    # Then
    assert pods == before


INSTANCE_TYPE = 'node.kubernetes.io/instance-type'
NO_GPU = {'key': 'dedicated', 'value': 'gpu', 'effect': 'NoSchedule'}


def test_hypothetical_template_copies_allocatable_labels_and_taints_with_a_fresh_hostname():
    # Given
    template = node('gpu-1', cpu=8000, memory=32 * GI, extended={GPU: 4}, allocatable_pods=58,
                    labels={HOST: 'gpu-1', ZONE: 'a', INSTANCE_TYPE: 'g5.xlarge'}, taints=[NO_GPU],
                    unschedulable=True, conditions={'Ready': False})

    # When
    made = hypothetical_nodes([{'template': 'gpu-1', 'count': 2}], [template])

    # Then
    assert [n.name for n in made] == ['hypothetical-g5.xlarge-1', 'hypothetical-g5.xlarge-2']
    first = made[0]
    assert (first.cpu, first.memory, first.extended, first.allocatable_pods) == (8000, 32 * GI, {GPU: 4}, 58)
    assert first.labels == {HOST: 'hypothetical-g5.xlarge-1', ZONE: 'a', INSTANCE_TYPE: 'g5.xlarge'}
    assert first.taints == [NO_GPU]
    # A fresh copy is Ready and schedulable, whatever state the template is in.
    assert first.unschedulable is False
    assert first.conditions == {'Ready': True}


def test_hypothetical_template_drops_the_taints_a_fresh_node_would_not_carry():
    # Given
    taints = [NO_GPU, {'key': 'node.kubernetes.io/not-ready', 'value': None, 'effect': 'NoExecute'},
              {'key': 'ToBeDeletedByClusterAutoscaler', 'value': '1700000000', 'effect': 'NoSchedule'}]

    # When
    made = hypothetical_nodes([{'template': 'a', 'count': 1}], [node('a', taints=taints)])

    # Then
    assert made[0].taints == [NO_GPU]


def test_hypothetical_custom_shape_without_an_instance_type_is_named_custom():
    # Given
    shape = NodeResources('', 2000, 4 * GI, labels={'pool': 'spare'}, allocatable_pods=110)

    # When
    made = hypothetical_nodes([{'shape': shape, 'count': 2}, {'shape': shape, 'count': 1}], [])

    # Then
    assert [n.name for n in made] == ['hypothetical-custom-1', 'hypothetical-custom-2', 'hypothetical-custom-3']
    assert made[2].labels == {'pool': 'spare', HOST: 'hypothetical-custom-3'}


def test_hypothetical_unknown_template_raises():
    with pytest.raises(UnknownTemplate):
        hypothetical_nodes([{'template': 'ghost', 'count': 1}], [node('a')])


def test_simulate_fit_with_extra_nodes_marks_and_describes_them():
    # Given — the only real node is full
    nodes = [node('a', cpu=1000, labels={INSTANCE_TYPE: 'm5.large'})]

    # When
    result = simulate_fit(pod('new', cpu=2000), nodes, [], [{'shape': node('', cpu=4000), 'count': 1}])

    # Then
    assert (result['fits'], result['total']) == (1, 2)
    verdicts = {r['node']: r for r in result['nodes']}
    assert verdicts['a']['hypothetical'] is False
    assert verdicts['hypothetical-custom-1']['fits'] is True
    assert verdicts['hypothetical-custom-1']['hypothetical'] is True
    described = result['hypotheticalNodes'][0]
    assert (described['name'], described['template'], described['cpu']) == ('hypothetical-custom-1', None, 4000)


def test_simulate_fit_honours_taints_on_hypothetical_nodes():
    # Given
    template = node('gpu-1', taints=[NO_GPU], labels={INSTANCE_TYPE: 'g5.xlarge'})
    tolerating = pod('trainer', cpu=100, tolerations=[{'key': 'dedicated', 'operator': 'Equal', 'value': 'gpu',
                                                       'effect': 'NoSchedule'}])

    # When
    plain = simulate_fit(pod('web', cpu=100), [template], [], [{'template': 'gpu-1', 'count': 1}])
    tolerated = simulate_fit(tolerating, [template], [], [{'template': 'gpu-1', 'count': 1}])

    # Then
    fake = 'hypothetical-g5.xlarge-1'
    assert slugs({r['node']: r for r in plain['nodes']}[fake]['reasons']) == ['taint']
    assert {r['node']: r for r in tolerated['nodes']}[fake]['fits'] is True
    assert tolerated['hypotheticalNodes'][0]['template'] == 'gpu-1'


def daemon(name, node_name, cpu, ds, **kw):
    return pod(name, node_name, cpu, owner_kind='DaemonSet', owner_name=ds, **kw)


def test_template_copies_run_the_template_daemonset_pods():
    # Given — the agent takes 500m of every node; the new pod needs all the rest
    nodes = [node('a', cpu=2000, labels={INSTANCE_TYPE: 'm5.large'}), node('b', cpu=2000)]
    pods = [daemon('agent-x1', 'a', 500, 'agent'), daemon('agent-x2', 'b', 500, 'agent'), pod('web', 'a', 1500)]

    # When
    result = simulate_fit(pod('new', cpu=1600), nodes, pods, [{'template': 'a', 'count': 1}])

    # Then
    fake = {r['node']: r for r in result['nodes']}['hypothetical-m5.large-1']
    assert slugs(fake['reasons']) == ['insufficient-cpu']
    assert fake['free']['cpu'] == 1500
    assert result['hypotheticalNodes'][0]['daemonPods'] == [
        {'pod': 'agent-hypothetical-m5.large-1', 'namespace': 'default', 'cpu': 500, 'memory': 0.0, 'extended': {}}]


def test_custom_shapes_run_every_daemonset_that_selects_and_tolerates_them():
    # Given
    linux_only = daemon('agent-1', 'a', 100, 'agent', node_selector={'os': 'linux'})
    gpu_plugin = daemon('plugin-1', 'gpu', 100, 'gpu-plugin', tolerations=[{'key': 'dedicated', 'operator': 'Exists'}])
    everywhere = daemon('proxy-1', 'a', 100, 'proxy', tolerations=[{'operator': 'Exists'}])
    extra = [{'shape': node('', labels={'os': 'linux'}, taints=[NO_GPU]), 'count': 1}]

    # When
    result = simulate_fit(pod('new'), [node('a', labels={'os': 'linux'}), node('gpu', taints=[NO_GPU])],
                          [linux_only, gpu_plugin, everywhere], extra)

    # Then — the node selector matches, but the taint keeps the agent off
    names = [p['pod'] for p in result['hypotheticalNodes'][0]['daemonPods']]
    assert names == ['gpu-plugin-hypothetical-custom-1', 'proxy-hypothetical-custom-1']


def test_simulate_drain_fits_only_after_adding_nodes():
    # Given
    nodes = [node('a', labels={INSTANCE_TYPE: 'm5.large'}), node('b', cpu=1000)]
    pods = [pod('big', 'a', 3000)]

    # When
    without = simulate_drain('a', nodes, pods)
    with_extra = simulate_drain('a', nodes, pods, extra=[{'template': 'a', 'count': 1}])

    # Then
    assert without['fits'] is False
    assert without['hypotheticalNodes'] == []
    assert with_extra['fits'] is True
    assert with_extra['remainingNodes'] == 2
    assert [(p['to'], p['hypothetical']) for p in with_extra['placements']] == [('hypothetical-m5.large-1', True)]


def test_simulate_pending_without_pending_pods_fits():
    # When
    result = simulate_pending([node('a')], [pod('web', 'a', 100)])

    # Then
    assert (result['fits'], result['total'], result['placements'], result['pending'], result['gated']) == \
        (True, 0, [], [], [])


def test_simulate_pending_places_the_biggest_pods_first_once_nodes_are_added():
    # Given — a is nearly full; two pending pods need a node each
    nodes = [node('a', cpu=4000, labels={INSTANCE_TYPE: 'm5.xlarge'})]
    pods = [pod('web', 'a', 3500), pod('small', None, 1000), pod('big', None, 3000)]

    # When
    now = simulate_pending(nodes, pods)
    grown = simulate_pending(nodes, pods, [{'template': 'a', 'count': 2}])

    # Then
    assert now['fits'] is False
    assert [p['pod'] for p in now['pending']] == ['big', 'small']
    assert now['pending'][0]['reasons'] == [{'slug': 'insufficient-cpu', 'nodes': 1}]
    assert grown['fits'] is True
    assert grown['total'] == 2
    assert grown['nodes'] == 3
    # big takes the first empty copy; small then goes where most room is left.
    assert [(p['pod'], p['to'], p['hypothetical']) for p in grown['placements']] == [
        ('big', 'hypothetical-m5.xlarge-2', True), ('small', 'hypothetical-m5.xlarge-1', True)]


def test_simulate_pending_reports_pods_that_never_fit_and_lists_gated_ones():
    # Given
    nodes = [node('a', labels={'kubernetes.io/arch': 'amd64'})]
    pods = [pod('arm', None, 100, node_selector={'kubernetes.io/arch': 'arm64'}),
            pod('queued', None, 100, scheduling_gates=['kueue.x-k8s.io/admission'])]

    # When
    result = simulate_pending(nodes, pods, [{'template': 'a', 'count': 3}])

    # Then
    assert result['fits'] is False
    assert [(p['pod'], p['reasons']) for p in result['pending']] == [('arm', [{'slug': 'node-selector', 'nodes': 4}])]
    assert result['gated'] == [{'pod': 'queued', 'namespace': 'default', 'gates': ['kueue.x-k8s.io/admission']}]
    assert result['total'] == 1
