from flask import Flask, jsonify
from typing import Optional


def create_app() -> Optional[Flask]:
    try:
        app = Flask(__name__)

        @app.route('/healthcheck', methods=['GET'])
        def healthcheck():
            return jsonify({'status': 'ok'})

        return app
    except Exception:
        return None
