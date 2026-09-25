"""
Global HTMX failure toast (UX review finding 4-04).

A failed hx-post used to be silent, and the event registration button stayed
on "Processing..." for good. crush_lu/base.html now renders the toast copy
server-side in the page language (components/htmx_error_toast.html) and loads
htmx-error-toast.js, which shows that copy on htmx:responseError / sendError /
timeout and resets the requesting form's Alpine ``isSubmitting`` flag.

The in-browser behaviour is covered by test_htmx_error_toast_playwright.py
(excluded from the default run); these tests pin the server-rendered contract
the script reads.
"""

import html
import re

import pytest
from django.core.cache import cache
from django.utils import translation
from django.utils.translation import gettext

NETWORK_MSGID = "Network error. Please check your connection and try again."
SERVER_MSGID = "An error occurred. Please try again."

# (network, server) per language. DE uses the informal "du" and FR the formal
# "vous", like the neighbouring error strings in the crush_lu catalogs.
EXPECTED_COPY = {
    "en": (NETWORK_MSGID, SERVER_MSGID),
    "de": (
        "Netzwerkfehler. Bitte überprüfe deine Verbindung und versuche es erneut.",
        "Ein Fehler ist aufgetreten. Bitte versuche es erneut.",
    ),
    "fr": (
        "Erreur réseau. Vérifiez votre connexion et réessayez.",
        "Une erreur s'est produite. Veuillez réessayer.",
    ),
}

MESSAGES_TAG_RE = re.compile(r'<div id="htmx-error-toast-messages"[^>]*>')
HANDLER_SCRIPT_RE = re.compile(
    r'<script[^>]*src="[^"]*crush_lu/js/htmx-error-toast\.js[^"]*"[^>]*>'
)


@pytest.fixture(autouse=True)
def _clear_cache():
    # Every test viewer shares one @ratelimit counter; start from a clean cache.
    cache.clear()
    yield
    cache.clear()


def _rendered_copy(content):
    match = MESSAGES_TAG_RE.search(content)
    assert match, "crush_lu/base.html must render #htmx-error-toast-messages"
    tag = match.group(0)
    copy = {
        key: html.unescape(value)
        for key, value in re.findall(r'data-(network|server)="([^"]*)"', tag)
    }
    return tag, copy


@pytest.mark.django_db
@pytest.mark.parametrize("lang", ["en", "de", "fr"])
def test_base_renders_htmx_error_copy_in_page_language(client, lang):
    response = client.get(f"/{lang}/about/", HTTP_HOST="crush.lu")

    assert response.status_code == 200
    tag, copy = _rendered_copy(response.content.decode())
    network, server = EXPECTED_COPY[lang]
    assert copy == {"network": network, "server": server}
    # A data carrier, not visible UI.
    assert re.search(r"\shidden[\s>]", tag)


@pytest.mark.parametrize("lang", ["de", "fr"])
def test_htmx_error_copy_is_translated_in_the_catalog(lang):
    with translation.override(lang):
        assert (gettext(NETWORK_MSGID), gettext(SERVER_MSGID)) == EXPECTED_COPY[lang]


@pytest.mark.django_db
def test_base_loads_the_global_htmx_error_handler_once(client):
    content = client.get("/en/about/", HTTP_HOST="crush.lu").content.decode()

    scripts = HANDLER_SCRIPT_RE.findall(content)
    assert len(scripts) == 1
    # Same loading convention as the other base.html scripts: CSP nonce + defer.
    assert "nonce=" in scripts[0]
    assert " defer " in scripts[0]


@pytest.mark.django_db
def test_chat_poll_opts_out_of_the_error_toast(client, connection_pair):
    """The 20 s background poll just retries, so it must not raise the global
    toast on every tick while offline; the compose form keeps it."""
    from crush_lu.models import UserDataConsent

    user1, _user2, connection, _event = connection_pair
    UserDataConsent.objects.update_or_create(
        user=user1, defaults={"crushlu_consent_given": True}
    )
    client.force_login(user1)

    response = client.get(f"/en/connections/{connection.id}/", HTTP_HOST="crush.lu")

    assert response.status_code == 200
    content = response.content.decode()
    poll = re.search(r'<div id="messages-container"[^>]*>', content)
    assert poll, "accepted connection should render the polling message list"
    assert 'data-htmx-error-toast="off"' in poll.group(0)
    # Only the poll opts out; the compose form (a sibling) still gets the toast.
    assert content.count('data-htmx-error-toast="off"') == 1
