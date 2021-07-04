import pytest
from app.app import create_app


@pytest.fixture
def test_client():
    app = create_app()
    return app.test_client()


def test_healthcheck(test_client):
    # When
    response = test_client.get('/healthcheck')

    # Then
    assert response.status_code == 200
