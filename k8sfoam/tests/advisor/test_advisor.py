import json
import openai
import pytest
from typing import Optional
from unittest.mock import MagicMock, patch
from k8sfoam.src.advisor import AdvisorConfig, AdvisorError, ask, parse_recommendations
from k8sfoam.src.app import create_app
from k8sfoam.src.synthetic.client import SyntheticRegistry
from k8sfoam.src.synthetic.scenarios import ML, SHOP

CONFIG = AdvisorConfig('sk-test', 'http://llm.local/v1', 'test-model')
# Mocks, not httpx objects: openai 1.x is built on httpx, 3.x on httpx2.
REQUEST = MagicMock()


def _response(status: int) -> MagicMock:
    return MagicMock(status_code=status, headers={}, request=REQUEST)


def _recommendation(severity='medium', title='Set limits', affected=None):
    return {'title': title, 'severity': severity, 'category': 'requests/limits', 'rationale': 'why',
            'affected': affected or [], 'suggestedChange': 'kubectl set resources ...'}


USAGE = {'prompt': 1200, 'completion': 300, 'total': 1500}


def _usage(usage: Optional[dict]) -> Optional[MagicMock]:
    return MagicMock(**{f'{k}_tokens': v for k, v in usage.items()}) if usage else None


def _completion(content: str, usage: Optional[dict] = None) -> MagicMock:
    completion = MagicMock()
    completion.choices = [MagicMock()]
    completion.choices[0].message.content = content
    completion.usage = _usage(usage)
    return completion


@pytest.fixture
def openai_client():
    """The patched openai.OpenAI instance: CI never reaches the network."""
    with patch('openai.OpenAI') as cls:
        yield cls.return_value


def test_parse_recommendations_sorts_by_severity_and_drops_extra_fields():
    text = json.dumps({'recommendations': [_recommendation('low', 'c'), {**_recommendation('high', 'a'), 'extra': 1},
                                           _recommendation('medium', 'b')]})

    assert [r['title'] for r in parse_recommendations(text)] == ['a', 'b', 'c']
    assert 'extra' not in parse_recommendations(text)[0]


@pytest.mark.parametrize('text, reason', [
    (None, 'Expecting value'),
    ('not json', 'Expecting value'),
    ('[]', 'no `recommendations` list'),
    (json.dumps({'recommendations': [{**_recommendation(), 'severity': 'critical'}]}), "severity 'critical'"),
    (json.dumps({'recommendations': [{**_recommendation(), 'category': 'vibes'}]}), "category 'vibes'"),
    (json.dumps({'recommendations': [{**_recommendation(), 'affected': [{'kind': 'Node'}]}]}), 'no text `namespace`'),
    (json.dumps({'recommendations': [{k: v for k, v in _recommendation().items() if k != 'title'}]}), 'no text `title`'),
])
def test_parse_recommendations_rejects_malformed_output(text, reason):
    with pytest.raises(AdvisorError) as ex:
        parse_recommendations(text)

    assert ex.value.status == 502
    assert 'malformed recommendations' in str(ex.value) and reason in str(ex.value)


def test_ask_requests_structured_output_without_retries(openai_client):
    openai_client.chat.completions.create.return_value = _completion(json.dumps({'recommendations': []}))

    with patch('openai.OpenAI') as cls:
        cls.return_value = openai_client
        assert ask(CONFIG, [{'role': 'user', 'content': 'x'}]) == ([], None)

    cls.assert_called_once_with(api_key='sk-test', base_url='http://llm.local/v1', max_retries=0, timeout=600.0)
    kwargs = openai_client.chat.completions.create.call_args.kwargs
    assert kwargs['model'] == 'test-model'
    assert kwargs['response_format']['type'] == 'json_schema'
    assert kwargs['response_format']['json_schema']['strict'] is True


@pytest.mark.parametrize('usage, expected', [
    (_usage(USAGE), USAGE),
    (None, None),
    (MagicMock(prompt_tokens=None, completion_tokens=None, total_tokens=None), None),  # a provider without counts
])
def test_ask_reports_token_usage_when_the_provider_does(openai_client, usage, expected):
    completion = _completion(json.dumps({'recommendations': []}))
    completion.usage = usage
    openai_client.chat.completions.create.return_value = completion

    assert ask(CONFIG, []) == ([], expected)


@pytest.mark.parametrize('error, status, message', [
    (openai.AuthenticationError('bad key', response=_response(401), body=None), 401, 'API key'),
    (openai.RateLimitError('slow down', response=_response(429), body=None), 429, 'rate limiting'),
    (openai.APITimeoutError(request=REQUEST), 504, 'did not answer'),
    (openai.APIConnectionError(request=REQUEST), 502, 'Cannot reach'),
    (openai.InternalServerError('boom', response=_response(500), body=None), 502, 'answered 500'),
])
def test_ask_maps_provider_errors(openai_client, error, status, message):
    openai_client.chat.completions.create.side_effect = error

    with pytest.raises(AdvisorError) as ex:
        ask(CONFIG, [])

    assert ex.value.status == status and message in str(ex.value)
    openai_client.chat.completions.create.assert_called_once()


@pytest.fixture(scope='module')
def registry():
    registry = SyntheticRegistry(churn_interval=0)
    yield registry
    registry.close()


@pytest.fixture
def client(registry):
    return create_app(synthetic=registry, advisor=CONFIG).test_client()


@pytest.mark.parametrize('request_args', [('get', '/advisor/preview'), ('post', '/advisor')])
def test_advisor_endpoints_are_off_without_a_config(registry, openai_client, request_args):
    method, url = request_args
    response = getattr(create_app(synthetic=registry).test_client(), method)(url, json={})

    assert response.status_code == 404
    assert b'OPENAI_API_KEY' in response.data
    openai_client.chat.completions.create.assert_not_called()


@pytest.mark.parametrize('redact', [True, False])
def test_preview_shows_exactly_what_is_sent(client, openai_client, redact):
    openai_client.chat.completions.create.return_value = _completion(json.dumps({'recommendations': []}))

    preview = client.get(f'/advisor/preview?context={SHOP}&redact={str(redact).lower()}')
    answer = client.post(f'/advisor?context={SHOP}', json={'redact': redact})

    assert preview.status_code == 200 and answer.status_code == 200
    assert preview.json['model'] == 'test-model'
    assert preview.json['messages'] == openai_client.chat.completions.create.call_args.kwargs['messages']
    assert ('"redacted":true' in preview.json['messages'][1]['content']) is redact


def test_advisor_restores_real_names_and_caches_the_answer(client, openai_client):
    messages = client.get(f'/advisor/preview?context={ML}').json['messages']
    summary = json.loads(messages[1]['content'].split('\n', 1)[1])
    workload = summary['findings'][next(iter(summary['findings']))]['top'][0]
    assert workload['namespace'].startswith('ns-') and workload['name'].startswith('workload-')
    openai_client.chat.completions.create.return_value = _completion(json.dumps({'recommendations': [
        _recommendation('low', f"Tune {workload['name']}"),
        _recommendation('high', 'Spread', [{'kind': workload['kind'], 'namespace': workload['namespace'], 'name': workload['name']}]),
    ]}), USAGE)

    first = client.post(f'/advisor?context={ML}', json={'redact': True})
    second = client.post(f'/advisor?context={ML}', json={'redact': True})

    assert first.status_code == 200 and first.json['cached'] is False
    assert first.json['usage'] == USAGE
    # A cached answer reports what the original cost.
    assert second.json == {**first.json, 'cached': True}
    openai_client.chat.completions.create.assert_called_once()
    high, low = first.json['recommendations']
    assert high['severity'] == 'high' and low['severity'] == 'low'
    affected = high['affected'][0]
    assert not affected['namespace'].startswith('ns-') and not affected['name'].startswith('workload-')
    assert low['title'] == f"Tune {affected['name']}"


def test_malformed_model_output_is_a_readable_error(client, openai_client):
    openai_client.chat.completions.create.return_value = _completion('Sure! Here are some ideas: ...')

    response = client.post(f'/advisor?context={SHOP}', json={'redact': False})

    assert response.status_code == 502
    assert response.data.startswith(b'The model returned malformed recommendations')


def test_advisor_rejects_a_non_json_body(client, openai_client):
    # A cross-site form or text/plain POST skips the CORS preflight; it must not reach the model.
    response = client.post(f'/advisor?context={SHOP}', data='x', content_type='text/plain')

    assert response.status_code == 415
    openai_client.chat.completions.create.assert_not_called()


@pytest.mark.parametrize('body', [[1], {'redact': 'yes'}, {'scope': 'ns/pod'}, {'scope': [1]}, {'scope': ['ns/pod'] * 20001}])
def test_advisor_rejects_a_bad_body(client, openai_client, body):
    response = client.post('/advisor', json=body)

    assert response.status_code == 400
    openai_client.chat.completions.create.assert_not_called()


def test_advisor_asks_again_when_redaction_flips(client, openai_client):
    openai_client.chat.completions.create.return_value = _completion(json.dumps({'recommendations': []}))

    redacted = client.post(f'/advisor?context={SHOP}', json={'redact': True})
    real = client.post(f'/advisor?context={SHOP}', json={'redact': False})

    assert redacted.json['cached'] is False and real.json['cached'] is False
    assert openai_client.chat.completions.create.call_count == 2


def test_advisor_asks_again_after_the_cluster_changes(openai_client):
    # Own registry: churning the shared one would change what the other tests see.
    registry = SyntheticRegistry(churn_interval=0)
    try:
        client = create_app(synthetic=registry, advisor=CONFIG).test_client()
        openai_client.chat.completions.create.return_value = _completion(json.dumps({'recommendations': []}))
        before = client.get(f'/advisor/preview?context={SHOP}').json
        assert client.post(f'/advisor?context={SHOP}', json={}).json['cached'] is False

        # A churn step may leave the summary as it was (a node flaps back); step until it differs.
        for _ in range(20):
            registry.get(SHOP).churn()
            if client.get(f'/advisor/preview?context={SHOP}').json != before:
                break
        else:
            pytest.fail('20 churn steps never changed the summary')

        assert client.post(f'/advisor?context={SHOP}', json={}).json['cached'] is False
        assert openai_client.chat.completions.create.call_count == 2
    finally:
        registry.close()


@pytest.mark.parametrize('value', ['False', 'FALSE', '0', 'no', ''])
def test_preview_stays_redacted_unless_redact_is_exactly_false(client, value):
    default = client.get(f'/advisor/preview?context={SHOP}').json

    assert client.get(f'/advisor/preview?context={SHOP}&redact={value}').json == default
    # The one value that turns it off really does, so the comparison above can fail.
    assert client.get(f'/advisor/preview?context={SHOP}&redact=false').json != default


def _summary(response) -> dict:
    return json.loads(response.json['messages'][1]['content'].split('\n', 1)[1])


def test_scope_narrows_the_summary_to_the_selected_pods(client, openai_client, registry):
    pods = [p for p in registry.get(SHOP).get_pod_resources() if p.node_name][:3]
    scope = [f'{p.namespace}/{p.name}' for p in pods]
    openai_client.chat.completions.create.return_value = _completion(json.dumps({'recommendations': []}))

    whole = _summary(client.get(f'/advisor/preview?context={SHOP}'))
    preview = client.post(f'/advisor/preview?context={SHOP}', json={'redact': True, 'scope': scope})
    answer = client.post(f'/advisor?context={SHOP}', json={'redact': True, 'scope': scope})

    assert 'pending' in whole and 'consolidation' in whole and 'scope' not in whole
    scoped = _summary(preview)
    assert scoped['scope'] == {'pods': 3, 'ofPods': whole['totals']['pods']}
    assert scoped['totals']['pods'] == 3 and 'pending' not in scoped and 'consolidation' not in scoped
    assert answer.status_code == 200 and answer.json['cached'] is False
    assert preview.json['messages'] == openai_client.chat.completions.create.call_args.kwargs['messages']


@pytest.mark.parametrize('scope', [[], ['nobody/gone-pod']])
@pytest.mark.parametrize('url', ['/advisor/preview', '/advisor'])
def test_a_scope_with_no_pods_is_rejected_before_the_model(client, openai_client, scope, url):
    response = client.post(f'{url}?context={SHOP}', json={'scope': scope})

    assert response.status_code == 400 and b'matches no pods' in response.data
    openai_client.chat.completions.create.assert_not_called()


def test_preview_post_needs_json(client):
    assert client.post(f'/advisor/preview?context={SHOP}', data='x', content_type='text/plain').status_code == 415
    assert client.post(f'/advisor/preview?context={SHOP}', json={'scope': 3}).status_code == 400
