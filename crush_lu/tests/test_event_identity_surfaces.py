"""Phase D surface tests for the Event Identity redesign (spec §7, §13, §14).

Covers the *display* side of the redesign — the structured Event Identity block
that replaces the free-text bio/interests snippet on member-, attendee- and
coach-facing surfaces:

  * a CI grep guard proving no template renders ``profile.bio`` /
    ``profile.interests`` (or ``|split_interests``) outside the single
    coach-only legacy block (§13);
  * the ``CrushProfile`` display helpers that resolve ``ask_me_about`` /
    ``interests_new`` for the templates (retired-safe, starters-first);
  * the reusable ``_event_identity`` display partial and the whitelisted
    ``_legacy_bio_interests`` block, rendered directly;
  * the check-in toast JSON producer, which must ship structured labels and
    never the legacy free-text field to event staff.

Phase C's write-path / wizard tests live in ``test_event_identity_ui``; the
form validators in ``test_event_identity``.
"""

import re
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.template.loader import render_to_string
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from crush_lu.models import CrushProfile, Interest

User = get_user_model()

TEMPLATE_ROOT = Path(__file__).resolve().parents[1] / "templates"

# The single template allowed to render the retained free-text columns: the
# collapsed, coach-only "Legacy bio & interests" block (spec §6.4).
LEGACY_BLOCK_WHITELIST = {"partials/_legacy_bio_interests.html"}

# Legacy free-text CrushProfile expressions. `profile.interests` matches
# `own_profile`/`other_profile`/`target_profile`/`crushprofile` too (all end in
# "profile"), but the negative lookahead spares the NEW `interests_new` M2M, and
# neither pattern touches the legitimate `coach.bio` / `membership.interests`.
LEGACY_EXPR = re.compile(r"profile\.bio|profile\.interests(?!_new)|\|\s*split_interests")


def _make_interest(slug, label, category="outdoors", sort_order=0):
    # Prefix the slug so it never collides with the taxonomy the data migration
    # seeds into the test DB (Interest rows persist across TestCase transactions).
    return Interest.objects.create(
        slug=f"eidsurf-{slug}",
        label=label,
        category=category,
        sort_order=sort_order,
        is_active=True,
    )


def _make_profile(username="disp@example.com", **kwargs):
    user = User.objects.create_user(
        username=username, email=username, password="pass-pass-pass", first_name="Bo"
    )
    defaults = dict(
        date_of_birth=timezone.now().date() - timedelta(days=30 * 365),
        gender="F",
        location="canton-luxembourg",
    )
    defaults.update(kwargs)
    return CrushProfile.objects.create(user=user, **defaults)


class SurfaceGuardTests(SimpleTestCase):
    """CI grep guard: legacy free-text profile fields render nowhere but the
    whitelisted coach-only legacy block (spec §13)."""

    def test_no_legacy_free_text_outside_the_whitelisted_block(self):
        violations = []
        for path in TEMPLATE_ROOT.rglob("*.html"):
            rel = path.relative_to(TEMPLATE_ROOT).as_posix()
            # Whitelist by suffix so it matches under crush_lu/ or any app root.
            if any(rel.endswith(w) for w in LEGACY_BLOCK_WHITELIST):
                continue
            for lineno, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                if LEGACY_EXPR.search(line):
                    violations.append(f"{rel}:{lineno}: {line.strip()}")
        self.assertEqual(
            violations,
            [],
            "Legacy bio/interests free text must render only in "
            "partials/_legacy_bio_interests.html. Offenders:\n"
            + "\n".join(violations),
        )

    def test_guard_regex_matches_and_spares_the_right_expressions(self):
        # Catches the legacy expressions on every profile-holding variable…
        for hit in (
            "{{ profile.bio }}",
            "{{ own_profile.interests }}",
            "{{ other_profile.bio }}",
            "{{ target_profile.interests|default:'—' }}",
            "{{ candidate.crushprofile.bio }}",
            "{% for t in profile.interests|split_interests %}",
        ):
            self.assertRegex(hit, LEGACY_EXPR)
        # …but spares the new M2M and the unrelated legitimate fields.
        for ok in (
            "{{ profile.interests_new.all }}",
            "{% for i in profile.event_interest_chips %}",
            "{{ profile.ask_me_about_interests }}",
            "{{ coach.bio }}",
            "{% for i in membership.interests.all %}",
        ):
            self.assertNotRegex(ok, LEGACY_EXPR)


class EventIdentityModelHelperTests(TestCase):
    """The read-only display resolvers on CrushProfile (spec §7)."""

    def setUp(self):
        self.hiking = _make_interest("hiking", "Hiking", sort_order=1)
        self.music = _make_interest("live-music", "Live music", "music", sort_order=2)
        self.food = _make_interest("cooking", "Cooking", "food", sort_order=3)
        self.profile = _make_profile()
        self.profile.interests_new.set([self.hiking, self.music, self.food])

    def test_ask_me_about_interests_preserves_declared_order(self):
        self.profile.ask_me_about = [self.food.pk, self.hiking.pk]
        self.profile.save()
        self.assertEqual(
            self.profile.ask_me_about_interests, [self.food, self.hiking]
        )

    def test_ask_me_about_interests_drops_ids_no_longer_selected(self):
        """A retired / de-selected id must never render a dangling chip."""
        self.profile.ask_me_about = [self.food.pk]
        self.profile.save()
        self.profile.interests_new.remove(self.food)  # de-selected
        self.assertEqual(self.profile.ask_me_about_interests, [])

    def test_event_interest_chips_puts_starters_first(self):
        self.profile.ask_me_about = [self.food.pk]
        self.profile.save()
        chips = self.profile.event_interest_chips
        self.assertEqual(chips[0], self.food)  # starter floated to the front
        self.assertEqual(set(chips), {self.hiking, self.music, self.food})

    def test_has_event_identity(self):
        self.assertTrue(self.profile.has_event_identity)  # has interests
        empty = _make_profile("empty@example.com")
        self.assertFalse(empty.has_event_identity)
        empty.event_vibe = "quiet_corner"
        empty.save()
        self.assertTrue(empty.has_event_identity)  # vibe alone counts

    def test_has_event_identity_ignores_stale_ask_me_about(self):
        """A dangling ask_me_about id (no vibe, no selected interests) resolves to
        zero chips, so it must NOT flag identity — else surfaces render an empty
        heading/container."""
        stale = _make_profile("stale@example.com")
        stale.ask_me_about = [self.hiking.pk]  # never selected on this profile
        stale.save()
        self.assertEqual(stale.ask_me_about_interests, [])
        self.assertFalse(stale.has_event_identity)

    def test_checkin_interest_labels_are_capped_and_structured(self):
        labels = self.profile.checkin_interest_labels(limit=2)
        self.assertEqual(len(labels), 2)
        self.assertTrue(set(labels) <= {"Hiking", "Live music", "Cooking"})


class EventIdentityDisplayPartialTests(TestCase):
    """The reusable ``_event_identity`` display partial (spec §7)."""

    PARTIAL = "crush_lu/partials/_event_identity.html"

    def setUp(self):
        self.hiking = _make_interest("hiking", "Hiking", sort_order=1)
        self.music = _make_interest("live-music", "Live music", "music", sort_order=2)
        self.profile = _make_profile(event_vibe="quiet_corner")
        self.profile.interests_new.set([self.hiking, self.music])
        self.profile.ask_me_about = [self.music.pk]
        self.profile.save()

    def test_renders_vibe_and_interest_chips(self):
        html = render_to_string(self.PARTIAL, {"profile": self.profile})
        self.assertIn("Hiking", html)
        self.assertIn("Live music", html)
        self.assertIn(str(self.profile.get_event_vibe_display()), html)

    def test_hide_vibe_omits_the_vibe_badge(self):
        html = render_to_string(
            self.PARTIAL, {"profile": self.profile, "hide_vibe": True}
        )
        self.assertNotIn(str(self.profile.get_event_vibe_display()), html)
        self.assertIn("Hiking", html)

    def test_limit_truncates_but_keeps_the_starter(self):
        html = render_to_string(
            self.PARTIAL, {"profile": self.profile, "limit": ":1", "hide_vibe": True}
        )
        # Only one chip, and it is the ask-me-about starter (floated first).
        self.assertIn("Live music", html)
        self.assertNotIn("Hiking", html)

    def test_empty_profile_renders_empty_text_only(self):
        empty = _make_profile("empty@example.com")
        html = render_to_string(
            self.PARTIAL, {"profile": empty, "empty_text": "Nothing yet"}
        )
        self.assertIn("Nothing yet", html)
        html_no_fallback = render_to_string(self.PARTIAL, {"profile": empty})
        self.assertNotIn("event-identity", html_no_fallback)

    def test_vibe_only_profile_renders_no_empty_container_when_vibe_hidden(self):
        """A hide_vibe surface must not emit an empty container for a profile
        whose only identity is a vibe (Codex P2: container with no chips)."""
        vibe_only = _make_profile("vibeonly@example.com", event_vibe="at_the_bar")
        html = render_to_string(
            self.PARTIAL,
            {"profile": vibe_only, "hide_vibe": True, "empty_text": "Nothing yet"},
        )
        self.assertNotIn("event-identity", html)  # no empty container
        self.assertIn("Nothing yet", html)  # falls through to empty_text
        # Shown (not hidden), the same profile renders the vibe badge.
        shown = render_to_string(self.PARTIAL, {"profile": vibe_only})
        self.assertIn("event-identity", shown)

    def test_stale_ask_me_about_renders_no_empty_container(self):
        """A profile whose only identity is a dangling ask_me_about id renders
        nothing but empty_text — never an empty heading/container."""
        stale = _make_profile("stale2@example.com")
        stale.ask_me_about = [self.hiking.pk]  # never selected
        stale.save()
        html = render_to_string(
            self.PARTIAL,
            {"profile": stale, "heading": "Event Identity", "empty_text": "None"},
        )
        self.assertNotIn("event-identity", html)
        self.assertNotIn("Event Identity", html)  # no dangling heading
        self.assertIn("None", html)


class LegacyBioInterestsPartialTests(TestCase):
    """The whitelisted coach-only collapsed legacy block (spec §6.4, §14)."""

    PARTIAL = "crush_lu/partials/_legacy_bio_interests.html"

    def test_renders_retained_bio_and_unmapped_interests(self):
        """Acceptance §14: unmapped free-text interests stay visible to coaches."""
        profile = _make_profile(
            bio="An old free-text bio", interests="quidditch, spelunking"
        )
        html = render_to_string(self.PARTIAL, {"profile": profile})
        self.assertIn("An old free-text bio", html)
        self.assertIn("quidditch", html)
        self.assertIn("spelunking", html)

    def test_renders_nothing_when_both_columns_empty(self):
        profile = _make_profile("blank@example.com", bio="", interests="")
        html = render_to_string(self.PARTIAL, {"profile": profile})
        self.assertNotIn("Legacy bio", html)
        self.assertEqual(html.strip(), "")


class CheckinToastJsonTests(TestCase):
    """The check-in toast JSON producer — a template-invisible surface (§7/§13):
    structured labels ship to event staff, the free-text field never does."""

    def test_get_profile_data_ships_labels_not_free_text(self):
        from crush_lu.views_checkin import _get_profile_data

        hiking = _make_interest("hiking", "Hiking", sort_order=1)
        profile = _make_profile(
            is_approved=True,
            bio="secret free-text bio",
            interests="secret, free, text",
        )
        profile.interests_new.set([hiking])
        registration = SimpleNamespace(user=profile.user, user_id=profile.user_id)

        data = _get_profile_data(registration)

        self.assertEqual(data["interests"], "Hiking")
        self.assertNotIn("secret", data["interests"])
