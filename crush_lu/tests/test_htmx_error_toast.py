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
INTERRUPTED_MSGID = "Connection interrupted. Retrying…"
UNCONFIRMED_MSGID = (
    "We could not confirm whether this was sent. Check before sending it again."
)
MSGIDS = {
    "network": NETWORK_MSGID,
    "server": SERVER_MSGID,
    "rate-limited": RATE_LIMITED_MSGID,
    "queued": QUEUED_MSGID,
    "interrupted": INTERRUPTED_MSGID,
    "unconfirmed": UNCONFIRMED_MSGID,
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
        "interrupted": "Verbindung unterbrochen. Neuer Versuch…",
        "unconfirmed": (
            "Wir konnten nicht bestätigen, ob das gesendet wurde. Prüfe das, "
            "bevor du es erneut sendest."
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
        "interrupted": "Connexion interrompue. Nouvelle tentative…",
        "unconfirmed": "Nous n'avons pas pu confirmer l'envoi. Vérifiez avant de renvoyer.",
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


def test_queued_copy_needs_the_workers_confirmation():
    """Eligibility is not proof the request was stored: the worker posts
    "crush-queued" only after bgSyncPlugin's queue write succeeded (Workbox
    awaits fetchDidFail callbacks in order and stops at the first throw), and
    the page shows the "will sync" copy only for a URL it heard about."""
    sw = _static("sw-workbox.js")
    ack_start = sw.index("const queuedAckPlugin")
    ack = sw[ack_start : sw.index("workbox.routing.registerRoute(", ack_start)]
    assert 'type: "crush-queued"' in ack and "fetchDidFail" in ack
    post_route = sw[ack_start:]
    post_route = post_route[: post_route.index('"POST",')]
    assert "plugins: [bgSyncPlugin, queuedAckPlugin]" in post_route

    handler = _static("js/htmx-error-toast.js")
    assert 'data.type === "crush-queued"' in handler
    on_failure = handler[handler.index("function onFailure(") :]
    assert "ackedRecently(key, since)" in on_failure
    # Missing ack + a worker that acknowledges = the write failed = "network";
    # missing ack + an older worker (never answers the capability question)
    # = queued silently = "interrupted", never "network".
    assert "workerQueuedAck !== true" in on_failure
    # No confirmation from a worker that never answered: storage unknown, so
    # the copy neither promises a retry nor invites a resend.
    assert 'copy = "unconfirmed"' in on_failure
    assert 'copy = "interrupted"' not in on_failure
    # The ack goes to the client that issued the fetch, never to every tab,
    # and carries the page's request id so it is matched to that request.
    assert "self.clients.get(clientId)" in ack and "matchAll" not in ack
    assert 'requestId: request.headers.get("X-Crush-Request-Id")' in ack
    assert 'headers[REQUEST_ID_HEADER] = "r"' in handler
    assert "var key = requestIdOf(detail) || url;" in on_failure
    # The form's isSubmitting flag is released only once the copy is known:
    # every reset goes through finish(), and the ack wait calls it last.
    assert on_failure.count("resetSubmitState(elt)") == 1
    finish = on_failure[on_failure.index("var finish = function") :]
    finish = finish[: finish.index("};")]
    assert "resetSubmitState(elt)" in finish and "restoreFocus(elt)" in finish
    timeout = on_failure[on_failure.index("setTimeout(function () {") :]
    assert "finish(copy);" in timeout[: timeout.index("}, QUEUE_ACK_WAIT_MS);")]
    assert 'data.type === "crush-capabilities?" && event.source' in sw
    # Storage is not replay: the worker holds the Queue itself and drains it
    # on the page's "online" request, so a promise to sync holds even where
    # the Background Sync API is missing or its registration failed.
    assert 'new workbox.backgroundSync.Queue("crush-queue"' in sw
    assert "BackgroundSyncPlugin(" not in sw
    assert 'data.type === "crush-drain-queue"' in sw and "drainQueue(crushQueue)" in sw
    # fetch consumes the body; a failed replay is requeued by serializing the
    # request again, so the replay must use a clone or the entry is lost.
    assert "await fetch(entry.request.clone())" in sw
    # Only a 429 proves the server did not process the request (the ratelimit
    # decorators answer before any view runs): it is requeued and the drain
    # stops, like a network failure. A 5xx can follow a committed mutation
    # (the connection message view stores the row before rendering), so it is
    # final: replaying it would create the message twice. Drains from any
    # source and any worker generation are serialized: one in-flight promise
    # per worker plus an origin-wide Web Lock across generations.
    assert "return response.status === 429;" in sw
    drain = sw[sw.index("function drainQueue(queue)") : sw.index("const crushQueue")]
    assert "if (drainInFlight) return drainInFlight;" in drain
    assert "drainInFlight = withDrainLock(async () => {" in drain
    assert 'locks.request("crush-queue-drain", run)' in sw
    assert "if (replayShouldRetry(response))" in drain
    assert drain.count("await queue.unshiftRequest(entry);") == 2
    assert 'window.addEventListener("online", scheduleDrains)' in handler
    assert 'postMessage({ type: "crush-drain-queue" })' in handler
    # A POST can fail while the browser still says it is online, so the
    # drain is also requested on a retry schedule after every ack.
    assert "var DRAIN_RETRY_MS = [" in handler
    # The schedule is open-ended (capped backoff) until the worker reports an
    # empty queue, and a capability answer with leftover entries starts it.
    assert "DRAIN_RETRY_MS[Math.min(step, DRAIN_RETRY_MS.length - 1)]" in handler
    assert 'data.type === "crush-drained"' in handler
    assert "drainReported(data.queued)" in handler
    assert 'postMessage({ type: "crush-drained", remaining })' in sw
    assert "queued = await crushQueue.size();" in sw
    ack_handler = handler[handler.index('data.type === "crush-queued"') :]
    assert "scheduleDrains();" in ack_handler[: ack_handler.index("} else if")]
    # Only the registration and message POSTs get the copy that names them;
    # every other queued action gets the action-neutral copy.
    assert "copy = queuedCopyFor(url);" in on_failure
    assert "/\\/events\\/\\d+\\/register\\/$/" in handler
    assert "/\\/connections\\/\\d+\\/$/" in handler
    # Forms without isSubmitting/hx-disabled-elt: the submit control is held
    # from beforeRequest until success or until finish() classifies the
    # failure, so a second click cannot queue a duplicate POST.
    assert 'document.addEventListener("htmx:beforeRequest"' in handler
    assert "holdSubmitters(detail.elt || evt.target)" in handler
    assert "if (detail.successful) releaseHeld(detail.elt || evt.target)" in handler
    assert "releaseHeld(elt)" in finish
    capabilities = sw[sw.index('data.type === "crush-capabilities?"') :]
    capabilities = capabilities[: capabilities.index('"crush-drain-queue"')]
    assert 'type: "crush-capabilities",' in capabilities
    assert "queuedAck: true," in capabilities
    assert 'postMessage({ type: "crush-capabilities?" })' in handler
    assert 'addEventListener(\n            "controllerchange"' in handler or (
        '"controllerchange"' in handler
    )
    # The base kind is a closure variable shared by every event; a per-event
    # reassignment would make one 429 classify every later failure.
    assert "kind = " not in on_failure.replace("var kind", "").replace("kind === ", "")


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
