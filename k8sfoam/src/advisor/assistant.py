"""The assistant's prompt: the user docs sections that match the question, plus the advisor's cluster summary.

The docs are about 77 KB, too much for a local model's context, so only the best-matching `##` sections are sent.
"""
import json
import math
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, NamedTuple, Tuple
from k8sfoam.src.advisor.summary import redact_text

# The wheel ships docs/*.md as k8sfoam/docs (pyproject.toml); a dev checkout has them at the repo root.
DOCS_DIRS = (Path(__file__).parents[2] / 'docs', Path(__file__).parents[3] / 'docs')
DOCS_BUDGET = 6000  # characters of docs per question
HISTORY_TURNS = 6
STOPWORDS = frozenset('the and for are but not you can how what which why does with this that from have has was its '
                      'into your about when where who will would there their them then than any all use using'.split())

ASSISTANT_PROMPT = """You are the help assistant of k8sfoams, a local, read-only dashboard of Kubernetes resource
*requests* (not live usage) per node and pod, with audit findings, node health, cost and scheduling simulators.
Answer the user's question about using k8sfoams or about the cluster it shows. Use only the documentation excerpts
and the cluster summary below; if they do not answer the question, say so and name the docs page that comes closest.
k8sfoams never changes the cluster: to change something, give the YAML or kubectl command for the user to apply.
Use the exact names from the summary. Be concise; markdown and fenced code blocks are fine.
The cluster summary is the live data of the cluster the user is looking at; its lists are sorted biggest first
(namespaces by requested CPU). `pending` and `consolidation` are scheduler simulation results: where the waiting pods
would fit and why the rest cannot, and which nodes could be emptied together. `mentioned` holds the details of the
nodes, namespaces and workloads named in the question. Examples in the documentation describe demo clusters, not this one.
To show something on the map, give a query from the token reference in single backticks, such as `qos:BestEffort`;
the user can apply it with one click."""
# Summary before docs, and the sentence above: with the docs first, qwen2.5:32b took a namespace named in a
# docs example for the biggest one.

# Always sent: highlighting something on the map is the answer to many questions, and without the token table
# qwen2.5:32b invented `namespace:<name>` for `ns:<name>`.
QUERY_REFERENCE = 'Filtering › Token reference'


class Section(NamedTuple):
    page: str
    heading: str
    text: str

    @property
    def label(self) -> str:
        return f'{self.page} › {self.heading}' if self.heading else self.page


def _words(text: str) -> List[str]:
    """Lowercase words without stopwords; a plural `s` is dropped so `namespaces` matches `namespace`."""
    words = re.findall(r'[a-z0-9][a-z0-9-]+', text.lower())
    return [w[:-1] if len(w) > 3 and w.endswith('s') and not w.endswith('ss') else w for w in words if w not in STOPWORDS]


def _sections(markdown: str, fallback_title: str) -> List[Section]:
    markdown = re.sub(r'\A---\n.*?\n---\n', '', markdown, flags=re.S)  # front matter
    title, heading = fallback_title, ''
    lines: List[str] = []
    out: List[Section] = []

    def flush():
        text = '\n'.join(lines).strip()
        if text:
            out.append(Section(title, heading, text))

    for line in markdown.splitlines():
        if line.startswith('# ') and not out and not heading:
            title = line[2:].strip()
        elif line.startswith('## '):
            flush()
            heading, lines = line[3:].strip(), []
        elif not re.match(r'\s*(!\[|<|\[Try it\])', line):  # images, HTML and demo links only work on the docs site
            lines.append(line)
    flush()
    return out


@lru_cache(maxsize=1)
def load_docs() -> Tuple[Section, ...]:
    """Every section of the user docs; empty when no docs directory is found."""
    directory = next((d for d in DOCS_DIRS if d.is_dir()), None)
    if directory is None:
        return ()
    return tuple(s for path in sorted(directory.glob('*.md'))
                 for s in _sections(path.read_text(encoding='utf-8'), path.stem.replace('-', ' ').capitalize()))


def search_docs(query: str, budget: int = DOCS_BUDGET) -> List[Section]:
    """The sections best matching `query`, best first, within `budget` characters.

    A long section is cut to a third of the budget, so one reference table cannot crowd out the rest.

    shortcut: BM25 keyword match, not embeddings; upgrade if answers keep missing the right page.
    """
    docs = load_docs()
    terms = set(_words(query))
    if not docs or not terms:
        return []
    bags = [Counter(_words(s.text)) for s in docs]
    heads = [set(_words(s.label)) for s in docs]
    df = Counter(w for bag, head in zip(bags, heads) for w in set(bag) | head)
    idf = {w: math.log(1 + (len(docs) - df[w] + 0.5) / (df[w] + 0.5)) for w in terms if df[w]}
    avg = sum(sum(bag.values()) for bag in bags) / len(bags)

    def score(i: int) -> float:
        # BM25 (k1=1.2, b=0.75), so a long section does not win on length; a word in the heading counts double.
        norm = 1.2 * (0.25 + 0.75 * sum(bags[i].values()) / avg)
        return sum(weight * (bags[i][w] * 2.2 / (bags[i][w] + norm) + 2 * (w in heads[i])) for w, weight in idf.items())

    ranked = sorted((i for i in range(len(docs)) if score(i) > 0), key=lambda i: (-score(i), i))
    hits: List[Section] = []
    used = 0
    for i in ranked:
        section = docs[i]
        if len(section.text) > budget // 3:
            section = section._replace(text=section.text[:budget // 3].rsplit('\n', 1)[0] + '\n…')
        if used + len(section.text) > budget:
            break
        hits.append(section)
        used += len(section.text)
    return hits


def build_chat_messages(question: str, history: List[Dict[str, str]], payload: dict,
                        mapping: Dict[str, str]) -> Tuple[List[dict], List[str]]:
    """(messages, source labels) for one question. `mapping` is empty unless names are redacted.

    The question and history come from the browser with real names; they are redacted again on every turn.
    """
    history = history[-HISTORY_TURNS:]
    previous = next((t['content'] for t in reversed(history) if t['role'] == 'user'), '')
    pinned = [s for s in load_docs() if s.label == QUERY_REFERENCE]
    found = search_docs(f'{question}\n{previous}', DOCS_BUDGET - sum(len(s.text) for s in pinned))
    hits = pinned + [s for s in found if s.label != QUERY_REFERENCE]
    pages = ', '.join(dict.fromkeys(s.page for s in load_docs()))
    excerpts = '\n\n'.join(f'### {s.label}\n{s.text}' for s in hits) or '(no matching section)'
    system = (f'{ASSISTANT_PROMPT}\n\nCluster summary:\n{json.dumps(payload, separators=(",", ":"))}\n\n'
              f'Documentation pages: {pages}\n\nDocumentation excerpts:\n{excerpts}')
    turns = [{'role': t['role'], 'content': redact_text(t['content'], mapping)} for t in history]
    messages = [{'role': 'system', 'content': system}, *turns, {'role': 'user', 'content': redact_text(question, mapping)}]
    return messages, [s.label for s in hits]
