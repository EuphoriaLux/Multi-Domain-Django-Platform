"""Shell polish: the shared toggle switch and the mobile notification bell.

UX review Wave 1, findings 8-04 and 8-03.

* 8-04 — ``account_settings.html`` carried ~15 inline copies of one toggle
  switch whose track had an unprefixed ``dark:bg-gray-800``: on the
  ``dark:bg-gray-800`` cards the OFF state was invisible (only the white knob
  showed). The copies now render through ``components/toggle.html`` with a
  ``dark:bg-gray-600`` track. The form contract must not move: the rendered
  checkbox names, values, ids, checked states and the JS hooks the push
  preference components delegate on are pinned below against the markup
  captured from main before the refactor.
* 8-03 — the mobile top-bar bell linked to *Connections* and its badge showed
  the pending connection-request count. It now opens the notification centre
  and its badge is the unread in-app ``Notification`` count — the same number
  the desktop bell (``/api/notifications/``) reports.
"""

from html.parser import HTMLParser
from pathlib import Path
from unittest import mock

from django.core.cache import cache
from django.test import RequestFactory, TestCase
from django.utils import timezone, translation

from crush_lu.context_processors import crush_user_context
from crush_lu.models import (
    CoachPushSubscription,
    CrushCoach,
    CrushProfile,
    EmailPreference,
    Notification,
    PushSubscription,
)
from crush_lu.tests.test_profile_edit_connect_card import _make_member

REPO_ROOT = Path(__file__).resolve().parents[2]


class _TagCollector(HTMLParser):
    """Collect every start tag as ``(tag, attrs-dict)`` in document order."""

    def __init__(self):
        super().__init__()
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def _tags(html):
    parser = _TagCollector()
    parser.feed(html)
    return parser.tags


def _switch_inputs(html):
    """Attrs of every toggle-switch checkbox (``peer`` input) on the page.

    The shared cookie banner's plain checkboxes carry no ``peer`` class and
    are not part of the settings form, so they are left out.
    """
    return [
        attrs
        for tag, attrs in _tags(html)
        if tag == "input"
        and attrs.get("type") == "checkbox"
        and "peer" in (attrs.get("class") or "").split()
    ]


def _checkbox_contract(html):
    """The form/JS-facing contract of every toggle switch on the page.

    Everything a submit, an Alpine binding or the push-preference event
    delegation reads: name, id, value, checked, data-* keys, Alpine
    ``:checked``/``@change`` and the non-styling hook classes. Styling classes
    and ``role`` are deliberately excluded — those are what the refactor
    changes.
    """
    contract = []
    for attrs in _switch_inputs(html):
        hooks = sorted(
            c
            for c in (attrs.get("class") or "").split()
            if c not in {"sr-only", "peer"}
        )
        contract.append(
            {
                "name": attrs.get("name"),
                "id": attrs.get("id"),
                "value": attrs.get("value"),
                "checked": "checked" in attrs,
                "pref_key": attrs.get("data-pref-key"),
                "has_subscription_id": "data-subscription-id" in attrs,
                "x_checked": attrs.get(":checked"),
                "x_change": attrs.get("@change"),
                "hooks": hooks,
            }
        )
    return contract


def _toggle(name=None, checked=False, pref_key=None, hooks=(), **extra):
    item = {
        "name": name,
        "id": None,
        "value": None,
        "checked": checked,
        "pref_key": pref_key,
        "has_subscription_id": pref_key is not None,
        "x_checked": None,
        "x_change": None,
        "hooks": list(hooks),
    }
    item.update(extra)
    return item


#: Captured from main (6db4a36) with the fixture below, before the toggle
#: markup moved into components/toggle.html. Order is document order.
EXPECTED_SETTINGS_CHECKBOXES = [
    _toggle(
        "unsubscribed_all",
        x_checked="unsubscribeAll",
        x_change="toggleUnsubscribe",
    ),
    _toggle("email_profile_updates", checked=True),
    _toggle("email_event_reminders", checked=False),
    _toggle("email_new_connections", checked=True),
    _toggle("email_new_messages", checked=False),
    _toggle("email_marketing", checked=True),
    _toggle("whatsapp_opt_in", checked=True),
    _toggle(pref_key="newMessages", checked=True, hooks=["push-pref-toggle"]),
    _toggle(pref_key="eventReminders", checked=False, hooks=["push-pref-toggle"]),
    _toggle(pref_key="newConnections", checked=True, hooks=["push-pref-toggle"]),
    _toggle(pref_key="profileUpdates", checked=False, hooks=["push-pref-toggle"]),
    _toggle(pref_key="newSubmissions", checked=False, hooks=["coach-push-pref-toggle"]),
    _toggle(
        pref_key="screeningReminders",
        checked=True,
        hooks=["coach-push-pref-toggle"],
    ),
    _toggle(pref_key="userResponses", checked=False, hooks=["coach-push-pref-toggle"]),
    _toggle(pref_key="systemAlerts", checked=True, hooks=["coach-push-pref-toggle"]),
]


class AccountSettingsToggleComponentTests(TestCase):
    """Finding 8-04: one toggle component, visible dark OFF state."""

    def setUp(self):
        cache.clear()
        self.user = _make_member("toggles@example.com")
        CrushProfile.objects.filter(user=self.user).update(
            phone_number="+352621000111", phone_verified=True
        )
        prefs = EmailPreference.get_or_create_for_user(self.user)
        prefs.unsubscribed_all = False
        prefs.email_profile_updates = True
        prefs.email_event_reminders = False
        prefs.email_new_connections = True
        prefs.email_new_messages = False
        prefs.email_marketing = True
        prefs.whatsapp_opt_in = True
        prefs.save()
        self.push = PushSubscription.objects.create(
            user=self.user,
            endpoint="https://fcm.googleapis.com/fcm/send/toggle-test",
            p256dh_key="p256dh",
            auth_key="auth",
            device_name="Android Chrome",
            enabled=True,
            notify_new_messages=True,
            notify_event_reminders=False,
            notify_new_connections=True,
            notify_profile_updates=False,
        )
        coach = CrushCoach.objects.create(
            user=self.user, bio="Coach", specializations="General", is_active=True
        )
        self.coach_push = CoachPushSubscription.objects.create(
            coach=coach,
            endpoint="https://fcm.googleapis.com/fcm/send/coach-toggle-test",
            p256dh_key="p256dh",
            auth_key="auth",
            device_name="Desktop Chrome",
            enabled=True,
            notify_new_submissions=False,
            notify_screening_reminders=True,
            notify_user_responses=False,
            notify_system_alerts=True,
        )
        self.client.login(username="toggles@example.com", password="testpass123")

    def _html(self):
        response = self.client.get("/en/account/settings/", HTTP_HOST="crush.lu")
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_form_contract_is_unchanged(self):
        """Names, values, ids, checked states and JS hooks match main."""
        self.assertEqual(_checkbox_contract(self._html()), EXPECTED_SETTINGS_CHECKBOXES)

    def test_push_toggles_carry_their_subscription_ids(self):
        ids = {
            attrs.get("data-pref-key"): attrs.get("data-subscription-id")
            for tag, attrs in _tags(self._html())
            if tag == "input" and attrs.get("data-pref-key")
        }
        for key in ("newMessages", "eventReminders", "newConnections"):
            self.assertEqual(ids[key], str(self.push.id))
        for key in ("newSubmissions", "screeningReminders", "systemAlerts"):
            self.assertEqual(ids[key], str(self.coach_push.id))

    def test_every_toggle_renders_through_the_component(self):
        html = self._html()
        tracks = [
            attrs["class"].split()
            for tag, attrs in _tags(html)
            if "after:content-['']" in (attrs.get("class") or "")
        ]
        self.assertEqual(len(tracks), len(EXPECTED_SETTINGS_CHECKBOXES))
        for classes in tracks:
            # The OFF track must stand off the dark:bg-gray-800 card.
            self.assertIn("dark:bg-gray-600", classes)
            self.assertNotIn("dark:bg-gray-800", classes)
            self.assertIn("after:bg-white", classes)
            self.assertIn("peer-focus:ring-4", classes)
        source = (
            REPO_ROOT / "crush_lu/templates/crush_lu/account_settings.html"
        ).read_text(encoding="utf-8")
        self.assertNotIn("after:content-['']", source)
        self.assertEqual(
            source.count('{% include "crush_lu/components/toggle.html"'),
            len(EXPECTED_SETTINGS_CHECKBOXES),
        )

    def test_toggles_expose_switch_semantics(self):
        checkboxes = _switch_inputs(self._html())
        self.assertEqual(len(checkboxes), len(EXPECTED_SETTINGS_CHECKBOXES))
        for attrs in checkboxes:
            self.assertEqual(attrs.get("role"), "switch")

    def test_labels_passed_with_underscore_stay_translated(self):
        """_("…") include args hit the same msgids the old {% trans %} did."""
        response = self.client.get("/de/account/settings/", HTTP_HOST="crush.lu")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Event-Erinnerungen")
        self.assertContains(
            response, "Erinnerungen an kommende Events, für die du angemeldet bist"
        )
        self.assertNotContains(response, ">Event Reminders<")

    def test_tone_variants_keep_their_checked_colours(self):
        html = self._html()
        tracks = [
            attrs["class"]
            for tag, attrs in _tags(html)
            if "after:content-['']" in (attrs.get("class") or "")
        ]
        self.assertIn("peer-checked:bg-red-500", tracks[0])  # unsubscribe all
        self.assertIn("peer-checked:bg-green-600", tracks[6])  # WhatsApp
        for index in (1, 2, 3, 4, 5, 7, 14):
            self.assertIn("peer-checked:bg-purple-600", tracks[index])

    def test_permission_denied_boxes_have_dark_mode_classes(self):
        source = (
            REPO_ROOT / "crush_lu/templates/crush_lu/account_settings.html"
        ).read_text(encoding="utf-8")
        blocks = source.split('<template x-if="showPermissionDenied">')[1:]
        self.assertEqual(len(blocks), 2)  # member push + coach push
        for block in blocks:
            box = block.split("</template>", 1)[0]
            self.assertIn("dark:bg-amber-900/20", box)
            self.assertIn("dark:border-amber-700", box)
            self.assertIn("dark:text-amber-200", box)
            self.assertIn("dark:text-amber-300", box)


def _first(tags, tag_name, css_class):
    for tag, attrs in tags:
        if tag == tag_name and css_class in (attrs.get("class") or "").split():
            return attrs
    raise AssertionError(f"<{tag_name} class={css_class!r}> not rendered")


class MobileTopBarBellTests(TestCase):
    """Finding 8-03: the mobile bell is the notification bell."""

    def setUp(self):
        cache.clear()
        self.user = _make_member("bell@example.com")
        for title in ("Ticket ready", "Profile approved"):
            Notification.objects.create(
                user=self.user, notification_type="test", title=title
            )
        Notification.objects.create(
            user=self.user,
            notification_type="test",
            title="Already read",
            read_at=timezone.now(),
        )
        # Another member's unread row must never reach this badge.
        other = _make_member("other-bell@example.com")
        Notification.objects.create(user=other, notification_type="test", title="x")
        self.client.login(username="bell@example.com", password="testpass123")

    def _tags(self, path="/en/dashboard/"):
        response = self.client.get(path, HTTP_HOST="crush.lu")
        self.assertEqual(response.status_code, 200)
        return _tags(response.content.decode())

    def test_bell_opens_the_notification_centre(self):
        bell = _first(self._tags(), "a", "top-bar-mobile-bell")
        self.assertEqual(bell["href"], "/en/notifications/")

    def test_bell_has_an_accessible_name_and_a_silent_badge(self):
        tags = self._tags()
        bell = _first(tags, "a", "top-bar-mobile-bell")
        self.assertEqual(bell["aria-label"], "Notifications")
        # Alpine swaps in "Unread notifications: N" while N > 0.
        self.assertEqual(bell["x-bind:aria-label"], "bellAriaLabel")
        badge = _first(tags, "span", "top-bar-mobile-bell-badge")
        # The count is spoken through the label, so the badge must not repeat it.
        self.assertEqual(badge.get("aria-hidden"), "true")
        self.assertEqual(badge["x-text"], "notificationBadgeText")

    def test_badge_is_the_unread_notification_count(self):
        header = _first(self._tags(), "header", "top-bar-mobile")
        # 2 unread + 1 read of mine (+ 1 unread of someone else's) -> 2.
        self.assertEqual(header["data-notification-count"], "2")
        self.assertEqual(
            header["data-i18n-unread-notifications"], "Unread notifications: {count}"
        )
        self.assertEqual(
            header["x-on:notif-unread-count.window"], "syncNotificationCount"
        )

    def test_desktop_bell_and_api_report_the_same_count(self):
        tags = self._tags()
        header = _first(tags, "header", "top-bar-mobile")
        desktop = next(
            attrs for tag, attrs in tags if attrs.get("x-data") == "notificationBell"
        )
        api = self.client.get(
            "/api/notifications/",
            HTTP_HOST="crush.lu",
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(api.status_code, 200)
        self.assertEqual(api.json()["unread_count"], 2)
        self.assertEqual(desktop["data-unread-count"], "2")
        self.assertEqual(header["data-notification-count"], "2")

    def test_marking_all_read_clears_the_badge(self):
        Notification.objects.filter(user=self.user).update(read_at=timezone.now())
        header = _first(self._tags(), "header", "top-bar-mobile")
        self.assertEqual(header["data-notification-count"], "0")

    def test_bell_label_is_translated(self):
        for lang, label, unread in (
            ("de", "Benachrichtigungen", "Ungelesene Benachrichtigungen: {count}"),
            ("fr", "Notifications", "Notifications non lues : {count}"),
        ):
            with self.subTest(lang=lang):
                cache.clear()
                tags = self._tags(f"/{lang}/dashboard/")
                bell = _first(tags, "a", "top-bar-mobile-bell")
                header = _first(tags, "header", "top-bar-mobile")
                self.assertEqual(bell["href"], f"/{lang}/notifications/")
                self.assertEqual(bell["aria-label"], label)
                self.assertEqual(header["data-i18n-unread-notifications"], unread)
        with translation.override("de"):
            self.assertEqual(
                translation.gettext("Unread notifications: {count}"),
                "Ungelesene Benachrichtigungen: {count}",
            )

    def test_unread_count_falls_back_to_zero_when_the_nav_context_fails(self):
        request = RequestFactory().get("/en/dashboard/")
        request.user = self.user
        with mock.patch.object(
            Notification, "unread_count_for", side_effect=RuntimeError("db down")
        ):
            context = crush_user_context(request)
        self.assertEqual(context["unread_notifications_count"], 0)

    def test_desktop_bell_broadcasts_and_mobile_bar_follows(self):
        """Source-level wiring check for the CSP-safe event bridge."""
        js = (REPO_ROOT / "crush_lu/static/crush_lu/js/alpine-components.js").read_text(
            encoding="utf-8"
        )
        bell = js.split('Alpine.data("notificationBell"', 1)[1].split(
            "Alpine.data(", 1
        )[0]
        self.assertIn('this.$watch("unreadCount"', bell)
        self.assertIn('new CustomEvent("notif-unread-count"', bell)
        top_bar = js.split('Alpine.data("topBarMobile"', 1)[1].split("Alpine.data(", 1)[
            0
        ]
        self.assertIn("syncNotificationCount: function (event)", top_bar)
        self.assertIn("get bellAriaLabel()", top_bar)
        self.assertNotIn("Object.assign", top_bar)


class ToggleComponentDocsTests(TestCase):
    def test_style_guide_documents_the_toggle(self):
        style = (REPO_ROOT / "crush_lu/STYLE.md").read_text(encoding="utf-8")
        # assertTrue, not assertIn: a failure would dump all of STYLE.md.
        self.assertTrue(
            "components/toggle.html" in style,
            "STYLE.md §4 must document components/toggle.html",
        )
