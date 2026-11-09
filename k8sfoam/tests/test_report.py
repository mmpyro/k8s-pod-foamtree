import csv
import io
import json
import re
import pytest
from pathlib import Path
from unittest.mock import patch
from k8sfoam.src.app import main
from k8sfoam.src.common.node_status import WARNINGS
from k8sfoam.src.common.pod_audit import FINDINGS
from k8sfoam.src.report import CSV_COLUMNS, csv_columns, js_round, kb_to_mib, to_csv, to_nodes, violations
from k8sfoam.src.synthetic.scenarios import ML, PLATFORM

ROOT = Path(__file__).parents[2]


def run(argv, capsys):
    """`k8sfoams *argv` -> (exit code, stdout, stderr)."""
    with patch('sys.argv', ['k8sfoams', *argv]):
        with pytest.raises(SystemExit) as exit_:
            main()
    out, err = capsys.readouterr()
    return exit_.value.code, out, err


def node(name='n1', warnings=(), pods=(), extended=None):
    return {'name': name, 'cpu': 4000, 'memory': 8_388_608, 'extended': extended or {}, 'warnings': list(warnings),
            'topology': {'zone': 'a'}, 'pods': list(pods)}


def pod(name='p1', findings=(), memory=1_048_576, extended=None):
    return {'name': name, 'namespace': 'ns', 'cpu': 250, 'memory': memory, 'extended': extended or {},
            'findings': list(findings), 'containers': [{'name': 'app', 'init': False, 'cpu': 250, 'memory': memory}]}


def test_report_csv_on_stdout(capsys):
    # Given / When
    code, out, err = run(['report', '--synthetic', '--format', 'csv'], capsys)

    # Then
    rows = list(csv.DictReader(io.StringIO(out)))
    assert code == 0 and err == ''
    assert len(rows) == 169
    assert {r['matched'] for r in rows} == {''}


def test_report_json_to_a_file(capsys, tmp_path):
    # Given
    target = tmp_path / 'report.json'

    # When
    code, out, _ = run(['report', '--synthetic', '--context', PLATFORM, '--output', str(target)], capsys)

    # Then
    report = json.loads(target.read_text())
    assert code == 0 and out == ''
    assert [*report] == ['generatedAt', 'context', 'metric', 'query', 'units', 'totals', 'nodes']
    assert report['context'] == PLATFORM
    assert report['units']['nvidia.com/gpu'] == 'count'
    assert report['totals']['pods'] == sum(len(n['pods']) for n in report['nodes'])


def test_report_fails_on_a_violated_policy(capsys):
    # Given / When — the shop cluster has one naked pod and no not-ready node
    code, _, err = run(['report', '--synthetic', '--fail-on', 'AUDIT:naked-pod, health:not-ready'], capsys)

    # Then
    assert code == 1
    assert 'audit:naked-pod  default/debug-shell' in err
    assert 'health:not-ready' not in err


def test_report_passes_when_no_pod_or_node_matches(capsys):
    # Given / When
    code, _, err = run(['report', '--synthetic', '--fail-on', 'health:not-ready'], capsys)

    # Then
    assert code == 0 and err == ''


@pytest.mark.parametrize('token', ['audit:nope', 'qos:burstable', 'missing-requests', 'health:'])
def test_report_rejects_an_unknown_policy(capsys, token):
    # Given / When
    code, out, err = run(['report', '--synthetic', '--fail-on', token], capsys)

    # Then
    assert code == 2 and out == ''
    assert 'unknown policy' in err


@patch('k8sfoam.src.report.K8sClient')
def test_report_exits_2_when_the_cluster_is_unreachable(k8s_client, capsys):
    # Given
    k8s_client.return_value.get_node_resources.side_effect = ConnectionError('connection refused')

    # When
    code, out, err = run(['report', '--context', 'prod', '--fail-on', 'audit:naked-pod'], capsys)

    # Then
    k8s_client.assert_called_once_with('prod')
    assert code == 2 and out == ''
    assert 'connection refused' in err


def test_report_exits_2_on_an_unknown_context(capsys):
    # Given / When
    code, _, err = run(['report', '--synthetic', '--context', 'prod'], capsys)

    # Then
    assert code == 2 and 'prod' in err


def test_report_uses_the_current_context_name(capsys):
    # Given / When
    with patch('k8sfoam.src.report.K8sClient') as k8s_client:
        k8s_client.return_value.get_contexts.return_value = [{'context': 'dev', 'active': False},
                                                             {'context': 'prod', 'active': True}]
        k8s_client.return_value.get_node_resources.return_value = []
        k8s_client.return_value.get_pod_resources.return_value = []
        code, out, _ = run(['report'], capsys)

    # Then
    k8s_client.assert_called_once_with(None)
    assert code == 0 and json.loads(out)['context'] == 'prod'


def test_js_round_rounds_halves_up_like_math_round():
    # Given / When / Then
    assert [js_round(v) for v in (0.5, 1.5, 2.5, -0.5, 2.4)] == [1, 2, 3, 0, 2]


def test_csv_quotes_like_the_browser_export():
    # Given
    rows = [{'a': 'x,y', 'b': 'say "hi"', 'c': None, 'd': 4096.0, 'e': 0.25}]

    # When
    text = to_csv(rows, ['a', 'b', 'c', 'd', 'e'])

    # Then
    assert text == 'a,b,c,d,e\r\n"x,y","say ""hi""",,4096,0.25\r\n'


def test_to_nodes_converts_to_mib_and_sums_pod_requests():
    # Given
    combined = {'nodes': [node(extended={'hugepages-2Mi': 2048, 'nvidia.com/gpu': 2}, pods=[
        pod('a', extended={'hugepages-2Mi': 1024, 'nvidia.com/gpu': 1}), pod('b')])]}

    # When
    [n] = to_nodes(combined)

    # Then
    assert n['memCapacity'] == kb_to_mib(8_388_608)
    assert n['cpuUsed'] == 500 and n['memUsed'] == kb_to_mib(2 * 1_048_576)
    assert n['extUsed'] == {'hugepages-2Mi': kb_to_mib(1024), 'nvidia.com/gpu': 1}
    assert n['topology'] == {'zone': 'a', 'region': None, 'instanceType': None, 'nodePool': None, 'capacityType': None}


def test_csv_columns_add_extended_resources_after_memory():
    # Given
    nodes = to_nodes({'nodes': [node(extended={'hugepages-2Mi': 2048, 'nvidia.com/gpu': 2, 'amd.com/gpu': 0})]})

    # When
    columns = csv_columns(nodes)

    # Then — a resource no node has capacity for gets no column
    at = columns.index('memory_mib')
    assert columns[at + 1:at + 3] == ['hugepages_2Mi_mib', 'nvidia.com/gpu']
    assert 'amd.com/gpu' not in columns


def test_ml_cluster_csv_has_a_gpu_column(capsys):
    # Given / When
    _, out, _ = run(['report', '--synthetic', '--context', ML, '--format', 'csv'], capsys)

    # Then
    assert 'nvidia.com/gpu' in out.splitlines()[0].split(',')


def test_violations_list_offending_pods_and_nodes():
    # Given
    nodes = to_nodes({'nodes': [node('n1', ['not-ready'], [pod('a', ['naked-pod']), pod('b')]),
                                node('n2', [], [pod('c', ['naked-pod', 'monolith'])])]})

    # When
    found = violations(nodes, ['audit:naked-pod', 'health:not-ready', 'health:cordoned'])

    # Then
    assert found == [('audit:naked-pod', 'ns/a'), ('audit:naked-pod', 'ns/c'), ('health:not-ready', 'n1')]


def test_csv_columns_match_the_browser_export_and_the_docs():
    # Given
    jsx = (ROOT / 'k8sfoam/src/frontend/export.jsx').read_text()
    js_columns = re.findall(r'"([a-z_]+)"', re.search(r'const CSV_COLUMNS = \[(.*?)\];', jsx, re.DOTALL).group(1))
    docs_row = next(line for line in (ROOT / 'docs/export.md').read_text().splitlines() if '**CSV report**' in line)
    documented = re.search(r'`(context,[a-z_,]+)`', docs_row).group(1).split(',')

    # When / Then
    assert CSV_COLUMNS == js_columns
    assert CSV_COLUMNS == documented


@pytest.mark.parametrize('slugs, jsx', [(FINDINGS, 'podaudit.jsx'), (WARNINGS, 'nodestatus.jsx')])
def test_fail_on_slugs_match_the_query_bar(slugs, jsx):
    # Given — the slugs audit: / health: accept in the query bar
    source = (ROOT / 'k8sfoam/src/frontend' / jsx).read_text()

    # When
    ui = re.findall(r'^\s*"([a-z-]+)":\s*\{\s*label:', source, re.MULTILINE)

    # Then
    assert list(slugs) == ui
