from django.test import Client


def test_healthz_returns_ok():
    response = Client().get("/healthz/")

    assert response.status_code == 200
    assert response.content == b"OK"
