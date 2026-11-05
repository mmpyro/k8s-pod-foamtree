import hashlib
import json
import os
from importlib.util import find_spec
from typing import List, Mapping, NamedTuple, Optional

DEFAULT_MODEL = 'gpt-4o-mini'


class AdvisorConfig(NamedTuple):
    """The LLM connection, server side only: the key never goes to the browser or the log."""
    api_key: str
    base_url: Optional[str]
    model: str


def load_config(environ: Mapping[str, str] = os.environ) -> Optional[AdvisorConfig]:
    """The advisor's connection, or None (feature off) without a key or without the `advisor` extra.

    The key comes from the environment, never a flag: a flag leaks into shell history and `ps`.
    """
    api_key = environ.get('OPENAI_API_KEY')
    if not api_key or find_spec('openai') is None:
        return None
    return AdvisorConfig(api_key, environ.get('OPENAI_BASE_URL') or None,
                         environ.get('K8SFOAMS_ADVISOR_MODEL') or DEFAULT_MODEL)


# Local models need minutes, not seconds: qwen2.5:32b on Ollama took ~5 min for 6 recommendations.
TIMEOUT = 600.0
SEVERITIES = ('high', 'medium', 'low')
CATEGORIES = ('requests/limits', 'resilience', 'scheduling', 'node health', 'cost')

SYSTEM_PROMPT = """You are a Kubernetes capacity and reliability reviewer.
You get a JSON summary of one cluster's resource *requests* (not live usage): allocation per node pool and zone,
node warnings, audit findings with the workloads they hit, the QoS mix, device fragmentation and the biggest namespaces.
The findings and warnings were computed deterministically and are facts; do not contradict or invent them.
Explain why they matter and what to change, ordered by impact. Stay within resource configuration:
requests and limits, resilience (replicas, spreading, PDBs), scheduling constraints, node health, node pools and cost.
Every recommendation names the objects it is about in `affected` (kind is Namespace, Node, NodePool or a workload
kind such as Deployment, StatefulSet, DaemonSet; namespace is "" for cluster-scoped objects), using the exact names
from the summary, and gives a concrete YAML snippet or kubectl command in `suggestedChange`.
Give between 3 and 8 recommendations, most impactful first; an empty list is only right for a cluster without any
finding or warning. Never suggest anything that needs data the summary does not contain.
Answer with one JSON object: {"recommendations": [{"title", "severity" (high|medium|low),
"category" (requests/limits|resilience|scheduling|node health|cost), "rationale",
"affected": [{"kind", "namespace", "name"}], "suggestedChange"}]}."""
# Spelled out although the response format carries the schema: under a strict schema alone,
# qwen2.5:32b answered with an empty list for a cluster full of findings.

RESPONSE_SCHEMA = {
    'type': 'object',
    'additionalProperties': False,
    'required': ['recommendations'],
    'properties': {'recommendations': {'type': 'array', 'items': {
        'type': 'object',
        'additionalProperties': False,
        'required': ['title', 'severity', 'category', 'rationale', 'affected', 'suggestedChange'],
        'properties': {
            'title': {'type': 'string'},
            'severity': {'type': 'string', 'enum': list(SEVERITIES)},
            'category': {'type': 'string', 'enum': list(CATEGORIES)},
            'rationale': {'type': 'string'},
            'affected': {'type': 'array', 'items': {
                'type': 'object',
                'additionalProperties': False,
                'required': ['kind', 'namespace', 'name'],
                'properties': {'kind': {'type': 'string'}, 'namespace': {'type': 'string'}, 'name': {'type': 'string'}},
            }},
            'suggestedChange': {'type': 'string'},
        },
    }}},
}


class AdvisorError(Exception):
    """A failed advisor call, with the HTTP status and the message the UI shows."""

    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.status = status


def build_messages(payload: dict) -> List[dict]:
    """The exact chat messages sent for a summary; the consent dialog shows these."""
    return [{'role': 'system', 'content': SYSTEM_PROMPT},
            {'role': 'user', 'content': 'Cluster summary:\n' + json.dumps(payload, separators=(',', ':'))}]


def cache_key(config: AdvisorConfig, messages: List[dict]) -> str:
    return hashlib.sha256(json.dumps([config.model, messages], sort_keys=True).encode()).hexdigest()


def ask(config: AdvisorConfig, messages: List[dict]) -> List[dict]:
    """The model's recommendations, most severe first. Raises AdvisorError; never retries."""
    import openai  # the optional `advisor` extra: load_config() returned None without it

    client = openai.OpenAI(api_key=config.api_key, base_url=config.base_url, max_retries=0, timeout=TIMEOUT)
    try:
        # Plain dicts, not the SDK's TypedDicts: the schema and messages are also served to the consent dialog.
        response = client.chat.completions.create(  # type: ignore[call-overload]
            model=config.model, messages=messages, temperature=0,
            response_format={'type': 'json_schema',
                             'json_schema': {'name': 'recommendations', 'strict': True, 'schema': RESPONSE_SCHEMA}})
    except openai.AuthenticationError:
        raise AdvisorError('The LLM provider rejected the API key (OPENAI_API_KEY)', 401)
    except openai.RateLimitError:
        raise AdvisorError('The LLM provider is rate limiting this key; try again later', 429)
    except openai.APITimeoutError:
        raise AdvisorError(f'The model did not answer within {TIMEOUT:.0f} s', 504)
    except openai.APIConnectionError:
        raise AdvisorError(f'Cannot reach the LLM provider at {client.base_url}', 502)
    except openai.APIStatusError as ex:
        raise AdvisorError(f'The LLM provider answered {ex.status_code}: {ex.message}', 502)
    if not response.choices:
        raise AdvisorError('The model returned no answer', 502)
    return parse_recommendations(response.choices[0].message.content)


def _strings(item: dict, keys: tuple, where: str) -> None:
    for key in keys:
        if not isinstance(item.get(key), str):
            raise ValueError(f'{where} has no text `{key}`')


def parse_recommendations(text: Optional[str]) -> List[dict]:
    """Validated recommendations, most severe first. Raises AdvisorError on malformed model output."""
    try:
        answer = json.loads(text or '')
        recommendations = answer.get('recommendations') if isinstance(answer, dict) else None
        if not isinstance(recommendations, list):
            raise ValueError('no `recommendations` list')
        for i, r in enumerate(recommendations, 1):
            where = f'recommendation {i}'
            if not isinstance(r, dict):
                raise ValueError(f'{where} is not an object')
            _strings(r, ('title', 'rationale', 'suggestedChange'), where)
            if r.get('severity') not in SEVERITIES:
                raise ValueError(f'{where} has severity {r.get("severity")!r}, expected one of {", ".join(SEVERITIES)}')
            if r.get('category') not in CATEGORIES:
                raise ValueError(f'{where} has category {r.get("category")!r}, expected one of {", ".join(CATEGORIES)}')
            if not isinstance(r.get('affected'), list) or not all(isinstance(a, dict) for a in r['affected']):
                raise ValueError(f'{where} has no `affected` list')
            for a in r['affected']:
                _strings(a, ('kind', 'namespace', 'name'), f'{where} affected object')
    except ValueError as ex:  # json.JSONDecodeError is a ValueError
        raise AdvisorError(f'The model returned malformed recommendations: {ex}', 502)
    keep = ('title', 'severity', 'category', 'rationale', 'affected', 'suggestedChange')
    return sorted(({k: r[k] for k in keep} for r in recommendations), key=lambda r: SEVERITIES.index(r['severity']))
