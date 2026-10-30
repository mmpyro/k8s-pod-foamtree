import json
import pytest
from k8sfoam.src.common.scheduler import MAX_REPLICAS
from k8sfoam.src.synthetic.export import (BUNDLE_TAG, FIT_PRESETS, STATIC_CONFIG, context_path, export_site,
                                          preset_form, static_index)
from k8sfoam.src.synthetic.scenarios import ML, PLATFORM, SHOP

INDEX = '<html><head><title>k8sfoams — Cluster Topology</title></head><body>\n' + BUNDLE_TAG + '\n</body></html>'


@pytest.fixture
def frontend(tmp_path):
    src = tmp_path / 'frontend'
    src.mkdir()
    (src / 'index.html').write_text(INDEX, encoding='utf-8')
    (src / 'bundle.js').write_text('/* bundle */', encoding='utf-8')
    (src / 'styles.css').write_text('/* styles */', encoding='utf-8')
    return src


@pytest.fixture(scope='module')
def site(tmp_path_factory):
    src = tmp_path_factory.mktemp('frontend')
    (src / 'index.html').write_text(INDEX, encoding='utf-8')
    (src / 'bundle.js').write_text('/* bundle */', encoding='utf-8')
    (src / 'styles.css').write_text('/* styles */', encoding='utf-8')
    out = tmp_path_factory.mktemp('site')
    export_site(out, frontend_dir=src)
    return out


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def test_context_path_is_url_and_filename_safe():
    # Given / When / Then
    assert context_path(SHOP) == 'demo-shop'
    assert context_path(PLATFORM) == 'demo-platform'
    assert context_path(ML) == 'gke-demo-ml-us-central1-ml-training'


def test_preset_form_writes_the_fit_modal_syntax():
    # Given
    spec = {'cpu': '4', 'memory': '16Gi', 'extended': {'nvidia.com/gpu': '1'}, 'nodeSelector': {'zone': 'a'},
            'tolerations': [{'key': 'gpu', 'operator': 'Exists', 'effect': 'NoSchedule'},
                            {'key': 'dedicated', 'operator': 'Equal', 'value': 'ml'},
                            {'operator': 'Exists'}],
            'nodeAffinity': [{'matchExpressions': [{'key': 'zone', 'operator': 'In', 'values': ['a', 'b']},
                                                   {'key': 'spot', 'operator': 'DoesNotExist'},
                                                   {'key': 'cores', 'operator': 'Gt', 'values': ['4']}]}]}

    # When
    form = preset_form(spec)

    # Then
    assert form == {'cpu': '4', 'memory': '16Gi', 'extended': 'nvidia.com/gpu=1', 'nodeSelector': 'zone=a',
                    'nodeAffinity': 'zone in (a,b), !spot, cores gt 4',
                    'tolerations': 'gpu:NoSchedule, dedicated=ml, *'}


def test_static_index_switches_the_bundle_to_static_mode(frontend):
    # Given / When
    html = static_index(frontend)

    # Then — the config is set before the bundle reads it
    assert html.index(STATIC_CONFIG) < html.index(BUNDLE_TAG)
    assert '<title>k8sfoams — live demo</title>' in html


def test_static_index_refuses_a_page_without_the_bundle(frontend):
    # Given
    (frontend / 'index.html').write_text('<html></html>', encoding='utf-8')

    # When / Then
    with pytest.raises(ValueError):
        static_index(frontend)


def test_export_needs_the_built_ui(frontend, tmp_path):
    # Given
    (frontend / 'bundle.js').unlink()

    # When / Then
    with pytest.raises(FileNotFoundError, match='make web'):
        export_site(tmp_path / 'out', frontend_dir=frontend)


def test_export_writes_the_dashboard_and_contexts(site):
    # Given / When
    contexts = read(site / 'data' / 'contexts.json')

    # Then
    assert (site / 'bundle.js').read_text(encoding='utf-8') == '/* bundle */'
    assert STATIC_CONFIG in (site / 'index.html').read_text(encoding='utf-8')
    assert [c['path'] for c in contexts] == ['demo-shop', 'gke-demo-ml-us-central1-ml-training', 'demo-platform']
    assert contexts[0] == {'context': SHOP, 'active': True, 'path': 'demo-shop'}


@pytest.mark.parametrize('path', ['demo-shop', 'gke-demo-ml-us-central1-ml-training', 'demo-platform'])
def test_export_precomputes_every_drain_preset_and_consolidation(site, path):
    # Given
    data = site / 'data' / path

    # When
    resources = read(data / 'resources.json')

    # Then
    for node in resources['nodes']:
        assert read(data / 'drain' / f"{node['name']}.json")['node'] == node['name']
    for preset in FIT_PRESETS:
        assert read(data / 'fit' / f"{preset['id']}.json")['total'] == len(resources['nodes'])
    consolidation = read(data / 'consolidation.json')
    assert [v['node'] for v in consolidation['nodes']] == [n['name'] for n in resources['nodes']]
    pending = read(data / 'pending.json')
    assert pending['total'] == resources['pendingPods']
    assert pending['hypotheticalNodes'] == []
    workloads = {(p['namespace'], p['owner']['kind'], p['owner']['name'])
                 for n in resources['nodes'] for p in n['pods']
                 if p['owner'] and p['owner']['name'] and p['owner']['kind'] not in ('DaemonSet', 'Node')}
    assert workloads
    for namespace, kind, name in workloads:
        result = read(data / 'replicas' / namespace / kind / f'{name}.json')
        assert result['requested'] == MAX_REPLICAS
        assert result['workload'] == {'namespace': namespace, 'kind': kind, 'name': name}
    assert not (data / 'replicas' / 'kube-system' / 'DaemonSet').exists()


def test_export_precomputes_checkout_api_replicas(site):
    # Given / When
    result = read(site / 'data' / 'demo-shop' / 'replicas' / 'checkout' / 'Deployment' / 'checkout-api.json')

    # Then
    assert result['template']['pod'].startswith('checkout-api-')
    assert result['total'] == 16


def test_export_lists_presets_with_their_form(site):
    # Given / When
    presets = read(site / 'data' / 'fit-presets.json')

    # Then
    assert [p['id'] for p in presets] == [p['id'] for p in FIT_PRESETS]
    assert presets[0]['form'] == {'cpu': '500m', 'memory': '1Gi', 'extended': '', 'nodeSelector': '',
                                  'nodeAffinity': '', 'tolerations': ''}


def test_export_replaces_stale_data(frontend, tmp_path):
    # Given
    out = tmp_path / 'out'
    stale = out / 'data' / 'old-context' / 'resources.json'
    stale.parent.mkdir(parents=True)
    stale.write_text('{}', encoding='utf-8')

    # When
    export_site(out, frontend_dir=frontend)

    # Then
    assert not stale.exists()
