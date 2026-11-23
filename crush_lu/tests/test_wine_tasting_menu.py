"""Wine Tasting event type: guests pick a tasting formula at sign-up.

The formula is paid on site to the venue (Domaines Vinsmoselle), never through
crush.lu; the venue invoices from the per-guest list. Spec:
ai-memory-hub/specs/2026-11-18-crush-wine-tasting-event-type.md
"""

import csv
import io
from decimal import Decimal

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory, TestCase

from crush_lu.admin.events import EventRegistrationAdminForm, MeetupEventAdmin
from crush_lu.forms import EventRegistrationForm
from crush_lu.models import EventRegistration, MeetupEvent
from crush_lu.models.events import EventMenuOption, format_menu_price
from crush_lu.models.payments import PaymentTransaction
from crush_lu.tests.test_registration_idempotency import RegistrationFixtures
from crush_lu.views_coach import _menu_summary
from hub.models import OFFER_EVENT_TYPE_CHOICES


class WineTastingFixtures(RegistrationFixtures):
    def wine_event(self, **overrides):
        values = {
            "event_type": "wine_tasting",
            "location": "Caves de Remerschen",
            "max_participants": 20,
            "has_food_component": False,
        }
        values.update(overrides)
        event = self.event(**values)
        self.discovery = EventMenuOption.objects.create(
            event=event,
            name="Discovery tasting",
            description="4 wines + 1 Crémant",
            price=Decimal("12.00"),
            sort_order=1,
        )
        self.wine_lover = EventMenuOption.objects.create(
            event=event,
            name="Wine Lover tasting",
            description="7 wines + 1 Crémant",
            price=Decimal("16.00"),
            sort_order=2,
        )
        self.retired = EventMenuOption.objects.create(
            event=event, name="Retired", price=Decimal("8"), is_active=False
        )
        return event


class EventTypeChoiceTests(TestCase):
    def test_wine_tasting_is_an_event_type(self):
        self.assertIn(("wine_tasting", "Wine Tasting"), MeetupEvent.EVENT_TYPE_CHOICES)

    def test_hub_offer_choices_stay_in_step(self):
        self.assertEqual(OFFER_EVENT_TYPE_CHOICES, MeetupEvent.EVENT_TYPE_CHOICES)


class PriceFormatTests(TestCase):
    def test_whole_euros_have_no_decimals_or_exponent(self):
        self.assertEqual(format_menu_price(Decimal("12.00")), "12")
        self.assertEqual(format_menu_price(Decimal("10.00")), "10")

    def test_cents_are_kept(self):
        self.assertEqual(format_menu_price(Decimal("12.5")), "12.50")

    def test_none_is_blank(self):
        self.assertEqual(format_menu_price(None), "")


class MenuFormTests(WineTastingFixtures, TestCase):
    def test_form_offers_only_active_options_in_order(self):
        event = self.wine_event()
        form = EventRegistrationForm(event=event)
        self.assertEqual(
            list(form.fields["menu_option"].queryset),
            [self.discovery, self.wine_lover],
        )

    def test_choice_is_required(self):
        event = self.wine_event()
        form = EventRegistrationForm({}, event=event)
        self.assertFalse(form.is_valid())
        self.assertIn("menu_option", form.errors)

    def test_inactive_option_is_rejected(self):
        event = self.wine_event()
        form = EventRegistrationForm({"menu_option": self.retired.pk}, event=event)
        self.assertFalse(form.is_valid())
        self.assertIn("menu_option", form.errors)

    def test_event_without_options_has_no_menu_field(self):
        event = self.event(event_type="wine_tasting")
        self.assertNotIn("menu_option", EventRegistrationForm(event=event).fields)


class MinimumFormulaTests(WineTastingFixtures, TestCase):
    """One formula can be the minimum to take part (Vinsmoselle, Dec 2026).

    Cheaper formulas drop out of the sign-up form unless exempt, so an
    alcohol-free tasting can stay open to drivers and non-drinkers.
    """

    def minimum_event(self):
        event = self.wine_event()
        self.wine_lover.is_minimum = True
        self.wine_lover.save()
        self.cremant = EventMenuOption.objects.create(
            event=event, name="Crémant tasting", price=Decimal("14"), sort_order=3
        )
        self.alcohol_free = EventMenuOption.objects.create(
            event=event,
            name="Alcohol-free tasting",
            price=Decimal("6"),
            sort_order=4,
            exempt_from_minimum=True,
        )
        return event

    def test_without_minimum_every_active_option_is_offered(self):
        event = self.wine_event()
        self.assertIsNone(event.minimum_menu_option)
        self.assertEqual(
            list(event.signup_menu_options), [self.discovery, self.wine_lover]
        )

    def test_cheaper_options_drop_out_except_exempt(self):
        event = self.minimum_event()
        form = EventRegistrationForm(event=event)
        self.assertEqual(
            list(form.fields["menu_option"].queryset),
            [self.wine_lover, self.alcohol_free],
        )

    def test_minimum_is_preselected(self):
        event = self.minimum_event()
        form = EventRegistrationForm(event=event)
        self.assertEqual(form.fields["menu_option"].initial, self.wine_lover.pk)

    def test_cheaper_option_is_rejected(self):
        event = self.minimum_event()
        form = EventRegistrationForm({"menu_option": self.discovery.pk}, event=event)
        self.assertFalse(form.is_valid())
        self.assertIn("menu_option", form.errors)

    def test_inactive_minimum_is_ignored(self):
        event = self.minimum_event()
        self.wine_lover.is_active = False
        self.wine_lover.save()
        self.assertIsNone(event.minimum_menu_option)
        self.assertIn(self.discovery, event.signup_menu_options)

    def test_showcase_features_the_minimum(self):
        event = self.minimum_event()
        showcase = event.menu_showcase
        self.assertEqual(showcase["featured"], self.wine_lover)
        self.assertEqual(showcase["minimum"], self.wine_lover)
        self.assertEqual(showcase["others"], [self.alcohol_free])

    def test_signup_with_exempt_option_succeeds(self):
        event = self.minimum_event()
        self.register(self.client_for(), event, menu_option=self.alcohol_free.pk)
        reg = EventRegistration.objects.get(event=event, user=self.user)
        self.assertEqual(reg.menu_option, self.alcohol_free)

    def test_signup_with_cheaper_option_creates_nothing(self):
        event = self.minimum_event()
        response = self.register(
            self.client_for(), event, menu_option=self.discovery.pk
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            EventRegistration.objects.filter(event=event, user=self.user).exists()
        )

    def test_event_page_says_required(self):
        event = self.minimum_event()
        response = self.client_for().get(f"/en/events/{event.pk}/")
        self.assertContains(response, "Required to take part")
        self.assertContains(response, "is the minimum")
        self.assertNotContains(response, "Discovery tasting")
        self.assertNotContains(response, "Crémant tasting")
        self.assertContains(response, "Alcohol-free tasting")


class MenuRegistrationTests(WineTastingFixtures, TestCase):
    def test_signup_stores_choice_and_snapshot(self):
        event = self.wine_event()
        response = self.register(
            self.client_for(), event, menu_option=self.wine_lover.pk
        )
        self.assertEqual(response.status_code, 302)
        reg = EventRegistration.objects.get(event=event, user=self.user)
        self.assertEqual(reg.menu_option, self.wine_lover)
        self.assertEqual(reg.menu_option_name, "Wine Lover tasting")
        self.assertEqual(reg.menu_option_price, Decimal("16.00"))

    def test_signup_without_choice_creates_nothing(self):
        event = self.wine_event()
        response = self.register(self.client_for(), event)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            EventRegistration.objects.filter(event=event, user=self.user).exists()
        )

    def test_option_from_another_event_is_rejected(self):
        event = self.wine_event()
        other = self.event(title="Other", event_type="wine_tasting")
        foreign = EventMenuOption.objects.create(
            event=other, name="Foreign", price=Decimal("5")
        )
        self.register(self.client_for(), event, menu_option=foreign.pk)
        self.assertFalse(
            EventRegistration.objects.filter(event=event, user=self.user).exists()
        )

    def test_reregistration_overwrites_the_cancelled_choice(self):
        event = self.wine_event()
        EventRegistration.objects.create(
            event=event,
            user=self.user,
            status="cancelled",
            menu_option=self.discovery,
            menu_option_name="Discovery tasting",
            menu_option_price=Decimal("12"),
        )
        self.register(self.client_for(), event, menu_option=self.wine_lover.pk)
        reg = EventRegistration.objects.get(event=event, user=self.user)
        self.assertNotEqual(reg.status, "cancelled")
        self.assertEqual(reg.menu_option, self.wine_lover)
        self.assertEqual(reg.menu_option_name, "Wine Lover tasting")

    def test_snapshot_survives_option_edit_and_delete(self):
        event = self.wine_event()
        self.register(self.client_for(), event, menu_option=self.discovery.pk)
        self.discovery.price = Decimal("99")
        self.discovery.save()
        self.discovery.delete()
        reg = EventRegistration.objects.get(event=event, user=self.user)
        self.assertIsNone(reg.menu_option)
        self.assertEqual(reg.menu_option_name, "Discovery tasting")
        self.assertEqual(reg.menu_option_price, Decimal("12.00"))

    def test_formula_is_never_charged_through_crush(self):
        """A free Crush seat stays free: the formula price is the venue's."""
        event = self.wine_event(registration_fee=Decimal("0"))
        self.register(self.client_for(), event, menu_option=self.wine_lover.pk)
        reg = EventRegistration.objects.get(event=event, user=self.user)
        self.assertEqual(reg.status, "confirmed")
        self.assertFalse(PaymentTransaction.objects.exists())

    def test_paid_crush_seat_is_pending_for_the_crush_fee_only(self):
        event = self.wine_event(registration_fee=Decimal("10"))
        self.register(self.client_for(), event, menu_option=self.wine_lover.pk)
        reg = EventRegistration.objects.get(event=event, user=self.user)
        self.assertEqual(reg.status, "pending")
        self.assertEqual(reg.menu_option_price, Decimal("16.00"))

    def test_event_page_lists_formulas_and_venue_payment(self):
        event = self.wine_event()
        response = self.client_for().get(f"/en/events/{event.pk}/")
        self.assertContains(response, "Discovery tasting")
        self.assertContains(response, "Wine Lover tasting")
        self.assertNotContains(response, "Retired")
        self.assertContains(response, "Caves de Remerschen")

    def test_register_page_renders_the_picker(self):
        event = self.wine_event()
        response = self.client_for().get(f"/en/events/{event.pk}/register/")
        self.assertContains(response, 'name="menu_option"', count=2)
        self.assertContains(response, "16 €")


class VenueExportTests(WineTastingFixtures, TestCase):
    def setUp(self):
        super().setUp()
        self.admin_user = get_user_model().objects.create_superuser(
            "boss", "boss@example.com", "pw"
        )

    def _request(self):
        request = RequestFactory().post("/admin/")
        request.user = self.admin_user
        request.session = {}
        request._messages = FallbackStorage(request)
        return request

    def test_venue_csv_lists_guests_formulas_and_totals_without_contacts(self):
        event = self.wine_event()
        guest = self.member("guest")
        guest.first_name, guest.last_name = "Anna", "Muller"
        guest.save()
        self.user.first_name, self.user.last_name = "Ben", "Schmit"
        self.user.save()
        for user, option in ((guest, self.discovery), (self.user, self.wine_lover)):
            EventRegistration.objects.create(
                event=event,
                user=user,
                status="confirmed",
                menu_option=option,
                menu_option_name=option.name,
                menu_option_price=option.price,
            )
        EventRegistration.objects.create(
            event=event,
            user=self.member("gone"),
            status="cancelled",
            menu_option=self.discovery,
            menu_option_name="Discovery tasting",
            menu_option_price=Decimal("12"),
        )
        admin = MeetupEventAdmin(MeetupEvent, AdminSite())
        response = admin.export_venue_menu_csv(
            self._request(), MeetupEvent.objects.filter(pk=event.pk)
        )
        body = response.content.decode("utf-8-sig")
        rows = list(csv.reader(io.StringIO(body), delimiter=";"))
        self.assertIn(["1", "Anna", "Muller", "Discovery tasting", "12"], rows)
        self.assertIn(["2", "Ben", "Schmit", "Wine Lover tasting", "16"], rows)
        self.assertIn(["TOTAL", "2", "28"], rows)
        self.assertNotIn("@example.com", body)

    def test_attendee_csv_has_formula_columns(self):
        event = self.wine_event()
        EventRegistration.objects.create(
            event=event,
            user=self.user,
            status="confirmed",
            menu_option=self.discovery,
            menu_option_name="Discovery tasting",
            menu_option_price=Decimal("12.00"),
        )
        admin = MeetupEventAdmin(MeetupEvent, AdminSite())
        response = admin.export_attendees_csv(
            self._request(), MeetupEvent.objects.filter(pk=event.pk)
        )
        rows = list(csv.reader(io.StringIO(response.content.decode())))
        self.assertIn("Menu / Formula", rows[0])
        self.assertIn("Discovery tasting", rows[1])
        self.assertIn("12", rows[1])


class AdminFormulaChangeTests(WineTastingFixtures, TestCase):
    def _form(self, reg, **changes):
        data = {
            "event": reg.event_id,
            "user": reg.user_id,
            "status": reg.status,
            "menu_option": reg.menu_option_id or "",
            "registered_at": reg.registered_at,
            "special_requests": "",
            "accessibility_needs": "",
        }
        data.update(changes)
        form = EventRegistrationAdminForm(data, instance=reg)
        # Only the fields this test cares about need to be bound.
        form.fields = {k: v for k, v in form.fields.items() if k in data}
        return form

    def test_staff_change_refreshes_snapshot(self):
        event = self.wine_event()
        reg = EventRegistration.objects.create(
            event=event,
            user=self.user,
            status="confirmed",
            menu_option=self.discovery,
            menu_option_name="Discovery tasting",
            menu_option_price=Decimal("12"),
        )
        form = self._form(reg, menu_option=self.wine_lover.pk)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        reg.refresh_from_db()
        self.assertEqual(reg.menu_option_name, "Wine Lover tasting")
        self.assertEqual(reg.menu_option_price, Decimal("16.00"))

    def test_staff_cannot_pick_another_events_formula(self):
        event = self.wine_event()
        other = self.event(title="Other", event_type="wine_tasting")
        foreign = EventMenuOption.objects.create(
            event=other, name="Foreign", price=Decimal("5")
        )
        reg = EventRegistration.objects.create(
            event=event, user=self.user, status="confirmed"
        )
        form = self._form(reg, menu_option=foreign.pk)
        self.assertFalse(form.is_valid())
        self.assertIn("menu_option", form.errors)


class CoachSummaryTests(TestCase):
    def test_summary_counts_and_totals_per_formula(self):
        regs = [
            EventRegistration(menu_option_name="A", menu_option_price=Decimal("12")),
            EventRegistration(menu_option_name="B", menu_option_price=Decimal("16")),
            EventRegistration(menu_option_name="A", menu_option_price=Decimal("12")),
            EventRegistration(menu_option_name=""),
        ]
        self.assertEqual(
            _menu_summary(regs),
            [("A", 2, Decimal("24")), ("B", 1, Decimal("16"))],
        )


class MenuSurfaceRenderTests(WineTastingFixtures, TestCase):
    """Surfaces the Crush-fee path (status "pending") actually exercises."""

    def _pending_registration(self, event):
        return EventRegistration.objects.create(
            event=event,
            user=self.user,
            status="pending",
            menu_option=self.wine_lover,
            menu_option_name="Wine Lover tasting",
            menu_option_price=Decimal("16.00"),
        )

    def test_htmx_success_partial_shows_formula(self):
        event = self.wine_event(registration_fee=Decimal("10"))
        response = self.client_for().post(
            f"/en/events/{event.pk}/register/",
            {"menu_option": self.wine_lover.pk},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Wine Lover tasting — 16 €")
        self.assertContains(response, "Caves de Remerschen")

    def test_htmx_error_rerender_keeps_the_picker(self):
        event = self.wine_event()
        response = self.client_for().post(
            f"/en/events/{event.pk}/register/", {}, HTTP_HX_REQUEST="true"
        )
        self.assertContains(response, 'name="menu_option"')
        self.assertContains(response, "Please choose your formula.")

    def test_payment_pending_email_shows_formula(self):
        from django.template.loader import render_to_string

        event = self.wine_event(registration_fee=Decimal("10"))
        reg = self._pending_registration(event)
        html = render_to_string(
            "crush_lu/emails/event_payment_pending.html",
            {"registration": reg, "event": event, "event_url": "/x/"},
        )
        self.assertIn("Wine Lover tasting — 16 €", html)
        self.assertIn("Paid on site directly to Caves de Remerschen", html)

    def test_reminder_and_confirmation_emails_show_formula(self):
        from django.template.loader import render_to_string

        event = self.wine_event()
        reg = self._pending_registration(event)
        for name in ("event_reminder", "event_registration_confirmation"):
            html = render_to_string(
                f"crush_lu/emails/{name}.html",
                {
                    "registration": reg,
                    "event": event,
                    "event_url": "/x/",
                    "days_until_event": 3,
                },
            )
            self.assertIn("Wine Lover tasting", html, name)

    def test_coach_page_shows_formula_and_summary(self):
        from crush_lu.models import CrushCoach

        event = self.wine_event()
        self._pending_registration(event)
        coach = self.member("coachy")
        CrushCoach.objects.create(user=coach, is_active=True)
        response = self.client_for(coach).get(
            f"/en/coach/events/{event.pk}/", {"status": "all"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "🍷 Wine Lover tasting — 16 €")
        self.assertContains(response, "Guests per tasting formula")


class MenuShowcaseTests(WineTastingFixtures, TestCase):
    """The single most expensive active formula is promoted on the event page."""

    def test_most_expensive_active_formula_is_featured(self):
        event = self.wine_event()
        showcase = event.menu_showcase
        self.assertEqual(showcase["featured"], self.wine_lover)
        self.assertEqual(showcase["others"], [self.discovery])
        self.assertEqual(showcase["count"], 2)

    def test_inactive_formula_is_never_featured(self):
        event = self.wine_event()
        self.retired.price = Decimal("99")
        self.retired.save()
        self.assertEqual(event.menu_showcase["featured"], self.wine_lover)

    def test_tie_for_top_price_features_nothing(self):
        event = self.wine_event()
        self.discovery.price = self.wine_lover.price
        self.discovery.save()
        showcase = event.menu_showcase
        self.assertIsNone(showcase["featured"])
        self.assertEqual(len(showcase["others"]), 2)

    def test_single_formula_is_not_featured(self):
        event = self.wine_event()
        self.discovery.is_active = False
        self.discovery.save()
        showcase = event.menu_showcase
        self.assertIsNone(showcase["featured"])
        self.assertEqual(showcase["others"], [self.wine_lover])

    def test_event_page_shows_wine_ghost_on_featured_card(self):
        event = self.wine_event()
        response = self.client_for().get(f"/en/events/{event.pk}/")
        self.assertContains(response, "The ghost's favourite", count=1)
        self.assertContains(response, "wine-ghost-bowl")

    def test_register_picker_badges_the_featured_formula(self):
        event = self.wine_event()
        response = self.client_for().get(f"/en/events/{event.pk}/register/")
        self.assertContains(response, "The ghost's favourite")
