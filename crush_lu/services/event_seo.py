"""Search-result wording for a public event page (``/events/<id>/``).

The ``<title>`` used to be the creative event name alone ("A winemaker, a glass
and you") and the meta description ended in English filler on the German and
French pages too. People search for a format and a place ("speed dating
luxembourg", "soirée célibataire luxembourg"), so the title leads with those
and the date. The creative name stays the page heading, the share title and
the structured-data ``name``.

Two rules every string here follows:

* **The place is the canton, never the town or the venue.** The event page
  shows anonymous visitors the canton instead of the address
  (``MeetupEvent.canton`` is a privacy control), and a search engine is an
  anonymous visitor. The canton "Luxembourg" and a blank canton read as the
  country, which is also the word people type.
* **Everything is translated at call time**, in the language of the request.
  The wording lives in the ``event search`` message contexts so it can be
  tuned in the ``.po`` files without touching the copy on the page itself.
"""

from django.utils import timezone
from django.utils.formats import date_format
from django.utils.html import strip_tags
from django.utils.text import Truncator
from django.utils.translation import get_language, pgettext

# The canton that is also the country's name. Naming it reads as "in
# Luxembourg", so it takes the same wording as an event with no canton.
COUNTRY_CANTON = "Luxembourg"

# Words of the event description kept after the facts. Google shows about 155
# characters; the facts (format, place, date, time) come first so they survive
# the cut, and the rest is the organiser's own teaser.
TEASER_WORDS = 20

CTX_FORMAT = "event search format"
CTX_PLACE = "event search place"
CTX_TEXT = "event search text"
CTX_DATE = "event search date format"


def _format_phrase(event):
    """The event format in the words people search for it."""
    phrases = {
        "speed_dating": pgettext(CTX_FORMAT, "Speed dating"),
        "mixer": pgettext(CTX_FORMAT, "Social mixer for singles"),
        "activity": pgettext(CTX_FORMAT, "Activity meetup for singles"),
        "themed": pgettext(CTX_FORMAT, "Themed night for singles"),
        "quiz_night": pgettext(CTX_FORMAT, "Quiz night for singles"),
        "crush_cache": pgettext(CTX_FORMAT, "Scavenger hunt for singles"),
        "wine_tasting": pgettext(CTX_FORMAT, "Wine tasting for singles"),
    }
    return phrases.get(event.event_type) or pgettext(CTX_FORMAT, "Singles event")


def _place_phrase(event, with_country=False):
    """ "in Remich", or "in Luxembourg" for the country-named or blank canton.

    ``with_country`` adds the country behind a canton for the description, so
    a page about an evening in Remich still says "Luxembourg" once.
    """
    canton = (event.canton or "").strip()
    if not canton or canton == COUNTRY_CANTON:
        return pgettext(CTX_PLACE, "in Luxembourg")
    # The translated canton name ("Clerf", "Redingen"); a legacy free-text
    # value that is not one of the twelve choices comes back unchanged.
    place = event.get_canton_display()
    if with_country:
        return pgettext(CTX_PLACE, "in %(place)s (Luxembourg)") % {"place": place}
    return pgettext(CTX_PLACE, "in %(place)s") % {"place": place}


def _format_date(value, fmt):
    """``date_format`` in the request language, French in lower case.

    This app's French catalogue capitalises month and weekday names
    ("Décembre", "Samedi") for the form dropdowns that list them, and
    ``date_format`` reads the same catalogue. Running French text writes them
    in lower case, and these strings hold nothing but a date.
    """
    text = date_format(value, fmt)
    if (get_language() or "").startswith("fr"):
        text = text.lower()
    return text


def event_search_headline(event):
    """Format and place: "Wine tasting for singles in Remich"."""
    return pgettext(CTX_TEXT, "%(format)s %(place)s") % {
        "format": _format_phrase(event),
        "place": _place_phrase(event),
    }


def event_search_title(event, now=None):
    """The ``<title>`` text, without the site name.

    "Wine tasting for singles in Remich, 5 December". The year is added only
    when the event is not in the current year, so the page of a past event
    does not read as an upcoming one.
    """
    start = timezone.localtime(event.date_time)
    today = timezone.localtime(now or timezone.now())
    if start.year == today.year:
        fmt = pgettext(CTX_DATE, "j F")
    else:
        fmt = pgettext(CTX_DATE, "j F Y")
    return pgettext(CTX_TEXT, "%(headline)s, %(date)s") % {
        "headline": event_search_headline(event),
        "date": _format_date(start, fmt),
    }


def _teaser(event):
    """The start of the description as one line of plain text."""
    text = " ".join(strip_tags(event.description or "").split())
    # Some descriptions open by repeating the event name; the page already
    # carries it in the heading and the share title.
    title = " ".join((event.title or "").split())
    if title and text.startswith(title):
        text = text[len(title) :].lstrip(" -–—|:·.,")
    return Truncator(text).words(TEASER_WORDS, truncate="…")


def event_search_description(event):
    """The meta description: the facts first, then the organiser's teaser.

    "Wine tasting for singles in Remich (Luxembourg) on Saturday 5 December
    2026 at 7 p.m. There are evenings when conversations come by themselves…"
    """
    start = timezone.localtime(event.date_time)
    facts = pgettext(CTX_TEXT, "%(format)s %(place)s on %(date)s at %(time)s.") % {
        "format": _format_phrase(event),
        "place": _place_phrase(event, with_country=True),
        "date": _format_date(start, pgettext(CTX_DATE, "l j F Y")),
        "time": date_format(start, "TIME_FORMAT"),
    }
    # Django's English time already ends in a full stop ("7 p.m."), and the
    # sentence brings its own.
    if facts.endswith(".."):
        facts = facts[:-1]
    teaser = _teaser(event)
    return f"{facts} {teaser}" if teaser else facts
