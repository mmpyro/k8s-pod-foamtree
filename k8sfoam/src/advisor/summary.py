"""The advisor's prompt payload: a compact, deterministic, size-bounded summary of FoamTreeMapper.combined().

Everything is aggregated per pool / zone / namespace / workload and every list is capped at TOP_N,
so a cluster of any size gives a payload of about the same size. Sorting is total, so the same
cluster always gives the same bytes (the answer cache keys on them).
"""
import re
from collections import Counter, defaultdict
from typing import Any, Collection, Dict, Iterable, Iterator, List, Optional, Pattern, Tuple
from k8sfoam.src.common.resources import is_device

TOP_N = 10
MENTIONED = 3  # objects named in a question that get their own details
UNITS = {'cpu': 'cores', 'memory': 'GiB', 'hourlyCost': 'USD'}
PLACEHOLDER = re.compile(r'\b(?:node|ns|workload|pool)-\d+\b')


class _Names():
    """Real names, or stable placeholders (`ns-1`, `workload-7`) numbered in sorted order."""

    def __init__(self, combined: dict, redact: bool, namespaces: Collection[str] = ()):
        """`namespaces` adds those only pods off the map use (pending, gated)."""
        self.redact = redact
        self.mapping: Dict[str, str] = {}
        self.__ids: Dict[Tuple[str, Any], str] = {}
        nodes = combined['nodes']
        pods = [pod for node in nodes for pod in node['pods']]
        for prefix, keys in (('node', {n['name'] for n in nodes}),
                             ('pool', {n['topology']['nodePool'] for n in nodes} - {None}),
                             ('ns', {p['namespace'] for p in pods} | set(namespaces)),
                             ('workload', {_workload(p)[1:] for p in pods})):
            for i, key in enumerate(sorted(keys, key=str), 1):
                self.__ids[(prefix, key)] = f'{prefix}-{i}'
                if redact:  # every name, not only those in the payload: the assistant redacts free text with it
                    self.mapping[f'{prefix}-{i}'] = key[-1] if isinstance(key, tuple) else key

    def __call__(self, prefix: str, key: Any) -> Optional[str]:
        """The name to send for a node, pool, namespace or (kind, name) workload."""
        real = key[-1] if isinstance(key, tuple) else key
        if not self.redact or real is None:
            return real
        return self.__ids[(prefix, key)]


def _workload(pod: dict) -> Tuple[str, str, str]:
    """(namespace, kind, name) of the pod's controller; a naked pod is its own workload."""
    owner = pod.get('owner') or {'kind': 'Pod', 'name': pod['name']}
    return pod['namespace'], owner['kind'], owner['name']


def _cores(millicores: float) -> float:
    return round(millicores / 1000, 2)


def _gib(kb: float) -> float:
    return round(kb * 1000 / 2 ** 30, 1)


def _pct(part: float, whole: float) -> Optional[float]:
    return round(100 * part / whole, 1) if whole else None


def _allocation(nodes: List[dict]) -> dict:
    """Allocatable vs requested cpu and memory of some nodes, and their hourly cost."""
    out: dict = {}
    for resource, unit in (('cpu', _cores), ('memory', _gib)):
        allocatable = sum(n[resource] for n in nodes)
        requested = sum(p[resource] for n in nodes for p in n['pods'])
        out[resource] = {'allocatable': unit(allocatable), 'requested': unit(requested),
                         'requestedPct': _pct(requested, allocatable)}
    priced = [n['price']['hourly'] for n in nodes if n.get('price')]
    out['hourlyCost'] = round(sum(priced), 2) if priced else None
    out['unpricedNodes'] = len(nodes) - len(priced)
    return out


def _top(counter: Counter, n: int = TOP_N) -> List[Tuple[Any, int]]:
    """The n biggest entries, ties broken by key so the order never depends on input order."""
    return sorted(counter.items(), key=lambda kv: (-kv[1], str(kv[0])))[:n]


def _pools(nodes: List[dict], names: _Names) -> List[dict]:
    groups: Dict[Optional[str], List[dict]] = defaultdict(list)
    for node in nodes:
        groups[node['topology']['nodePool']].append(node)
    pools = []
    for pool, members in sorted(groups.items(), key=lambda kv: (-len(kv[1]), str(kv[0]))):
        def facts(key: str) -> List[str]:
            return sorted({str(n['topology'][key]) for n in members if n['topology'][key]})
        pools.append({'pool': names('pool', pool), 'nodes': len(members), 'instanceTypes': facts('instanceType'),
                      'capacityTypes': facts('capacityType'), 'zones': facts('zone'),
                      'warnings': dict(sorted(Counter(w for n in members for w in n['warnings']).items())),
                      **_allocation(members)})
    return pools[:TOP_N]


def _zones(nodes: List[dict]) -> List[dict]:
    groups: Dict[str, List[dict]] = defaultdict(list)
    for node in nodes:
        groups[node['topology']['zone'] or 'unknown'].append(node)
    return [{'zone': zone, 'nodes': len(members),
             **{r: v['requestedPct'] for r, v in _allocation(members).items() if r in ('cpu', 'memory')}}
            for zone, members in sorted(groups.items())][:TOP_N]


def _node_warnings(nodes: List[dict], names: _Names) -> Dict[str, dict]:
    by_warning: Dict[str, List[str]] = defaultdict(list)
    for node in nodes:
        for warning in node['warnings']:
            by_warning[warning].append(node['name'])
    return {w: {'count': len(ns), 'nodes': [names('node', n) for n in sorted(ns)[:TOP_N]]}
            for w, ns in sorted(by_warning.items())}


def _findings(pods: List[dict], names: _Names) -> Dict[str, dict]:
    by_finding: Dict[str, Counter] = defaultdict(Counter)
    for pod in pods:
        for finding in pod['findings']:
            by_finding[finding][_workload(pod)] += 1
    return {f: {'pods': sum(c.values()), 'workloads': len(c),
                'top': [{'namespace': names('ns', ns), 'kind': kind, 'name': names('workload', (kind, name)),
                         'pods': count} for (ns, kind, name), count in _top(c)]}
            for f, c in sorted(by_finding.items())}


def _qos(pods: List[dict], names: _Names) -> dict:
    risky: Dict[str, Counter] = defaultdict(Counter)
    for pod in pods:
        if pod['qos'] in ('BestEffort', 'Burstable'):
            risky[pod['namespace']][pod['qos']] += 1
    worst = sorted(risky.items(), key=lambda kv: (-kv[1]['BestEffort'], -kv[1]['Burstable'], kv[0]))[:TOP_N]
    return {'pods': dict(sorted(Counter(str(p['qos']) for p in pods).items())),
            'riskiestNamespaces': [{'namespace': names('ns', ns), 'BestEffort': c['BestEffort'], 'Burstable': c['Burstable']}
                                   for ns, c in worst]}


def _devices(nodes: List[dict]) -> Dict[str, dict]:
    """Per device resource: totals and how the free units are scattered (fragmentation)."""
    out = {}
    for resource in sorted({r for n in nodes for r in n['extended'] if is_device(r)}):
        holders = [n for n in nodes if n['extended'].get(resource)]
        free: Counter = Counter()
        requested = 0
        for node in holders:
            used = sum(p['extended'].get(resource, 0) for p in node['pods'])
            requested += used
            free[node['extended'][resource] - used] += 1
        out[resource] = {'nodes': len(holders), 'allocatable': sum(n['extended'][resource] for n in holders),
                         'requested': requested,
                         'nodesByFreeUnits': {str(k): v for k, v in sorted(free.items())}}
    return out


def _namespaces(pods: List[dict], names: _Names) -> List[dict]:
    groups: Dict[str, List[dict]] = defaultdict(list)
    for pod in pods:
        groups[pod['namespace']].append(pod)
    biggest = sorted(groups.items(), key=lambda kv: (-sum(p['cpu'] for p in kv[1]), kv[0]))[:TOP_N]
    return [{'namespace': names('ns', ns), 'pods': len(ps), 'workloads': len({_workload(p) for p in ps}),
             'cpu': _cores(sum(p['cpu'] for p in ps)), 'memory': _gib(sum(p['memory'] for p in ps))}
            for ns, ps in biggest]


def _pending(pending: dict, names: _Names) -> dict:
    """The pending simulation: how many waiting pods would fit now, and why the rest cannot, per namespace."""
    reasons: Counter = Counter()
    by_ns: Dict[str, Counter] = defaultdict(Counter)
    for pod in pending['pending']:
        slugs = {r['slug'] for r in pod['reasons']}
        reasons.update(slugs)
        by_ns[pod['namespace']].update(slugs)
    count = Counter(p['namespace'] for p in pending['pending'])
    return {'unschedulable': len(pending['pending']), 'wouldFit': len(pending['placements']),
            'gated': len(pending['gated']), 'podsByReason': dict(sorted(reasons.items())),
            'namespaces': [{'namespace': names('ns', ns), 'pods': c, 'podsByReason': dict(sorted(by_ns[ns].items()))}
                           for ns, c in _top(count)]}


def _consolidation(consolidation: dict, nodes: List[dict], names: _Names) -> dict:
    """The consolidation simulation: which nodes could be emptied together, what that frees, what blocks the rest."""
    prices = {n['name']: (n.get('price') or {}).get('hourly') for n in nodes}
    plan = [step['node'] for step in consolidation['plan']]
    saving: List[Optional[float]] = [prices.get(node) for node in plan]
    blockers = Counter(b['slug'] for v in consolidation['nodes'] for b in v['blockers'])
    return {'removableAlone': sum(1 for v in consolidation['nodes'] if v['removable']),
            'plan': {'nodes': len(plan), 'cpu': _cores(consolidation['freed']['cpu']),
                     'memory': _gib(consolidation['freed']['memory']),
                     'hourlySaving': None if None in saving else round(sum(p or 0 for p in saving), 2),
                     'names': [names('node', n) for n in plan[:TOP_N]]},
            'nodesByBlocker': dict(sorted(blockers.items())), 'pdbsChecked': consolidation['pdbsChecked']}


def _workloads(pods: List[dict], names: _Names) -> List[dict]:
    """The TOP_N workloads of some pods by requested CPU."""
    groups: Dict[Tuple[str, str, str], List[dict]] = defaultdict(list)
    for pod in pods:
        groups[_workload(pod)].append(pod)
    biggest = sorted(groups.items(), key=lambda kv: (-sum(p['cpu'] for p in kv[1]), kv[0]))[:TOP_N]
    return [{'namespace': names('ns', ns), 'kind': kind, 'name': names('workload', (kind, name)), 'pods': len(ps),
             'cpu': _cores(sum(p['cpu'] for p in ps)), 'memory': _gib(sum(p['memory'] for p in ps)),
             'findings': dict(sorted(Counter(f for p in ps for f in p['findings']).items()))}
            for (ns, kind, name), ps in biggest]


def _node_details(node: dict, names: _Names) -> dict:
    topology = node['topology']
    return {'kind': 'Node', 'name': names('node', node['name']), 'pool': names('pool', topology['nodePool']),
            'zone': topology['zone'], 'instanceType': topology['instanceType'], 'capacityType': topology['capacityType'],
            'warnings': list(node['warnings']),
            'taints': [f"{t.get('key')}:{t.get('effect')}" for t in node['taints']],
            **_allocation([node]), 'pods': len(node['pods']), 'topWorkloads': _workloads(node['pods'], names)}


def _namespace_details(namespace: str, pods: List[dict], names: _Names) -> dict:
    return {'kind': 'Namespace', 'name': names('ns', namespace), 'pods': len(pods),
            'qos': dict(sorted(Counter(str(p['qos']) for p in pods).items())),
            'findings': dict(sorted(Counter(f for p in pods for f in p['findings']).items())),
            'topWorkloads': _workloads(pods, names)}


def _workload_details(key: Tuple[str, str, str], placed: List[Tuple[dict, dict]], names: _Names) -> dict:
    """A workload's pods; container names only without redaction, as they can carry the same names."""
    ns, kind, name = key
    pods = [pod for pod, _ in placed]
    first = min(pods, key=lambda p: p['name'])
    return {'kind': kind, 'namespace': names('ns', ns), 'name': names('workload', (kind, name)), 'pods': len(pods),
            'nodes': sorted({names('node', node['name']) for _, node in placed}, key=str)[:TOP_N],
            'qos': dict(sorted(Counter(str(p['qos']) for p in pods).items())),
            'findings': dict(sorted(Counter(f for p in pods for f in p['findings']).items())),
            'podRequests': {'cpu': _cores(first['cpu']), 'memory': _gib(first['memory']),
                            'containers': [{'name': f'container-{i}' if names.redact else c['name'], 'init': c['init'],
                                            'cpu': _cores(c['cpu']), 'memory': _gib(c['memory'])}
                                           for i, c in enumerate(first.get('containers', []), 1)]}}


def _mentioned(text: str, combined: dict, names: _Names) -> List[dict]:
    """Details of up to MENTIONED nodes, namespaces and workloads named in `text`, in order of appearance.

    A pod's name stands for its workload.
    """
    nodes = combined['nodes']
    placed = [(pod, node) for node in nodes for pod in node['pods']]
    by_name: Dict[str, Dict[Tuple[str, Any], None]] = defaultdict(dict)  # an ordered set per name
    for node in nodes:
        by_name[node['name']][('node', node['name'])] = None
    for pod, _ in placed:
        by_name[pod['namespace']][('ns', pod['namespace'])] = None
        by_name[_workload(pod)[2]][('workload', _workload(pod))] = None
        by_name[pod['name']][('workload', _workload(pod))] = None
    if not text or not by_name:
        return []
    wanted = list(dict.fromkeys(obj for m in _whole_names(by_name).finditer(text) for obj in by_name[m.group(0)]))
    details = []
    for kind, key in wanted[:MENTIONED]:
        if kind == 'node':
            details.append(_node_details(next(n for n in nodes if n['name'] == key), names))
        elif kind == 'ns':
            details.append(_namespace_details(key, [p for p, _ in placed if p['namespace'] == key], names))
        else:
            details.append(_workload_details(key, [(p, n) for p, n in placed if _workload(p) == key], names))
    return details


def _scoped(combined: dict, scope: Collection[str]) -> dict:
    """Only the pods named "namespace/pod" in `scope`, and the nodes they run on."""
    wanted = set(scope)
    nodes = [{**n, 'pods': [p for p in n['pods'] if f"{p['namespace']}/{p['name']}" in wanted]} for n in combined['nodes']]
    return {**combined, 'nodes': [n for n in nodes if n['pods']], 'pendingPods': None}


def build_summary(combined: dict, redact: bool, pending: Optional[dict] = None, consolidation: Optional[dict] = None,
                  mentioned: str = '', scope: Optional[Collection[str]] = None) -> Tuple[dict, Dict[str, str]]:
    """(payload, mapping) for one context; mapping turns placeholders back into real names and stays server side.

    `pending` / `consolidation` are simulate_pending() / simulate_consolidation() of the same cluster.
    `mentioned` is free text whose node, namespace, workload and pod names get their own details.
    `scope` ("namespace/pod" names) narrows the summary to those pods; the cluster-wide simulations are then left out.
    Labels are never sent, redacted or not.
    """
    if scope is not None:
        total = sum(len(n['pods']) for n in combined['nodes'])
        combined, pending, consolidation = _scoped(combined, scope), None, None
    waiting = {p['namespace'] for p in pending['pending'] + pending['gated']} if pending else set()
    names = _Names(combined, redact, waiting)
    nodes = combined['nodes']
    pods = [pod for node in nodes for pod in node['pods']]
    payload = {
        'units': UNITS,
        'redacted': redact,
        'totals': {'nodes': len(nodes), 'pods': len(pods), 'pendingPods': combined.get('pendingPods', 0),
                   **_allocation(nodes)},
        'pools': _pools(nodes, names),
        'zones': _zones(nodes),
        'nodeWarnings': _node_warnings(nodes, names),
        'findings': _findings(pods, names),
        'qos': _qos(pods, names),
        'devices': _devices(nodes),
        'namespaces': _namespaces(pods, names),
    }
    if scope is not None:
        payload['scope'] = {'pods': len(pods), 'ofPods': total}
    if pending is not None:
        payload['pending'] = _pending(pending, names)
    if consolidation is not None:
        payload['consolidation'] = _consolidation(consolidation, nodes, names)
    details = _mentioned(mentioned, combined, names)
    if details:
        payload['mentioned'] = details
    return payload, names.mapping


def unredact(value: Any, mapping: Dict[str, str]) -> Any:
    """`value` with every placeholder in every string swapped back for its real name."""
    if isinstance(value, str):
        return PLACEHOLDER.sub(lambda m: mapping.get(m.group(0), m.group(0)), value)
    if isinstance(value, list):
        return [unredact(v, mapping) for v in value]
    if isinstance(value, dict):
        return {k: unredact(v, mapping) for k, v in value.items()}
    return value


def _whole_names(names: Iterable[str]) -> Pattern:
    """Any of `names` as a whole name, longest first: `api` inside `payments-api` does not match; a full stop may end one."""
    alternatives = '|'.join(re.escape(n) for n in sorted(names, key=len, reverse=True))
    return re.compile(rf'(?<![\w.-])(?:{alternatives})(?![\w-]|\.\w)')


def redact_text(text: str, mapping: Dict[str, str]) -> str:
    """`text` with every real name of `mapping` swapped for its placeholder; the inverse of unredact."""
    placeholders = {real: placeholder for placeholder, real in sorted(mapping.items(), reverse=True)}
    if not placeholders:
        return text
    return _whole_names(placeholders).sub(lambda m: placeholders[m.group(0)], text)


_WORD_TAIL = re.compile(r'[\w-]*\Z')


def unredact_stream(deltas: Iterable[str], mapping: Dict[str, str]) -> Iterator[str]:
    """unredact() over a streamed answer: a trailing run of name characters is held back until it ends,
    so a placeholder split over two deltas (`work` + `load-7`) is still swapped. Joined, equals unredact(whole)."""
    held = ''
    for delta in deltas:
        held += delta
        cut = _WORD_TAIL.search(held).start()  # type: ignore[union-attr]
        if cut:
            yield unredact(held[:cut], mapping)
            held = held[cut:]
    if held:
        yield unredact(held, mapping)
