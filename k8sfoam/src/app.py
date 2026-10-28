import argparse
import time
from importlib.metadata import PackageNotFoundError, version
from flask import Flask, Response, jsonify, request
from typing import Any, Iterator, Optional, Protocol
from k8sfoam.src.k8s.k8s_client import K8sClient, DEFAULT_PAGE_SIZE
from k8sfoam.src.k8s.cache import CacheRegistry
from k8sfoam.src.utils.mappers import FoamTreeMapper
from k8sfoam.src.common.resources import convert_cpu, convert_memory, convert_extended, is_extended
from k8sfoam.src.common.dtos import PodResources
from k8sfoam.src.common.scheduler import simulate_consolidation, simulate_fit, simulate_drain
from k8sfoam.src.synthetic.client import DEFAULT_CHURN_INTERVAL, SyntheticRegistry
from k8sfoam.src.synthetic.scenarios import DEFAULT_SEED

# Minimum seconds between two `changed` events; a burst in between becomes one.
DEBOUNCE = 1.0
# A quiet stream still sends a comment this often, so a closed tab is noticed
# (the write fails) and proxies do not drop the connection as idle.
HEARTBEAT = 15.0


def get_version() -> str:
    try:
        return version('k8sfoams')
    except PackageNotFoundError:
        return 'unknown'


def parse_pod_spec(body) -> PodResources:
    """Hypothetical pod from the fit form. Raises ValueError on a malformed field."""
    if not isinstance(body, dict):
        raise ValueError('Request body must be a JSON object')
    node_selector = body.get('nodeSelector') or {}
    tolerations = body.get('tolerations') or []
    if not isinstance(node_selector, dict) or not all(isinstance(v, str) for v in node_selector.values()):
        raise ValueError('nodeSelector must map label keys to string values')
    if not isinstance(tolerations, list) or not all(isinstance(t, dict) for t in tolerations):
        raise ValueError('tolerations must be a list of objects')
    extended_spec = body.get('extended') or {}
    if not isinstance(extended_spec, dict):
        raise ValueError('extended must map resource names to quantities')
    # cpu/memory have their own fields; pods and attachable volumes are not requestable.
    invalid = [name for name in extended_spec if not is_extended(name)]
    if invalid:
        raise ValueError(f'Not an extended resource: {", ".join(invalid)}')
    try:
        cpu = convert_cpu(str(body.get('cpu') or '0'))
        memory = convert_memory(str(body.get('memory') or '0'))
        extended = {name: convert_extended(name, str(q)) for name, q in extended_spec.items()}
    except Exception:
        raise ValueError(f"Invalid quantity: cpu={body.get('cpu')!r}, memory={body.get('memory')!r}, "
                         f"extended={extended_spec!r}")
    tolerations = [{k: t.get(k) for k in ('key', 'operator', 'value', 'effect')} for t in tolerations]
    return PodResources('hypothetical', None, cpu, memory, [], [], extended={k: v for k, v in extended.items() if v},
                        node_selector=node_selector, tolerations=tolerations)


class Registry(Protocol):
    """Where the clusters of each context come from: the watch caches, or the synthetic clusters."""

    def get(self, context: Optional[str]) -> Any:
        """The cluster of `context`, read like a K8sClient; None means the active context."""


def change_events(registry: Registry, context: Optional[str]) -> Iterator[str]:
    """Server-Sent Events for one browser tab: `changed` whenever the context's cache changes."""
    cache = registry.get(context)
    seen = cache.version
    last_sent = float('-inf')
    # EventSource drops an event whose data is empty, so each one carries the version.
    yield f'event: ready\ndata: {seen}\n\n'
    while True:
        if cache.wait_for_change(seen, HEARTBEAT) != seen:
            delay = DEBOUNCE - (time.monotonic() - last_sent)
            if delay > 0:
                time.sleep(delay)
            seen = cache.version
            last_sent = time.monotonic()
            yield f'event: changed\ndata: {seen}\n\n'
            continue
        yield ': ping\n\n'
        # Counts as use, so an open tab keeps a quiet cluster's cache from idling out,
        # and picks up the replacement once a failed cache has been stopped.
        current = registry.get(context)
        if current is not cache:
            cache, seen = current, current.version


def positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError(f'must be at least 1, got {value}')
    return number


def non_negative_float(value: str) -> float:
    number = float(value)
    if number < 0:
        raise argparse.ArgumentTypeError(f'must be at least 0, got {value}')
    return number


def create_app(page_size: int = DEFAULT_PAGE_SIZE, watch: bool = True,
               synthetic: Optional[SyntheticRegistry] = None) -> Optional[Flask]:
    """The dashboard server. With `synthetic`, every context is a generated cluster and no kubeconfig is read."""
    try:
        app = Flask(__name__, static_url_path='', static_folder='frontend')
        registry: Optional[Registry] = synthetic or (CacheRegistry(page_size) if watch else None)

        def cluster(context: Optional[str]) -> Any:
            """Nodes and pods of a context: from its watch cache, or listed right now with --no-watch."""
            return registry.get(context) if registry is not None else K8sClient(context, page_size)

        @app.route('/healthcheck', methods=['GET'])
        def healthcheck():
            return jsonify({'status': 'ok'})

        @app.route('/resources', methods=['GET'])
        def get_all_k8s_resources():
            try:
                k8s_client = cluster(request.args.get('context'))
                mapper = FoamTreeMapper(k8s_client.get_node_resources(), [*k8s_client.get_pod_resources()])
                return jsonify(mapper.combined())
            except Exception as ex:
                return str(ex), 500

        # path: extended resource names carry a vendor prefix (nvidia.com/gpu).
        @app.route('/resources/<path:resource_type>', methods=['GET'])
        def get_k8s_resources(resource_type: str):
            try:
                k8s_client = cluster(request.args.get('context'))
                mapper = FoamTreeMapper(k8s_client.get_node_resources(), [*k8s_client.get_pod_resources()])
                # cpu/memory keep their case-insensitive match; extended names are case-sensitive in Kubernetes.
                resource = resource_type.lower() if resource_type.lower() in ('cpu', 'memory') else resource_type
                supported = mapper.available_resources()
                if resource not in supported:
                    return (f'Resource type: {resource_type} is not supported. '
                            f'Supported types are: [{", ".join(supported)}]'), 400
                return jsonify(mapper.transform(resource))
            except Exception as ex:
                return str(ex), 500

        @app.route('/simulate/fit', methods=['POST'])
        def simulate_pod_fit():
            try:
                pod = parse_pod_spec(request.get_json(silent=True))
            except ValueError as ex:
                return str(ex), 400
            try:
                k8s_client = cluster(request.args.get('context'))
                return jsonify(simulate_fit(pod, k8s_client.get_node_resources(), k8s_client.get_pod_resources()))
            except Exception as ex:
                return str(ex), 500

        @app.route('/simulate/drain/<node_name>', methods=['GET'])
        def simulate_node_drain(node_name: str):
            try:
                k8s_client = cluster(request.args.get('context'))
                result = simulate_drain(node_name, k8s_client.get_node_resources(), k8s_client.get_pod_resources())
            except Exception as ex:
                return str(ex), 500
            if result is None:
                return f'Node {node_name} not found', 404
            return jsonify(result)

        @app.route('/simulate/consolidation', methods=['GET'])
        def simulate_cluster_consolidation():
            try:
                k8s_client = cluster(request.args.get('context'))
                return jsonify(simulate_consolidation(k8s_client.get_node_resources(), k8s_client.get_pod_resources()))
            except Exception as ex:
                return str(ex), 500

        @app.route('/events', methods=['GET'])
        def get_change_events():
            # Without a cache there is nothing to watch; the browser falls back to polling.
            if registry is None:
                return 'Live updates need the watch cache (started without --no-watch)', 404
            try:
                events = change_events(registry, request.args.get('context'))
                first = next(events)
            except Exception as ex:
                return str(ex), 500

            def stream() -> Iterator[str]:
                yield first
                yield from events

            return Response(stream(), mimetype='text/event-stream',
                            headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

        @app.route('/contexts', methods=['GET'])
        def get_k8s_contexts():
            if synthetic is not None:
                return jsonify(synthetic.contexts())
            try:
                k8s_client = K8sClient()
                return jsonify(k8s_client.get_contexts())
            except Exception as ex:
                return str(ex), 500

        @app.route('/', methods=['GET'])
        def web():
            return app.send_static_file('index.html')

        return app
    except Exception:
        return None


def main():
    """Main entry point for the k8sfoams console script."""
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', type=str, default='127.0.0.1', required=False, help='Host IP on which server listen')
    parser.add_argument('--port', type=int, default=8080, required=False, help='Port on which server listen')
    parser.add_argument('-d', action='store_true', help='Run server in debug mode')
    parser.add_argument('--page-size', type=positive_int, default=DEFAULT_PAGE_SIZE,
                        help='Objects fetched per LIST request')
    parser.add_argument('--no-watch', action='store_true',
                        help='List nodes and pods on every request instead of keeping a watch cache per context')
    parser.add_argument('--synthetic', action='store_true',
                        help='Show generated demo clusters instead of the kubeconfig contexts; needs no cluster')
    parser.add_argument('--seed', type=int, default=DEFAULT_SEED,
                        help='Seed of the --synthetic clusters; the same seed builds the same clusters')
    parser.add_argument('--churn-interval', type=non_negative_float, default=DEFAULT_CHURN_INTERVAL,
                        help='Seconds between random changes to the --synthetic clusters; 0 keeps them still')
    parser.add_argument('-v', '--version', action='version', version=get_version(), help='Show version and exit')
    args = parser.parse_args()

    options = {'page_size': args.page_size, 'watch': not args.no_watch}
    if args.synthetic:
        options['synthetic'] = SyntheticRegistry(args.seed, args.churn_interval)
    app = create_app(**options)
    if app:
        app.run(host=args.host, port=args.port, debug=args.d)
