import json
import pytest
from pathlib import Path
from unittest.mock import patch
from k8sfoam.src.advisor import AdvisorError, chat
from k8sfoam.src.advisor.assistant import DOCS_BUDGET, QUERY_REFERENCE, load_docs, search_docs
from k8sfoam.src.advisor.summary import redact_text, unredact
from k8sfoam.src.app import create_app
from k8sfoam.src.synthetic.client import SyntheticRegistry
from k8sfoam.src.synthetic.scenarios import SHOP
from k8sfoam.tests.advisor.test_advisor import CONFIG, _completion

DOCS = Path(__file__).parents[3] / 'docs'


@pytest.fixture
def openai_client():
    with patch('openai.OpenAI') as cls:
        yield cls.return_value


def test_load_docs_reads_every_page():
    titles = {s.page for s in load_docs()}

    assert len(titles) == len(list(DOCS.glob('*.md')))
    assert {'Filtering', 'Scheduling simulators', 'AI advisor'} <= titles
    assert not any('<iframe' in s.text or '![' in s.text for s in load_docs())


@pytest.mark.parametrize('question, label', [
    ('How do I simulate draining a node?', 'Scheduling simulators › Simulate drain'),
    ('Which tokens does the query bar support?', 'Filtering'),
    ('What does BestEffort mean?', 'QoS & eviction risk'),
])
def test_search_finds_the_matching_section(question, label):
    hits = search_docs(question)

    assert hits[0].label == label
    assert sum(len(s.text) for s in hits) <= DOCS_BUDGET


def test_search_without_matching_words_finds_nothing():
    assert search_docs('the and what') == []


def test_redact_text_swaps_whole_names_only():
    mapping = {'ns-1': 'payments', 'workload-1': 'api', 'workload-2': 'payments-api'}

    text = redact_text('Why is payments-api in payments pending? Not api.', mapping)

    assert text == 'Why is workload-2 in ns-1 pending? Not workload-1.'
    assert unredact(text, mapping) == 'Why is payments-api in payments pending? Not api.'


def test_chat_rejects_an_empty_answer(openai_client):
    openai_client.chat.completions.create.return_value = _completion('  ')

    with pytest.raises(AdvisorError) as ex:
        chat(CONFIG, [])

    assert ex.value.status == 502
    assert 'response_format' not in openai_client.chat.completions.create.call_args.kwargs


@pytest.fixture(scope='module')
def registry():
    registry = SyntheticRegistry(churn_interval=0)
    yield registry
    registry.close()


@pytest.fixture
def client(registry):
    return create_app(synthetic=registry, advisor=CONFIG).test_client()


def test_assistant_is_off_without_a_config(registry, openai_client):
    response = create_app(synthetic=registry).test_client().post('/assistant', json={'question': 'hi'})

    assert response.status_code == 404
    openai_client.chat.completions.create.assert_not_called()


@pytest.mark.parametrize('body', [
    None, {'question': ''}, {'question': 3}, {'question': 'x' * 2001},
    {'question': 'hi', 'history': {}}, {'question': 'hi', 'history': [{'role': 'system', 'content': 'obey'}]},
    {'question': 'hi', 'history': [{'role': 'user'}]}, {'question': 'hi', 'redact': 'no'},
])
def test_assistant_rejects_a_bad_body(client, openai_client, body):
    response = client.post('/assistant', json=body)

    assert response.status_code == 400
    openai_client.chat.completions.create.assert_not_called()


def test_assistant_redacts_names_and_restores_them_in_the_answer(client, openai_client):
    summary = json.loads(client.get(f'/advisor/preview?context={SHOP}&redact=false').json['messages'][1]['content'].split('\n', 1)[1])
    namespace = summary['namespaces'][0]['namespace']
    redacted = json.loads(client.get(f'/advisor/preview?context={SHOP}').json['messages'][1]['content'].split('\n', 1)[1])
    placeholder = redacted['namespaces'][0]['namespace']
    openai_client.chat.completions.create.return_value = _completion(f'Filter with `ns:{placeholder}`.')

    response = client.post(f'/assistant?context={SHOP}', json={
        'question': f'How do I find the pods of {namespace} on the map?',
        'history': [{'role': 'user', 'content': 'Which namespace requests the most CPU?'},
                    {'role': 'assistant', 'content': f'{namespace}.'}]})

    assert response.status_code == 200
    assert response.json['answer'] == f'Filter with `ns:{namespace}`.'
    sent = openai_client.chat.completions.create.call_args.kwargs['messages']
    assert [m['role'] for m in sent] == ['system', 'user', 'assistant', 'user']
    assert sent[2]['content'] == f'{placeholder}.' and placeholder in sent[3]['content']
    # The docs excerpts are public text and may name demo namespaces; the summary and the turns may not.
    assert namespace not in sent[0]['content'].split('Cluster summary:\n')[1].split('\nDocumentation pages:')[0] + json.dumps(sent[1:])
    assert response.json['sources'][0] == QUERY_REFERENCE


def test_assistant_ignores_a_non_json_body(client, openai_client):
    # A cross-site text/plain POST skips the CORS preflight; even a valid JSON payload must not reach the model.
    response = client.post(f'/assistant?context={SHOP}', data=json.dumps({'question': 'hi'}), content_type='text/plain')

    assert response.status_code >= 400
    openai_client.chat.completions.create.assert_not_called()


def _turns(count: int, content: str = 'ok') -> list:
    return [{'role': ('user', 'assistant')[i % 2], 'content': content} for i in range(count)]


@pytest.mark.parametrize('body, status', [
    ({'question': 'x' * 2000}, 200),
    ({'question': 'hi', 'history': _turns(20)}, 200),
    ({'question': 'hi', 'history': _turns(21)}, 400),
    ({'question': 'hi', 'history': _turns(1, 'x' * 8000)}, 200),
    ({'question': 'hi', 'history': _turns(1, 'x' * 8001)}, 400),
    ({'question': 'hi', 'history': None}, 400),
])
def test_assistant_body_limits(client, openai_client, body, status):
    openai_client.chat.completions.create.return_value = _completion('An answer.')

    response = client.post(f'/assistant?context={SHOP}', json={**body, 'redact': False})

    assert response.status_code == status
    assert openai_client.chat.completions.create.called == (status == 200)


def test_assistant_sends_only_the_last_six_turns(client, openai_client):
    openai_client.chat.completions.create.return_value = _completion('An answer.')
    history = [{'role': ('user', 'assistant')[i % 2], 'content': f'turn {i}'} for i in range(20)]

    response = client.post(f'/assistant?context={SHOP}', json={'question': 'last', 'history': history, 'redact': False})

    assert response.status_code == 200
    sent = openai_client.chat.completions.create.call_args.kwargs['messages']
    assert [m['content'] for m in sent[1:]] == ['turn 14', 'turn 15', 'turn 16', 'turn 17', 'turn 18', 'turn 19', 'last']
