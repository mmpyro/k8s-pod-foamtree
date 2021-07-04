from app.app import create_app


app = create_app()

if __name__ == '__main__' and app is not None:
    app.run(host="127.0.0.1", port=8080, debug=True)