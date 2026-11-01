"""The cost-sync webhook accepts an optional list of steps."""

import json

import pytest
from django.test import RequestFactory

from power_up.finops import views_webhook


@pytest.fixture
def post(monkeypatch):
    monkeypatch.setenv("SECRET_SYNC_TOKEN", "tok")
    captured = {}

    def fake_call_command(command, **options):
        captured.update(options)
        command.step_results = [{"step": "import", "status": "ok", "elapsed": 1.0}]

    monkeypatch.setattr(views_webhook, "call_command", fake_call_command)

    def _post(body=None, token="tok"):
        data = json.dumps(body) if body is not None else ""
        request = RequestFactory().post(
            "/finops/api/sync/",
            data=data,
            content_type="application/json",
            HTTP_X_SYNC_TOKEN=token,
        )
        return views_webhook.trigger_cost_sync(request)

    _post.captured = captured
    return _post


def test_empty_body_runs_everything(post):
    response = post()
    assert response.status_code == 200
    assert "steps" not in post.captured
    assert json.loads(response.content)["steps"][0]["step"] == "import"


def test_steps_are_forwarded(post):
    post({"steps": ["aggregations"], "full": True})
    assert post.captured["steps"] == ["aggregations"]
    assert post.captured["full"] is True


@pytest.mark.parametrize("steps", [[], ["nope"], "import", [1]])
def test_invalid_steps_are_rejected(post, steps):
    assert post({"steps": steps}).status_code == 400


def test_bad_token_is_still_rejected(post):
    assert post({"steps": ["import"]}, token="wrong").status_code == 403
