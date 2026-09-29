import argparse
from importlib.metadata import PackageNotFoundError, version
from flask import Flask, jsonify, request
from typing import Optional
from k8sfoam.src.k8s.k8s_client import K8sClient
from k8sfoam.src.utils.mappers import FoamTreeMapper


def get_version() -> str:
    try:
        return version('k8sfoams')
    except PackageNotFoundError:
        return 'unknown'


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
