"""Write the synthetic clusters out as a static k8sfoams site, for the docs demo.

    python -m k8sfoam.src.synthetic.export docs/demo

The site is the regular frontend bundle switched to static mode: instead of the
HTTP API it reads JSON files holding what each endpoint would have answered.
Every drain and the consolidation plan are precomputed; the fit simulator offers
FIT_PRESETS only, since an arbitrary pod cannot be answered ahead of time. Each
workload's replica simulation is stored once with MAX_REPLICAS copies; the
frontend cuts it down to the count asked for.
"""
import argparse
import json
import re
import shutil
from pathlib import Path
from typing import Any, List
from k8sfoam.src.app import parse_pod_spec
from k8sfoam.src.common.scheduler import (DRAIN_SKIPPED_OWNERS, MAX_REPLICAS, simulate_consolidation, simulate_drain,
                                          simulate_fit, simulate_replicas)
from k8sfoam.src.synthetic.client import SyntheticRegistry
from k8sfoam.src.synthetic.scenarios import DEFAULT_SEED
from k8sfoam.src.utils.mappers import FoamTreeMapper

FRONTEND_DIR = Path(__file__).resolve().parent.parent / 'frontend'
ASSETS = ('bundle.js', 'styles.css')
BUNDLE_TAG = '<script src="bundle.js"></script>'
# Read by api.jsx: its presence switches the bundle from the HTTP API to these files.
STATIC_CONFIG = '<script>window.K8SFOAM_STATIC = {"data": "data/"};</script>'

# `spec` is the POST /simulate/fit body; `form` is the same pod as the fit
# modal's text fields, so picking a preset fills them in.
FIT_PRESETS: List[dict] = [
    {'id': 'small-service', 'label': 'Small service',
     'spec': {'cpu': '500m', 'memory': '1Gi'}},
    {'id': 'batch-job', 'label': 'Big batch job',
     'spec': {'cpu': '6', 'memory': '24Gi'}},
    {'id': 'in-memory-cache', 'label': 'In-memory cache',
     'spec': {'cpu': '2', 'memory': '48Gi'}},
    {'id': 'gpu-inference', 'label': 'GPU inference',
     'spec': {'cpu': '4', 'memory': '16Gi', 'extended': {'nvidia.com/gpu': '1'},
              'tolerations': [{'key': 'nvidia.com/gpu', 'operator': 'Exists', 'effect': 'NoSchedule'}]}},
    {'id': 'scratch-heavy', 'label': 'Scratch-heavy worker',
     'spec': {'cpu': '1', 'memory': '2Gi', 'extended': {'ephemeral-storage': '40Gi'}}},
    {'id': 'node-agent', 'label': 'Node agent (tolerates all)',
     'spec': {'cpu': '250m', 'memory': '256Mi', 'tolerations': [{'operator': 'Exists'}]}},
]


def _toleration_text(t: dict) -> str:
    """One toleration in the fit modal's syntax: `*`, `key`, `key:Effect` or `key=value:Effect`."""
    if not t.get('key'):
        return '*'
    text = t['key'] if t.get('operator') == 'Exists' else f"{t['key']}={t.get('value') or ''}"
    return f"{text}:{t['effect']}" if t.get('effect') else text


def _requirement_text(r: dict) -> str:
    """One node affinity expression in the fit modal's syntax: `key in (a,b)`, `key`, `!key` or `key gt N`."""
    operator, values = r['operator'], r.get('values') or []
    if operator in ('In', 'NotIn'):
        return f"{r['key']} {operator.lower()} ({','.join(values)})"
    if operator == 'Exists':
        return r['key']
    if operator == 'DoesNotExist':
        return f"!{r['key']}"
    return f"{r['key']} {operator.lower()} {values[0]}"


def preset_form(spec: dict) -> dict:
    """The fit modal's text fields for a preset spec. The modal holds a single node affinity term."""
    terms = spec.get('nodeAffinity') or []
    return {
        'cpu': spec.get('cpu', ''),
        'memory': spec.get('memory', ''),
        'extended': ', '.join(f'{k}={v}' for k, v in (spec.get('extended') or {}).items()),
        'nodeSelector': ', '.join(f'{k}={v}' for k, v in (spec.get('nodeSelector') or {}).items()),
        'nodeAffinity': ', '.join(_requirement_text(r) for r in terms[0]['matchExpressions']) if terms else '',
        'tolerations': ', '.join(_toleration_text(t) for t in spec.get('tolerations') or []),
    }


def context_path(context: str) -> str:
    """A context's directory under data/: its last path segment, made URL- and filename-safe."""
    return re.sub(r'[^A-Za-z0-9.-]+', '-', context.split('/')[-1]).strip('-')


def _write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, separators=(',', ':')), encoding='utf-8')


def static_index(frontend_dir: Path = FRONTEND_DIR) -> str:
    html = (frontend_dir / 'index.html').read_text(encoding='utf-8')
    if BUNDLE_TAG not in html:
        raise ValueError(f'{frontend_dir / "index.html"} does not load {BUNDLE_TAG}')
    html = html.replace('<title>k8sfoams — Cluster Topology</title>', '<title>k8sfoams — live demo</title>')
    return html.replace(BUNDLE_TAG, f'{STATIC_CONFIG}\n{BUNDLE_TAG}')


def export_data(data_dir: Path, seed: int = DEFAULT_SEED) -> None:
    """Every endpoint's answer for every synthetic context, as JSON files under `data_dir`."""
    registry = SyntheticRegistry(seed, churn_interval=0)
    contexts = registry.contexts()
    if data_dir.exists():
        shutil.rmtree(data_dir)
    _write(data_dir / 'contexts.json', [{**c, 'path': context_path(c['context'])} for c in contexts])
    _write(data_dir / 'fit-presets.json',
           [{'id': p['id'], 'label': p['label'], 'form': preset_form(p['spec'])} for p in FIT_PRESETS])
    for c in contexts:
        cluster = registry.get(c['context'])
        nodes, pods, pdbs = cluster.get_node_resources(), cluster.get_pod_resources(), cluster.get_disruption_budgets()
        out = data_dir / context_path(c['context'])
        _write(out / 'resources.json', FoamTreeMapper(iter(nodes), pods).combined())
        for node in nodes:
            _write(out / 'drain' / f'{node.name}.json', simulate_drain(node.name, nodes, pods, pdbs))
        _write(out / 'consolidation.json', simulate_consolidation(nodes, pods, pdbs))
        for preset in FIT_PRESETS:
            _write(out / 'fit' / f"{preset['id']}.json", simulate_fit(parse_pod_spec(preset['spec']), nodes, pods))
        workloads = {(p.namespace, p.owner_kind, p.owner_name) for p in pods
                     if p.owner_kind and p.owner_name and p.owner_kind not in DRAIN_SKIPPED_OWNERS}
        for namespace, kind, name in sorted(workloads):
            _write(out / 'replicas' / namespace / kind / f'{name}.json',
                   simulate_replicas(namespace, kind, name, MAX_REPLICAS, nodes, pods))


def export_site(out_dir: Path, seed: int = DEFAULT_SEED, frontend_dir: Path = FRONTEND_DIR) -> None:
    """A self-contained demo site: the frontend in static mode plus its data."""
    missing = [name for name in ASSETS if not (frontend_dir / name).is_file()]
    if missing:
        raise FileNotFoundError(f'{", ".join(missing)} missing from {frontend_dir}; run `make web` first')
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in ASSETS:
        shutil.copyfile(frontend_dir / name, out_dir / name)
    (out_dir / 'index.html').write_text(static_index(frontend_dir), encoding='utf-8')
    export_data(out_dir / 'data', seed)


def main() -> None:
    parser = argparse.ArgumentParser(description='Write the synthetic clusters out as a static k8sfoams demo site')
    parser.add_argument('out_dir', type=Path, help='Directory to write the site into')
    parser.add_argument('--seed', type=int, default=DEFAULT_SEED, help='Seed of the synthetic clusters')
    args = parser.parse_args()
    export_site(args.out_dir, args.seed)
    print(f'Demo site written to {args.out_dir}')


if __name__ == '__main__':
    main()
