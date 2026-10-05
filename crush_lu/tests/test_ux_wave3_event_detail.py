"""UX Wave 3 WP6 — event detail (findings 4-03, 4-12, 4-14, 4-18).

Paths are literal (`/en/events/<id>/`) because `reverse("crush_lu:...")`
resolves against the default urlconf, not the one `HTTP_HOST=crush.lu`
selects (see AGENTS.md).

Run with: pytest crush_lu/tests/test_ux_wave3_event_detail.py
"""

from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.contrib.staticfiles import finders
from django.core.cache import cache
from django.test import Client, TestCase
from django.utils import timezone

from crush_lu.models import CrushProfile, MeetupEvent, UserDataConsent
from crush_lu.tests.test_ux_wave4_design_sweep import buttons, with_class

User = get_user_model()


class EventDetailWave3TestBase(TestCase):
    """Shared fixtures: an upcoming event and a helper to fetch its page."""

    def setUp(self):
        # SQLite doesn't roll back the cache between tests and every test's
        # viewer is user 2 sharing one @ratelimit counter — see
        # AGENTS.md "SQLite rolls back PK sequences but not the cache".
        cache.clear()
        self.client = Client(HTTP_HOST="crush.lu")

    def _make_event(self, **kwargs):
        defaults = dict(
            title="Wine Tasting & Speed Dating – Moselle Edition",
            description=(
                "Join us for a wonderful evening of wine tasting paired with "
                "speed dating. We'll explore some of the finest wines the "
                "Moselle valley has to offer, guided by a local sommelier, "
                "while you meet new people in a relaxed, low-pressure "
                "setting. Snacks are provided, and the format leaves plenty "
                "of time for longer conversations between rounds."
            ),
            event_type="mixer",
            date_time=timezone.now() + timedelta(days=7),
            location="Luxembourg City",
            address="1 Rue de la Gare, Luxembourg",
            max_participants=20,
            registration_deadline=timezone.now() + timedelta(days=5),
            registration_fee=0,
            is_published=True,
            profile_requirement="none",
        )
        defaults.update(kwargs)
        return MeetupEvent.objects.create(**defaults)

    def _create_user(self, username):
        user = User.objects.create_user(
            username=username,
            email=username,
            password="testpass123",
            first_name=username.split("@")[0],
        )
        UserDataConsent.objects.update_or_create(
            user=user, defaults={"crushlu_consent_given": True}
        )
        return user

    def _detail_url(self, event):
        return f"/en/events/{event.id}/"

    def _get_detail(self, event):
        response = self.client.get(self._detail_url(event))
        self.assertEqual(response.status_code, 200)
        return response.content.decode()


class NoImageHeroTitleTests(EventDetailWave3TestBase):
    """#4-12: the no-image hero shows the full title, not a clamped one."""

    def test_hero_h2_no_longer_line_clamped(self):
        event = self._make_event()
        html = self._get_detail(event)
        self.assertNotIn("line-clamp-2", html)

    def test_full_title_renders_as_an_h1(self):
        event = self._make_event()
        html = self._get_detail(event)
        self.assertIn('<h1 class="text-white font-bold', html)
        self.assertIn(event.title, html)


class FactStripAndDescriptionTests(EventDetailWave3TestBase):
    """#4-03: a fact strip precedes the (collapsible) description."""

    def test_fact_strip_appears_before_description(self):
        event = self._make_event(registration_fee=15)
        html = self._get_detail(event)
        facts_index = html.index("Ages 18")  # part of the quick-facts strip
        description_index = html.index("eventDescriptionToggle")
        self.assertLess(
            facts_index,
            description_index,
            "the quick-facts strip must render before the description block",
        )
        self.assertIn("€15", html)

    def test_long_description_is_collapsible(self):
        event = self._make_event()
        html = self._get_detail(event)
        self.assertIn("eventDescriptionToggle", html)
        self.assertRegex(html, r'id="event-description-text"[^>]*line-clamp-4')
        self.assertIn("Read more", html)

    def test_short_description_has_no_read_more_toggle(self):
        event = self._make_event(description="A short blurb.")
        html = self._get_detail(event)
        self.assertNotIn("Read more", html)

    def test_cta_panel_and_sticky_bar_markers_present(self):
        event = self._make_event()
        html = self._get_detail(event)
        self.assertIn('id="event-cta-panel"', html)
        self.assertIn('id="event-sticky-cta"', html)
        self.assertIn("eventStickyCta", html)

    def test_read_more_toggle_has_aria_expanded_and_controls(self):
        """WP6-2: the toggle must expose its expand/collapse state and the
        region it controls, not just visually swap the label."""
        event = self._make_event()
        html = self._get_detail(event)
        self.assertIn('id="event-description-text"', html)
        self.assertIn('aria-controls="event-description-text"', html)
        self.assertIn('x-bind:aria-expanded="expandedAria"', html)


class StickyCtaToastOffsetTests(EventDetailWave3TestBase):
    """WP6-1: the sticky CTA bar must not permanently obscure toasts.

    Behaviour itself (offsetting #toast-container while the bar is visible,
    and never doing so when its own height is 0 on desktop) is exercised in
    the browser by test_ux_wave3_event_detail_playwright.py; this asserts
    the JS actually wires up the watcher it depends on.
    """

    def test_alpine_component_watches_visible_and_offsets_toast_container(self):
        js_path = finders.find("crush_lu/js/alpine/core.js")
        self.assertIsNotNone(
            js_path, "alpine/core.js not found via staticfiles finders"
        )
        with open(js_path, encoding="utf-8") as fh:
            js = fh.read()
        start = js.index('Alpine.data("eventStickyCta"')
        end = js.index("Alpine.data(", start + 1)
        component_src = js[start:end]
        self.assertIn('this.$watch("visible"', component_src)
        self.assertIn('getElementById("toast-container")', component_src)
        self.assertIn('removeProperty("bottom")', component_src)
        # Must guard offsetHeight == 0 (bar is md:hidden, so IntersectionObserver
        # can flip `visible` true on desktop while the bar itself is display:none).
        self.assertIn("barHeight > 0", component_src)
        # x-show always defers its own style mutation through
        # requestAnimationFrame (even with no x-transition), which runs AFTER
        # a plain $nextTick microtask — so offsetHeight must be read after at
        # least two animation frames, not via $nextTick alone, or it still
        # sees the pre-toggle height (0 on show, stale on hide).
        self.assertIn("requestAnimationFrame(function () {", component_src)
        self.assertEqual(
            component_src.count("requestAnimationFrame("),
            2,
            "offsetHeight must be read after two animation frames, "
            "not one — a single rAF can still race x-show's own",
        )

    def test_toast_offset_also_resyncs_on_resize(self):
        """Codex review on #1062: `visible` only ever changes from the
        IntersectionObserver, so crossing the md:hidden breakpoint while it
        stays true (rotate/resize) never re-ran the offset calc without a
        resize listener too."""
        js_path = finders.find("crush_lu/js/alpine/core.js")
        with open(js_path, encoding="utf-8") as fh:
            js = fh.read()
        start = js.index('Alpine.data("eventStickyCta"')
        end = js.index("Alpine.data(", start + 1)
        component_src = js[start:end]
        self.assertIn('addEventListener("resize"', component_src)
        # The watcher and the resize listener must share one calculation, or
        # they can disagree — the resize path must not just be a copy that
        # drifts from the watcher's own rules over time.
        self.assertEqual(component_src.count("function syncToastOffset"), 1)
        self.assertIn("setTimeout(syncToastOffset", component_src)
        self.assertIn(
            "requestAnimationFrame(syncToastOffset)",
            component_src,
            "the visible-watcher path must call the same function, not a "
            "second copy of the calculation",
        )


class VerificationDeadEndLinksTests(EventDetailWave3TestBase):
    """#4-14: dead-end verification boxes now offer a next step."""

    def _profile(self, user, **kwargs):
        defaults = dict(
            date_of_birth=date(1995, 1, 1),
            gender="F",
            location="Luxembourg",
            is_approved=False,
            verification_status="pending",
        )
        defaults.update(kwargs)
        return CrushProfile.objects.create(user=user, **defaults)

    def test_approved_gate_not_yet_verified_offers_entry_events_link(self):
        event = self._make_event(profile_requirement="approved")
        user = self._create_user("pending1@test.com")
        self._profile(user)
        self.client.force_login(user)

        html = self._get_detail(event)
        self.assertIn("Get verified at an entry event or with LuxID first.", html)
        self.assertIn("See entry events", html)
        self.assertIn('href="/en/events/"', html)

    def test_coach_required_offers_entry_events_link(self):
        event = self._make_event(profile_requirement="coach_assigned")
        user = self._create_user("nocoach@test.com")
        self._profile(user, is_approved=True, verification_status="verified")
        self.client.force_login(user)

        html = self._get_detail(event)
        self.assertIn("Coach required", html)
        self.assertIn("See entry events", html)

    def test_rejected_profile_offers_contact_support_link(self):
        event = self._make_event(profile_requirement="coach_assigned")
        user = self._create_user("rejected@test.com")
        from crush_lu.models.profiles import CrushCoach

        coach_user = self._create_user("coachx@test.com")
        coach = CrushCoach.objects.create(user=coach_user)
        self._profile(
            user,
            is_approved=False,
            verification_status="rejected",
            assigned_coach=coach,
        )
        self.client.force_login(user)

        html = self._get_detail(event)
        self.assertIn("Please contact support.", html)
        self.assertIn('href="mailto:support@crush.lu"', html)
        self.assertIn("Contact support", html)

    def test_rejected_profile_on_approved_gate_offers_support_not_luxid(self):
        """Codex review on #1062: LuxID refuses to verify a rejected profile,
        so the `approved`-gate branch must route it to support instead of the
        generic "not approved" branch's "Verify with LuxID" action."""
        event = self._make_event(profile_requirement="approved")
        user = self._create_user("rejected-approved@test.com")
        self._profile(
            user,
            is_approved=False,
            verification_status="rejected",
        )
        self.client.force_login(user)

        html = self._get_detail(event)
        self.assertIn("Please contact support.", html)
        self.assertIn('href="mailto:support@crush.lu"', html)
        self.assertNotIn("Verify with LuxID", html)


class ShareButtonFallbackTests(EventDetailWave3TestBase):
    """#4-18: Share stays visible and falls back to copy-link."""

    def test_share_button_is_not_unconditionally_hidden(self):
        event = self._make_event()
        html = self._get_detail(event)
        self.assertNotIn("shareBtn.style.display = 'none';", html)

    def test_share_falls_back_to_clipboard_with_toast(self):
        event = self._make_event()
        html = self._get_detail(event)
        self.assertIn("navigator.clipboard.writeText", html)
        self.assertIn("Link copied", html)

    def test_share_has_a_legacy_copy_path_and_error_toast(self):
        """Codex review on #1062: a WebView/browser with neither Web Share
        nor the async Clipboard API must not reach a silent dead end."""
        event = self._make_event()
        html = self._get_detail(event)
        self.assertIn("execCommand('copy')", html)
        self.assertIn("Could not copy the link", html)
        self.assertIn("type: 'error'", html)


class FactStripLocaleDateOrderTests(EventDetailWave3TestBase):
    """Codex review on #1062: the fact strip and sticky CTA must use
    DATE_FORMAT (locale-aware day/month order), not the literal "D, M j"
    which fixes English ordering even for DE/FR readers."""

    def test_fact_strip_uses_date_format_not_the_literal_pattern(self):
        event = self._make_event()
        html = self._get_detail(event)
        self.assertNotIn('date:"D, M j"', html)

    def test_de_page_renders_day_before_month(self):
        from django.utils import formats, translation

        event = self._make_event(date_time=timezone.now() + timedelta(days=7))
        response = self.client.get(f"/de/events/{event.id}/")
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        # German DATE_FORMAT is "j. F Y" (day, then month name): compute the
        # exact localized string the template's `date:"DATE_FORMAT"` filter
        # produces and require it verbatim, rather than scanning the whole
        # page for a bare month name (which can false-match unrelated text).
        with translation.override("de"):
            expected_date = formats.date_format(
                timezone.localtime(event.date_time), "DATE_FORMAT"
            )
        self.assertIn(expected_date, html)


class DescriptionClampGateTests(EventDetailWave3TestBase):
    """WP6 fix: the clamp class must only ever apply when a "Read more"
    toggle was also rendered to undo it — the two must share one gate."""

    def test_long_description_marks_the_wrapper_collapsible(self):
        event = self._make_event()
        html = self._get_detail(event)
        self.assertIn('data-collapsible="true"', html)
        self.assertRegex(html, r'id="event-description-text"[^>]*line-clamp-4')

    def test_short_description_is_not_marked_collapsible(self):
        event = self._make_event(description="A short blurb.")
        html = self._get_detail(event)
        self.assertNotIn("data-collapsible", html)
        self.assertNotRegex(html, r'id="event-description-text"[^>]*line-clamp-4')

    def test_toggle_only_changes_clamp_for_collapsible_descriptions(self):
        js_path = finders.find("crush_lu/js/alpine/core.js")
        with open(js_path, encoding="utf-8") as fh:
            js = fh.read()
        start = js.index('Alpine.data("eventDescriptionToggle"')
        end = js.index("Alpine.data(", start + 1)
        component_src = js[start:end]
        self.assertIn("this.collapsible = this.$el.dataset.collapsible", component_src)
        self.assertIn("if (this.collapsible)", component_src)
        self.assertIn("this.$refs.description.classList.toggle(", component_src)


class StickyCtaPaymentDueFallbackTests(EventDetailWave3TestBase):
    """WP6 fix: the sticky bar must also appear for a registered-but-unpaid
    member, whose CTA is a `.js-sumup-checkout-detail` <button>, not an
    `a.btn-crush-primary` anchor (finding: init() only ever looked for the
    anchor, so the bar silently never appeared in this state)."""

    def _register_unpaid(self, event, user):
        from crush_lu.models import EventRegistration

        return EventRegistration.objects.create(
            event=event,
            user=user,
            status="pending",
            payment_confirmed=False,
        )

    def test_sticky_bar_falls_back_to_the_pay_button_when_no_anchor_exists(self):
        event = self._make_event(registration_fee=25)
        user = self._create_user("unpaid1@test.com")
        self._register_unpaid(event, user)
        self.client.force_login(user)

        html = self._get_detail(event)
        panel_html = html.split('id="event-cta-panel"')[1].split(
            'id="event-sticky-cta"'
        )[0]
        # No btn-crush-primary anchor inside the CTA panel for this state (the
        # card Pay button is the panel's one primary, but it is a <button>,
        # which the sticky bar's `a.btn-crush-primary` lookup skips)...
        primaries = with_class(buttons(panel_html), "btn-crush-primary")
        self.assertEqual([b for b in primaries if "href" in b], [])
        self.assertEqual(len(primaries), 1)
        # ...so the sticky bar's payment button must be wired up instead.
        self.assertIn('x-show="isPayment"', html)
        self.assertIn('@click="onCtaClick"', html)
        self.assertIn("js-sumup-checkout-detail", html)

    def test_init_falls_back_to_the_card_pay_button_and_replays_its_click(self):
        js_path = finders.find("crush_lu/js/alpine/core.js")
        with open(js_path, encoding="utf-8") as fh:
            js = fh.read()
        start = js.index('Alpine.data("eventStickyCta"')
        end = js.index("Alpine.data(", start + 1)
        component_src = js[start:end]
        self.assertIn("js-sumup-checkout-detail", component_src)
        self.assertIn("this.isPayment = true", component_src)
        self.assertIn("onCtaClick: function (event) {", component_src)
        self.assertIn("real.click();", component_src)

    def test_observer_watches_the_cta_element_not_the_whole_panel(self):
        """Minor finding: the comment says the bar hides "while that anchor
        is still visible", so the IntersectionObserver must watch the anchor
        (or its payment-button fallback), not #event-cta-panel itself."""
        js_path = finders.find("crush_lu/js/alpine/core.js")
        with open(js_path, encoding="utf-8") as fh:
            js = fh.read()
        start = js.index('Alpine.data("eventStickyCta"')
        end = js.index("Alpine.data(", start + 1)
        component_src = js[start:end]
        self.assertIn("observer.observe(target);", component_src)
        self.assertNotIn("observer.observe(panel);", component_src)


class LuxidVerifyLinkTests(EventDetailWave3TestBase):
    """#4-14 headline part: the "Verify with LuxID" button must actually
    render for an unapproved member when LuxID is configured for the site.
    No test previously configured a LuxID SocialApp, so this branch of
    get_luxid_connect_url (and the anchor it feeds) was never exercised."""

    def _profile(self, user, **kwargs):
        defaults = dict(
            date_of_birth=date(1995, 1, 1),
            gender="F",
            location="Luxembourg",
            is_approved=False,
            verification_status="pending",
        )
        defaults.update(kwargs)
        return CrushProfile.objects.create(user=user, **defaults)

    def test_incomplete_profile_offers_wizard_instead_of_luxid(self):
        from unittest.mock import patch

        event = self._make_event(profile_requirement="approved")
        user = self._create_user("incomplete-luxid@test.com")
        self._profile(user, verification_status="incomplete")
        self.client.force_login(user)

        with patch(
            "crush_lu.luxid.get_luxid_connect_url",
            return_value="/accounts/luxid/login/?process=connect",
        ) as connect_url:
            html = self._get_detail(event)

        connect_url.assert_not_called()
        self.assertNotIn("Verify with LuxID", html)
        self.assertIn('href="/en/create-profile/"', html)
        self.assertIn("Finish your profile", html)

    def test_verify_with_luxid_renders_when_luxid_is_configured(self):
        from unittest.mock import patch

        event = self._make_event(profile_requirement="approved")
        user = self._create_user("pending-luxid@test.com")
        self._profile(user)
        self.client.force_login(user)

        with patch(
            "crush_lu.luxid.get_luxid_connect_url",
            return_value="/accounts/luxid/login/?process=connect",
        ):
            html = self._get_detail(event)

        self.assertIn(
            'href="/accounts/luxid/login/?process=connect"',
            html,
        )
        self.assertIn("Verify with LuxID", html)


class FactStripPremiumAwareCapacityTests(EventDetailWave3TestBase):
    """Codex review on #1062: the fact strip's "spots left" must use the
    same premium-aware capacity snapshot as the registration CTA, not
    `event.spots_remaining` (total capacity), or the two can contradict each
    other for a direct event with reserved premium seats."""

    def _profile(self, user, **kwargs):
        defaults = dict(
            date_of_birth=date(1995, 1, 1),
            gender="F",
            location="Luxembourg",
            is_approved=True,
            verification_status="verified",
        )
        defaults.update(kwargs)
        return CrushProfile.objects.create(user=user, **defaults)

    def _fill_public_capacity(self, event, count):
        from crush_lu.models import EventRegistration

        for i in range(count):
            filler = self._create_user(f"filler{i}@test.com")
            EventRegistration.objects.create(
                event=event,
                user=filler,
                status="confirmed",
                payment_confirmed=True,
            )

    def test_non_premium_viewer_sees_zero_when_only_reserved_seats_remain(self):
        # capacity 10, 2 reserved for premium, 8 confirmed: public_capacity is
        # full (8/8) even though event.spots_remaining (10-8=2) is not.
        event = self._make_event(max_participants=10, reserved_premium_seats=2)
        self._fill_public_capacity(event, 8)
        viewer = self._create_user("viewer1@test.com")
        self._profile(viewer)
        self.client.force_login(viewer)

        html = self._get_detail(event)
        self.assertNotIn("2 spots left", html)
        self.assertIn("0 spots left", html)

    def test_premium_viewer_sees_the_reserved_seats(self):
        from crush_lu.models.profiles import CrushCoach, PremiumMembership

        event = self._make_event(max_participants=10, reserved_premium_seats=2)
        self._fill_public_capacity(event, 8)
        viewer = self._create_user("viewer2@test.com")
        self._profile(viewer)
        coach_user = self._create_user("coach2@test.com")
        coach = CrushCoach.objects.create(user=coach_user)
        PremiumMembership.objects.create(user=viewer, coach=coach, status="active")
        self.client.force_login(viewer)

        html = self._get_detail(event)
        self.assertIn("2 spots left", html)


class FactStripReviewRoundTwoTests(EventDetailWave3TestBase):
    """Codex round 2 on #1062."""

    def test_one_remaining_seat_is_singular(self):
        event = self._make_event(max_participants=1)
        html = self._get_detail(event)
        self.assertIn("1 spot left", html)
        self.assertNotIn("1 spots left", html)

    def test_spots_are_hidden_once_registration_has_closed(self):
        event = self._make_event(
            registration_deadline=timezone.now() - timedelta(hours=1)
        )
        self.assertFalse(event.is_registration_accepting)
        html = self._get_detail(event)
        self.assertNotIn("spots left", html)
        self.assertNotIn("spot left", html)

    def test_sticky_cta_sits_above_nav_height_plus_safe_area(self):
        html = self._get_detail(self._make_event())
        self.assertIn(
            "bottom-[calc(var(--bottom-nav-height)+env(safe-area-inset-bottom,0px))]",
            html,
        )

    def test_copy_failure_toast_is_translated_in_french(self):
        event = self._make_event()
        response = self.client.get(f"/fr/events/{event.id}/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Impossible de copier le lien.")


class FactStripReviewRoundThreeTests(EventDetailWave3TestBase):
    """Codex round 3 on #1062."""

    def _profile(self, user, **kwargs):
        defaults = dict(
            date_of_birth=date(1995, 1, 1),
            gender="F",
            location="Luxembourg",
            is_approved=False,
            verification_status="pending",
        )
        defaults.update(kwargs)
        return CrushProfile.objects.create(user=user, **defaults)

    def test_copy_failure_toast_is_escaped_for_javascript_in_french(self):
        """The FR translation contains the apostrophe in "d'adresse", which
        breaks a single-quoted JS string literal unless escapejs is applied —
        the resulting syntax error would kill the whole event-page script, so
        the Share button would have no handler at all."""
        event = self._make_event()
        response = self.client.get(f"/fr/events/{event.id}/")
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        # escapejs is still applied (#1116 changed the copy, which no longer
        # carries "d'adresse"): its hyphen renders as \u002D in the literal.
        self.assertIn("s\u00e9lectionnez\\u002Dle ici :", html)
        self.assertNotIn("sélectionnez-le ici :'", html)

    def test_rejected_profile_without_coach_offers_support_not_entry_events(self):
        """Codex review on #1062: event_register checks verification_status
        before assigned_coach, so a rejected profile without a coach must be
        routed to support, not to the (useless) entry-events link that the
        missing-coach branch offers."""
        event = self._make_event(profile_requirement="coach_assigned")
        user = self._create_user("rejected-nocoach@test.com")
        self._profile(
            user,
            is_approved=False,
            verification_status="rejected",
            assigned_coach=None,
        )
        self.client.force_login(user)

        html = self._get_detail(event)
        self.assertIn("Please contact support.", html)
        self.assertIn('href="mailto:support@crush.lu"', html)
        self.assertNotIn("Coach required", html)

    def test_share_falls_back_to_copy_on_non_abort_share_error(self):
        """Codex review on #1062: a browser exposing navigator.share but
        rejecting it with a non-AbortError (blocked by a WebView or
        permissions policy) must still fall back to copying the link,
        not just log and leave the button dead."""
        event = self._make_event()
        html = self._get_detail(event)
        script = html.split("Web Share functionality")[1].split("</script>")[0]
        listener_block = script.split("shareBtn.addEventListener")[1]
        catch_block = listener_block.split("catch (err) {")[1].split("} else {")[0]
        self.assertIn("AbortError", catch_block)
        self.assertIn("copyLinkFallback();", catch_block)

    def test_sticky_cta_skips_language_blocked_members(self):
        """Codex review on #1062: a language-blocked member's registration
        anchor is rendered alongside the language warning even though
        event_register rejects them — the sticky bar must not target it."""
        js_path = finders.find("crush_lu/js/alpine/core.js")
        with open(js_path, encoding="utf-8") as fh:
            js = fh.read()
        start = js.index('Alpine.data("eventStickyCta"')
        end = js.index("Alpine.data(", start + 1)
        component_src = js[start:end]
        self.assertIn('querySelector("#event-language-blocked")', component_src)

        event = self._make_event(languages=["fr"])
        user = self._create_user("langblocked@test.com")
        self._profile(user, is_approved=True, verification_status="verified")
        self.client.force_login(user)

        html = self._get_detail(event)
        self.assertIn('id="event-language-blocked"', html)
        panel_html = html.split('id="event-cta-panel"')[1].split(
            'id="event-sticky-cta"'
        )[0]
        self.assertIn("btn-crush-primary", panel_html)
