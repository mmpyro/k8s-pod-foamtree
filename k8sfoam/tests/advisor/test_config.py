import pytest
from unittest.mock import patch
from k8sfoam.src.advisor import DEFAULT_MODEL, AdvisorConfig, load_config
from k8sfoam.src.app import create_app


@pytest.mark.parametrize('environ, expected', [
    ({}, None),
    ({'OPENAI_API_KEY': ''}, None),
    ({'OPENAI_BASE_URL': 'http://localhost:11434/v1'}, None),
    ({'OPENAI_API_KEY': 'sk-1'}, AdvisorConfig('sk-1', None, DEFAULT_MODEL)),
    ({'OPENAI_API_KEY': 'sk-1', 'OPENAI_BASE_URL': 'http://localhost:11434/v1', 'K8SFOAMS_ADVISOR_MODEL': 'qwen2.5:32b'},
     AdvisorConfig('sk-1', 'http://localhost:11434/v1', 'qwen2.5:32b')),
])
def test_load_config(environ, expected):
    assert load_config(environ) == expected


@patch('k8sfoam.src.advisor.find_spec', return_value=None)
def test_load_config_is_off_without_the_advisor_extra(find_spec):
    assert load_config({'OPENAI_API_KEY': 'sk-1'}) is None
    find_spec.assert_called_once_with('openai')


@pytest.mark.parametrize('advisor, expected', [
    (None, {'enabled': False, 'model': None}),
    (AdvisorConfig('sk-secret', 'http://llm/v1', 'm'), {'enabled': True, 'model': 'm'}),
])
def test_advisor_status_never_returns_the_key(advisor, expected):
    response = create_app(watch=False, advisor=advisor).test_client().get('/advisor/status')

    assert response.status_code == 200
    assert response.json == expected
    assert b'sk-secret' not in response.data and b'llm' not in response.data
