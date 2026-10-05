"""Turn a ``PartnerOffer`` into the field values for a new ``MeetupEvent``.

Pure functions, no HTTP: the hub API returns this as a draft today and the
crush_lu coach event form will call it to prefill. Keys are ``MeetupEvent``
field names (snake_case) on purpose, so a caller can feed them straight to the
event form; the date, deadline and publish state are left to the coach.

Spec: ai-memory-hub/specs/2026-10-01-hub-partner-and-offers.md
"""

from .models import PartnerOffer

OFFER_LANGUAGES = ("en", "de", "fr")


def _text(value):
    return value if value is not None else ""


def build_event_prefill(offer: PartnerOffer) -> dict:
    """``MeetupEvent`` field values taken from ``offer`` and its partner."""
    partner = offer.location
    fields = {
        "partner": partner.pk,
        "offer": offer.pk,
        "event_type": offer.event_type,
        # The venue name as it is shown on the event page.
        "location": partner.name,
        "address_street": partner.address_street,
        "address_number": partner.address_number,
        "address_postcode": partner.address_postcode,
        "address_town": partner.address_town or partner.city,
        "address": partner.address,
        "canton": partner.canton,
        "latitude": partner.latitude,
        "longitude": partner.longitude,
        "duration_minutes": offer.duration_minutes,
        "max_participants": offer.max_participants,
        "max_participants_m": offer.max_participants_m,
        "max_participants_f": offer.max_participants_f,
        "max_participants_nb": offer.max_participants_nb,
        "min_age": offer.min_age,
        "max_age": offer.max_age,
        "registration_fee": offer.registration_fee,
        "has_food_component": offer.has_food_component,
        "allow_plus_ones": offer.allow_plus_ones,
        "languages": list(offer.languages),
    }
    for lang in OFFER_LANGUAGES:
        fields[f"title_{lang}"] = _text(getattr(offer, f"title_{lang}", None))
        fields[f"description_{lang}"] = _text(
            getattr(offer, f"description_{lang}", None)
        )
    return {
        "offerId": offer.pk,
        "partnerId": partner.pk,
        "suggestedWeekdays": list(offer.weekdays),
        "suggestedStartTime": (
            offer.start_time.strftime("%H:%M") if offer.start_time else None
        ),
        "fields": fields,
    }
