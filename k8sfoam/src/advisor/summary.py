"""The advisor's prompt payload: a compact, deterministic, size-bounded summary of FoamTreeMapper.combined().

Everything is aggregated per pool / zone / namespace / workload and every list is capped at TOP_N,
so a cluster of any size gives a payload of about the same size. Sorting is total, so the same
cluster always gives the same bytes (the answer cache keys on them).
"""
import re
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Tuple
from k8sfoam.src.common.resources import is_device

TOP_N = 10
UNITS = {'cpu': 'cores', 'memory': 'GiB', 'hourlyCost': 'USD'}
PLACEHOLDER = re.compile(r'\b(?:node|ns|workload|pool)-\d+\b')


class _Names():
    """Real names, or stable placeholders (`ns-1`, `workload-7`) numbered in sorted order."""

    def __init__(self, combined: dict, redact: bool):
        self.redact = redact
        self.mapping: Dict[str, str] = {}
        self.__ids: Dict[Tuple[str, Any], str] = {}
        nodes = combined['nodes']
        pods = [pod for node in nodes for pod in node['pods']]
        for prefix, keys in (('node', {n['name'] for n in nodes}),
                             ('pool', {n['topology']['nodePool'] for n in nodes} - {None}),
                             ('ns', {p['namespace'] for p in pods}),
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


def build_summary(combined: dict, redact: bool) -> Tuple[dict, Dict[str, str]]:
    """(payload, mapping) for one context; mapping turns placeholders back into real names and stays server side.

    Labels are never sent, redacted or not.
    """
    names = _Names(combined, redact)
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


def redact_text(text: str, mapping: Dict[str, str]) -> str:
    """`text` with every real name of `mapping` swapped for its placeholder; the inverse of unredact.

    Longest name first, and only whole names: `api` inside `payments-api` stays; a full stop may end a name.
    """
    placeholders = {real: placeholder for placeholder, real in sorted(mapping.items(), reverse=True)}
    if not placeholders:
        return text
    names = '|'.join(re.escape(n) for n in sorted(placeholders, key=len, reverse=True))
    return re.sub(rf'(?<![\w.-])(?:{names})(?![\w-]|\.\w)', lambda m: placeholders[m.group(0)], text)
