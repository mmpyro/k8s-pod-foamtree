from flask import Flask, jsonify, request
from typing import Optional
from k8sfoam.src.k8s.k8s_client import K8sClient
from k8sfoam.src.utils.mappers import FoamTreeMapper


def create_app() -> Optional[Flask]:
    try:
        app = Flask(__name__, static_url_path='', static_folder='frontend')

        @app.route('/healthcheck', methods=['GET'])
        def healthcheck():
            return jsonify({'status': 'ok'})

        @app.route('/resources/<resource_type>', methods=['GET'])
        def get_k8s_resources(resource_type: str):
            try:
                context = request.args.get('context')
                k8s_client = K8sClient(context)
                mapper = FoamTreeMapper(k8s_client.get_node_resources(), [*k8s_client.get_pod_resources()])
                if resource_type.lower() == 'memory':
                    return jsonify(mapper.transform_memory_resources_to_foamtree())
                elif resource_type.lower() == 'cpu':
                    return jsonify(mapper.transform_cpu_resources_to_foamtree())
                else:
                    return f'Resource type: {resource_type} is not supported. Supported types are: [cpu, memory]', 400
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
