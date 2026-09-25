"""
Global HTMX failure toast.

A failed hx-post used to be silent, and the event registration button stayed
on "Processing..." for good. crush_lu/base.html now renders the toast copy
server-side in the page language (components/htmx_error_toast.html) and loads
htmx-error-toast.js, which shows that copy on htmx:responseError / sendError /
timeout and resets the requesting form's Alpine ``isSubmitting`` flag. On
mobile a member's toast sits above the bottom tab bar, not on top of it.

The in-browser behaviour is covered by test_htmx_error_toast_playwright.py
(excluded from the default run); these tests pin the server-rendered contract
the script reads.
"""

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest
from django.core.cache import cache
from django.template.loader import render_to_string
from django.utils import translation
from django.utils.safestring import mark_safe
from django.utils.translation import gettext

NETWORK_MSGID = "Network error. Please check your connection and try again."
SERVER_MSGID = "An error occurred. Please try again."
RATE_LIMITED_MSGID = "Too many attempts. Please try again later."
QUEUED_MSGID = (
    "Once you're back online, your event registrations and messages will sync "
    "automatically!"
)
MSGIDS = {
    "network": NETWORK_MSGID,
    "server": SERVER_MSGID,
    "rate-limited": RATE_LIMITED_MSGID,
    "queued": QUEUED_MSGID,
}

# Copy per language. DE uses the informal "du" and FR the formal "vous", like
# the neighbouring error strings in the crush_lu catalogs. Every msgid already
# existed in the catalogs (the 429 one is @ratelimit's, the queued one is the
# offline page's), so this feature adds no translation.
EXPECTED_COPY = {
    "en": dict(MSGIDS),
    "de": {
        "network": (
            "Netzwerkfehler. Bitte überprüfe deine Verbindung und versuche es erneut."
        ),
        "server": "Ein Fehler ist aufgetreten. Bitte versuche es erneut.",
        "rate-limited": "Zu viele Versuche. Bitte versuche es später erneut.",
        "queued": (
            "Sobald du wieder online bist, werden deine Event-Anmeldungen und "
            "Nachrichten automatisch synchronisiert!"
        ),
    },
    "fr": {
        "network": "Erreur réseau. Vérifiez votre connexion et réessayez.",
        "server": "Une erreur s'est produite. Veuillez réessayer.",
        "rate-limited": "Trop de tentatives. Veuillez réessayer plus tard.",
        "queued": (
            "Une fois de retour en ligne, vos inscriptions aux événements et vos "
            "messages seront synchronisés automatiquement !"
        ),
    },
}

HANDLER_SCRIPT_RE = re.compile(
    r'<script[^>]*src="[^"]*crush_lu/js/htmx-error-toast\.js[^"]*"[^>]*>'
)
VOID_ELEMENTS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "source",
    "track",
    "wbr",
}


class _Elements(HTMLParser):
    """Every start tag as (tag, attrs, attrs of each open ancestor).

    Attribute values come back unescaped, exactly as the browser's
    getAttribute() would return them.
    """

    def __init__(self, content):
        super().__init__(convert_charrefs=True)
        self._open = []
        self.found = []
        self.feed(content)
        self.close()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.found.append((tag, attrs, [a for _t, a in self._open]))
        if tag not in VOID_ELEMENTS:
            self._open.append((tag, attrs))

    def handle_startendtag(self, tag, attrs):
        self.found.append((tag, dict(attrs), [a for _t, a in self._open]))

    def handle_endtag(self, tag):
        for i in range(len(self._open) - 1, -1, -1):
            if self._open[i][0] == tag:
                del self._open[i:]
                return


def _element_by_id(content, element_id):
    matches = [e for e in _Elements(content).found if e[1].get("id") == element_id]
    assert len(matches) == 1, f"expected exactly one #{element_id}"
    return matches[0]


@pytest.fixture(autouse=True)
def _clear_cache():
    # Every test viewer shares one @ratelimit counter; start from a clean cache.
    cache.clear()
    yield
    cache.clear()


def _rendered_copy(content):
    _tag, attrs, _ancestors = _element_by_id(content, "htmx-error-toast-messages")
    return attrs, {kind: attrs.get(f"data-{kind}") for kind in MSGIDS}


@pytest.mark.django_db
@pytest.mark.parametrize("lang", ["en", "de", "fr"])
def test_base_renders_htmx_error_copy_in_page_language(client, lang):
    response = client.get(f"/{lang}/about/", HTTP_HOST="crush.lu")

    assert response.status_code == 200
    attrs, copy = _rendered_copy(response.content.decode())
    assert copy == EXPECTED_COPY[lang]
    # A data carrier, not visible UI.
    assert "hidden" in attrs


def test_htmx_error_copy_survives_quotes_in_a_translation(monkeypatch):
    """A msgstr with '"', '&' or '<' must reach the script intact.

    The trans tag marks a literal's translation safe, so without explicit
    escaping a '"' in a future msgstr would end the data attribute early and
    the toast would show a truncated message.
    """
    import django.template.base as template_base

    real_gettext_lazy = template_base.gettext_lazy
    tricky = 'Sag "Hallo" & <b>nochmal</b>'

    def fake_gettext_lazy(msgid):
        if msgid == NETWORK_MSGID:
            # Like a real catalog hit: a safe literal's translation is safe.
            return mark_safe(tricky)
        return real_gettext_lazy(msgid)

    monkeypatch.setattr(template_base, "gettext_lazy", fake_gettext_lazy)

    _attrs, copy = _rendered_copy(
        render_to_string("crush_lu/components/htmx_error_toast.html")
    )

    assert copy == {**MSGIDS, "network": tricky}


@pytest.mark.parametrize("lang", ["de", "fr"])
def test_htmx_error_copy_is_translated_in_the_catalog(lang):
    with translation.override(lang):
        assert {kind: gettext(msgid) for kind, msgid in MSGIDS.items()} == (
            EXPECTED_COPY[lang]
        )


def _static(name):
    path = Path(__file__).resolve().parents[1] / "static" / "crush_lu" / name
    return path.read_text(encoding="utf-8")


def test_queued_copy_mirrors_the_service_worker_queue_rule():
    """htmx-error-toast.js decides "this POST was queued for background sync"
    with a copy of sw-workbox.js's isQueueablePost() exclusions. If the two
    lists drift, a member is either told a lost request will replay, or told
    to retry a request that is already queued (and sends it twice)."""
    sw = _static("sw-workbox.js")
    body = sw[sw.index("function isQueueablePost(") :]
    body = body[: body.index("\n    }\n")]
    sw_prefixes = re.findall(r'!pathname\.startsWith\("([^"]+)"\)', body)

    handler = _static("js/htmx-error-toast.js")
    start = handler.index("var QUEUE_EXCLUDED_PREFIXES = [")
    handler_prefixes = re.findall(
        r'"([^"]+)"', handler[start : handler.index("];", start)]
    )

    assert sw_prefixes, "isQueueablePost() exclusions not found in sw-workbox.js"
    assert handler_prefixes == sw_prefixes


def test_dismissed_toast_leaves_the_store_before_its_exit_animation():
    """The dedupe in htmx-error-toast.js reads Alpine.store("toasts").items;
    removeToast() must drop the item when dismissal begins, not 300 ms later,
    or a dismiss-retry-fail within that window shows nothing."""
    src = _static("js/toast-component.js")
    body = src[src.index("function removeToast(") :]
    body = body[: body.index("function addToast(")]
    assert body.index('Alpine.store("toasts").remove(id)') < body.index("setTimeout(")


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
    _tag, poll, _ancestors = _element_by_id(content, "messages-container")
    assert poll.get("hx-trigger", "").startswith("every ")
    assert poll.get("data-htmx-error-toast") == "off"

    # The compose form posts into the same list but is a sibling of the poll:
    # neither it nor anything around it opts out, so a failed send still toasts.
    compose = [
        (attrs, ancestors)
        for tag, attrs, ancestors in _Elements(content).found
        if tag == "form"
        and attrs.get("hx-post")
        and attrs.get("hx-target") == "#messages-container"
    ]
    assert len(compose) == 1, "accepted connection should render the compose form"
    form, ancestors = compose[0]
    assert all(a.get("data-htmx-error-toast") != "off" for a in [form, *ancestors])


@pytest.mark.django_db
def test_member_toasts_sit_above_the_mobile_tab_bar(client, django_user_model):
    """Members see the fixed bottom tab bar on mobile. Their toast container
    must not carry the bottom-0 utility: utilities are the later cascade layer,
    so it would beat the .toast-above-nav offset and put toasts on the bar."""
    guest_page = client.get("/en/about/", HTTP_HOST="crush.lu").content.decode()
    _tag, guest, _ancestors = _element_by_id(guest_page, "toast-container")
    assert "bottom-0" in guest["class"].split()
    assert "toast-above-nav" not in guest["class"].split()

    member = django_user_model.objects.create_user(
        username="toast.nav@example.com",
        email="toast.nav@example.com",
        password="Toast-pass-2026!",
    )
    client.force_login(member)
    member_page = client.get("/en/about/", HTTP_HOST="crush.lu").content.decode()
    _tag, container, _ancestors = _element_by_id(member_page, "toast-container")
    assert "toast-above-nav" in container["class"].split()
    assert "bottom-0" not in container["class"].split()
    # The tab bar the offset clears is really there for members.
    assert 'class="bottom-nav"' in member_page
