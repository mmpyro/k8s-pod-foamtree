import argparse
from importlib.metadata import PackageNotFoundError, version
from flask import Flask, jsonify, request
from typing import Optional
from k8sfoam.src.k8s.k8s_client import K8sClient
from k8sfoam.src.utils.mappers import FoamTreeMapper
from k8sfoam.src.common.resources import convert_cpu, convert_memory
from k8sfoam.src.common.dtos import PodResources
from k8sfoam.src.common.scheduler import simulate_fit, simulate_drain


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
    try:
        cpu = convert_cpu(str(body.get('cpu') or '0'))
        memory = convert_memory(str(body.get('memory') or '0'))
    except Exception:
        raise ValueError(f"Invalid quantity: cpu={body.get('cpu')!r}, memory={body.get('memory')!r}")
    tolerations = [{k: t.get(k) for k in ('key', 'operator', 'value', 'effect')} for t in tolerations]
    return PodResources('hypothetical', None, cpu, memory, [], [], node_selector=node_selector,
                        tolerations=tolerations)


def create_app() -> Optional[Flask]:
    try:
        app = Flask(__name__, static_url_path='', static_folder='frontend')

        @app.route('/healthcheck', methods=['GET'])
        def healthcheck():
            return jsonify({'status': 'ok'})

        # path: extended resource names carry a vendor prefix (nvidia.com/gpu).
        @app.route('/resources/<path:resource_type>', methods=['GET'])
        def get_k8s_resources(resource_type: str):
            try:
                context = request.args.get('context')
                k8s_client = K8sClient(context)
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
                k8s_client = K8sClient(request.args.get('context'))
                return jsonify(simulate_fit(pod, k8s_client.get_node_resources(), k8s_client.get_pod_resources()))
            except Exception as ex:
                return str(ex), 500

        @app.route('/simulate/drain/<node_name>', methods=['GET'])
        def simulate_node_drain(node_name: str):
            try:
                k8s_client = K8sClient(request.args.get('context'))
                result = simulate_drain(node_name, k8s_client.get_node_resources(), k8s_client.get_pod_resources())
            except Exception as ex:
                return str(ex), 500
            if result is None:
                return f'Node {node_name} not found', 404
            return jsonify(result)

        @app.route('/contexts', methods=['GET'])
        def get_k8s_contexts():
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
    parser.add_argument('-v', '--version', action='version', version=get_version(), help='Show version and exit')
    args = parser.parse_args()

    app = create_app()
    if app:
        app.run(host=args.host, port=args.port, debug=args.d)
