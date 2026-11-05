import os
from importlib.util import find_spec
from typing import Mapping, NamedTuple, Optional

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
