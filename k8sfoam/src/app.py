import argparse
from flask import Flask, jsonify, request
from typing import Optional
from k8sfoam.src.k8s.k8s_client import K8sClient
from k8sfoam.src.utils.mappers import FoamTreeMapper

_CORE_RESOURCE_TYPES = frozenset({'cpu', 'memory'})


def create_app() -> Optional[Flask]:
    try:
        app = Flask(__name__, static_url_path='', static_folder='frontend')

        @app.route('/healthcheck', methods=['GET'])
        def healthcheck():
            return jsonify({'status': 'ok'})

        @app.route('/resources/extended-keys', methods=['GET'])
        def get_extended_resource_keys():
            """Return sorted list of extended resource types detected in the cluster.

            The frontend fetches this on startup to populate the Resource metric
            selector with GPU/TPU/storage options only when they actually exist.
            """
            try:
                context = request.args.get('context')
                k8s_client = K8sClient(context)
                mapper = FoamTreeMapper(k8s_client.get_node_resources(), [*k8s_client.get_pod_resources()])
                return jsonify(mapper.get_extended_resource_keys())
            except Exception as ex:
                return str(ex), 500

        @app.route('/resources/<path:resource_type>', methods=['GET'])
        def get_k8s_resources(resource_type: str):
            try:
                context = request.args.get('context')
                k8s_client = K8sClient(context)
                mapper = FoamTreeMapper(k8s_client.get_node_resources(), [*k8s_client.get_pod_resources()])
                rt = resource_type.lower()
                if rt == 'memory':
                    return jsonify(mapper.transform_memory_resources_to_foamtree())
                elif rt == 'cpu':
                    return jsonify(mapper.transform_cpu_resources_to_foamtree())
                else:
                    # Any other key is treated as an extended resource
                    # (e.g. "nvidia.com/gpu", "ephemeral-storage").
                    return jsonify(mapper.transform_extended_resources_to_foamtree(resource_type))
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
    app = create_app()

    if app:
        parser = argparse.ArgumentParser()
        parser.add_argument('--host', type=str, default='127.0.0.1', required=False, help='Host IP on which server listen')
        parser.add_argument('--port', type=int, default=8080, required=False, help='Port on which server listen')
        parser.add_argument('-d', action='store_true', help='Run server in debug mode')
        args = parser.parse_args()
        app.run(host=args.host, port=args.port, debug=args.d)
