"""Public event guides must work through host routing in every language."""

import pytest
from django.core.cache import cache


@pytest.mark.django_db
@pytest.mark.parametrize("language", ["en", "fr", "de"])
def test_public_event_guides_and_localized_links(client, language):
    cache.clear()
    for route in (
        "how-it-works",
        "voting-demo",
        "speed-dating",
        "quiz-night",
        "events",
    ):
        response = client.get(f"/{language}/{route}/", HTTP_HOST="crush.lu")
        assert response.status_code == 200
        body = response.content.decode()
        if route == "speed-dating":
            assert f'href="/{language}/quiz-night/"' in body
        elif route == "quiz-night":
            assert f'href="/{language}/speed-dating/"' in body
        else:
            assert f'href="/{language}/speed-dating/"' in body
            assert f'href="/{language}/quiz-night/"' in body
        assert f'href="/{language}/events/"' in body
        if route != "events":
            assert f'href="/{language}/my-events/"' in body
        if language != "en" and route != "events":
            assert "A smooth arrival starts before the event" not in body
            assert "Check that your seat is confirmed" not in body
            assert "Find your next event" not in body
