"""`k8sfoams report`: the dashboard's JSON / CSV export without the browser, for CI and cron.

A port of app.jsx `toNode()` and export.jsx `reportJson()` / `podRows()` over FoamTreeMapper.combined(), so the
same cluster gives the same file either way (bar `generatedAt`). Change both sides together.
"""
import argparse
import json
import math
import sys
from datetime import datetime, timezone
from typing import Any, List, Optional, Tuple
from k8sfoam.src.common.node_status import WARNINGS
from k8sfoam.src.common.pod_audit import FINDINGS
from k8sfoam.src.k8s.k8s_client import K8sClient
from k8sfoam.src.synthetic.client import SyntheticRegistry
from k8sfoam.src.utils.mappers import FoamTreeMapper

# export.jsx CSV_COLUMNS: one extended-resource column per resource on the cluster goes after memory_mib.
CSV_COLUMNS = [
    'context', 'node', 'zone', 'region', 'node_pool', 'instance_type', 'capacity_type', 'namespace', 'pod', 'qos',
    'cpu_millicores', 'memory_mib', 'containers', 'init_containers', 'findings', 'node_warnings', 'matched',
]

# --fail-on tokens, spelled like the query bar's audit: and health: filters.
FAIL_ON = {'audit': FINDINGS, 'health': WARNINGS}


def kb_to_mib(kb: float) -> float:
    """Backend memory is decimal kB; the report is MiB, like app.jsx kbToMib."""
    return (kb * 1000) / (1024 * 1024)


def is_bytes(name: str) -> bool:
    return name == 'ephemeral-storage' or name.startswith('hugepages-')


def _ext(extended: Optional[dict]) -> dict:
    return {name: kb_to_mib(v) if is_bytes(name) else v for name, v in (extended or {}).items()}


def js_round(value: float) -> float:
    """Math.round: halves go up, unlike Python's round()."""
    return math.floor(value + 0.5)


def js_number(value: Any) -> Any:
    """Integral floats as ints, recursively, so the text reads like JSON.stringify / String() wrote it."""
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, dict):
        return {k: js_number(v) for k, v in value.items()}
    if isinstance(value, list):
        return [js_number(v) for v in value]
    return value


def to_nodes(combined: dict) -> list:
    """app.jsx toNodes(): MiB everywhere, pod requests summed per node in the same order (same floats)."""
    nodes = []
    for n in combined['nodes']:
        cpu_used, mem_used_kb = 0, 0
        ext_used: dict = {}
        pods = []
        for p in n['pods']:
            pod_ext = _ext(p.get('extended'))
            cpu_used += p['cpu'] or 0
            mem_used_kb += p['memory'] or 0
            for name, v in pod_ext.items():
                ext_used[name] = ext_used.get(name, 0) + v
            pods.append({
                'name': p['name'], 'namespace': p.get('namespace') or '', 'labels': p.get('labels') or {},
                'qos': p.get('qos') or '', 'findings': p.get('findings') or [], 'owner': p.get('owner') or None,
                'cpu': p['cpu'] or 0, 'mem': kb_to_mib(p['memory'] or 0), 'ext': pod_ext,
                'containers': [{'name': c['name'], 'init': bool(c['init']), 'cpu': c['cpu'] or 0,
                                'mem': kb_to_mib(c['memory'] or 0), 'ext': _ext(c.get('extended'))}
                               for c in p.get('containers') or []],
            })
        topo = n.get('topology') or {}
        nodes.append({
            'name': n['name'],
            'topology': {k: topo.get(k) or None for k in ('zone', 'region', 'instanceType', 'nodePool', 'capacityType')},
            'cpuCapacity': n['cpu'] or 0, 'memCapacity': kb_to_mib(n['memory'] or 0),
            'cpuUsed': cpu_used, 'memUsed': kb_to_mib(mem_used_kb),
            'extCap': _ext(n.get('extended')), 'extUsed': ext_used, 'pods': pods,
            'warnings': n.get('warnings') or [], 'taints': n.get('taints') or [],
            'conditions': n.get('conditions') or {}, 'unschedulable': bool(n.get('unschedulable')),
        })
    return nodes


def detect_resources(nodes: list) -> list:
    """resources.jsx detectResources(): extended resources some node has capacity for."""
    return sorted({name for n in nodes for name, cap in n['extCap'].items() if cap > 0})


def _ext_column(name: str) -> str:
    return f"{name.replace('-', '_')}_mib" if is_bytes(name) else name


def csv_columns(nodes: list) -> list:
    at = CSV_COLUMNS.index('memory_mib') + 1
    return [*CSV_COLUMNS[:at], *map(_ext_column, detect_resources(nodes)), *CSV_COLUMNS[at:]]


def pod_rows(nodes: list, context: str) -> list:
    """export.jsx podRows() with no query: `matched` stays blank."""
    extended = detect_resources(nodes)
    rows = []
    for n in nodes:
        topo = n['topology']
        for p in n['pods']:
            rows.append({
                'context': context, 'node': n['name'], 'zone': topo['zone'], 'region': topo['region'],
                'node_pool': topo['nodePool'], 'instance_type': topo['instanceType'],
                'capacity_type': topo['capacityType'], 'namespace': p['namespace'], 'pod': p['name'], 'qos': p['qos'],
                'cpu_millicores': js_round(p['cpu']), 'memory_mib': js_round(p['mem'] * 100) / 100,
                **{_ext_column(name): js_round((p['ext'].get(name) or 0) * 100) / 100 if is_bytes(name)
                   else p['ext'].get(name) or 0 for name in extended},
                'containers': ';'.join(c['name'] for c in p['containers'] if not c['init']),
                'init_containers': ';'.join(c['name'] for c in p['containers'] if c['init']),
                'findings': ';'.join(p['findings']), 'node_warnings': ';'.join(n['warnings']), 'matched': '',
            })
    return rows


def _csv_cell(value: Any) -> str:
    value = js_number(value)
    s = '' if value is None else 'true' if value is True else 'false' if value is False else str(value)
    return f'"{s.replace(chr(34), chr(34) * 2)}"' if any(ch in s for ch in '",\r\n') else s


def to_csv(rows: list, columns: list) -> str:
    lines = [','.join(map(_csv_cell, columns))]
    lines += [','.join(_csv_cell(r.get(c)) for c in columns) for r in rows]
    return '\r\n'.join(lines) + '\r\n'


def totals(nodes: list) -> dict:
    t: dict = {'cpuCap': 0, 'cpuUsed': 0, 'memCap': 0, 'memUsed': 0, 'pods': 0, 'nodes': len(nodes),
               'extCap': {}, 'extUsed': {}}
    for n in nodes:
        t['cpuCap'] += n['cpuCapacity']
        t['cpuUsed'] += n['cpuUsed']
        t['memCap'] += n['memCapacity']
        t['memUsed'] += n['memUsed']
        t['pods'] += len(n['pods'])
        for key in ('extCap', 'extUsed'):
            for name, v in n[key].items():
                t[key][name] = t[key].get(name, 0) + v
    return t


def report_json(nodes: list, context: str) -> str:
    """export.jsx reportJson() with the UI's defaults: CPU metric, no query, so no `matched`."""
    now = datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
    report = {
        'generatedAt': now, 'context': context, 'metric': 'cpu', 'query': '',
        'units': {'cpu': 'millicores', 'memory': 'MiB',
                  **{name: 'MiB' if is_bytes(name) else 'count' for name in detect_resources(nodes)}},
        'totals': totals(nodes),
        'nodes': [{
            'name': n['name'], 'topology': n['topology'],
            'cpuCapacity': n['cpuCapacity'], 'cpuUsed': n['cpuUsed'],
            'memCapacity': n['memCapacity'], 'memUsed': n['memUsed'],
            'extended': {'capacity': n['extCap'], 'used': n['extUsed']},
            'unschedulable': n['unschedulable'], 'warnings': n['warnings'], 'taints': n['taints'],
            'conditions': n['conditions'],
            'pods': [{'name': p['name'], 'namespace': p['namespace'], 'owner': p['owner'], 'qos': p['qos'],
                      'labels': p['labels'], 'cpu': p['cpu'], 'mem': p['mem'], 'extended': p['ext'],
                      'findings': p['findings'], 'containers': p['containers']} for p in n['pods']],
        } for n in nodes],
    }
    return json.dumps(js_number(report), indent=2, ensure_ascii=False)


def parse_fail_on(value: str) -> List[str]:
    """`audit:missing-requests,health:not-ready` -> lower-cased tokens. Raises ArgumentTypeError on an unknown one."""
    tokens = [t.strip().lower() for t in value.split(',') if t.strip()]
    for token in tokens:
        prefix, _, slug = token.partition(':')
        if slug not in FAIL_ON.get(prefix, ()):
            known = ', '.join(f'{p}:{s}' for p, slugs in FAIL_ON.items() for s in slugs)
            raise argparse.ArgumentTypeError(f'unknown policy {token!r}; use {known}')
    return tokens


def violations(nodes: list, tokens: List[str]) -> List[Tuple[str, str]]:
    """(token, offender) per pod (`namespace/name`) or node breaking a --fail-on policy."""
    found = []
    for token in tokens:
        prefix, _, slug = token.partition(':')
        for n in nodes:
            if prefix == 'health' and slug in n['warnings']:
                found.append((token, n['name']))
            if prefix == 'audit':
                found += [(token, f"{p['namespace']}/{p['name']}") for p in n['pods'] if slug in p['findings']]
    return found


def _cluster(args: argparse.Namespace) -> Tuple[Any, str]:
    """The cluster to report on and its context name; None picks the kubeconfig's current context."""
    if args.synthetic:
        registry = SyntheticRegistry(args.seed, 0)
        contexts = registry.contexts()
        client: Any = registry.get(args.context)
    else:
        client = K8sClient(args.context, timeout=args.timeout or None)
        contexts = [] if args.context else client.get_contexts()
    return client, args.context or next((c['context'] for c in contexts if c['active']), '')


def run_report(args: argparse.Namespace) -> int:
    """Write the report, then check --fail-on. Exit code: 0 ok, 1 a policy is violated, 2 an error."""
    try:
        client, context = _cluster(args)
        combined = FoamTreeMapper(client.get_node_resources(), [*client.get_pod_resources()]).combined()
        # /resources goes through Flask's jsonify, which sorts keys, and the browser export keeps that order.
        nodes = to_nodes(json.loads(json.dumps(combined, sort_keys=True)))
        body = report_json(nodes, context) + '\n' if args.format == 'json' else to_csv(pod_rows(nodes, context),
                                                                                      csv_columns(nodes))
        if args.output:
            with open(args.output, 'w', encoding='utf-8', newline='') as out:
                out.write(body)
        else:
            sys.stdout.write(body)
    except Exception as ex:
        print(f'k8sfoams report: {ex}', file=sys.stderr)
        return 2
    found = violations(nodes, args.fail_on or [])
    if found:
        print(f'k8sfoams report: {len(found)} policy violation(s) in {context}', file=sys.stderr)
        for token, offender in found:
            print(f'  {token}  {offender}', file=sys.stderr)
    return 1 if found else 0
