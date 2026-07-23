"""One-off migration of legacy free-text ``CrushProfile.interests`` into the
structured ``interests_new`` taxonomy M2M (Event Identity redesign, spec §8.2).

Safe by default: pass ``--execute`` to write, otherwise the command only prints
a dry-run report. Legacy ``interests`` text is **never** modified — overflow
beyond the ``MAX_INTERESTS`` cap stays readable there and coach-visible.

    python manage.py migrate_interests_to_taxonomy              # dry-run report
    python manage.py migrate_interests_to_taxonomy --execute    # write interests_new

The keyword map below is the initial authored rule set covering the themes
observed in the production sample (spec §2 / §8.2.1). Refine it against the real
dry-run report before the execute run — the acceptance metric is per-profile:
≥85% of profiles with legacy interests should gain at least one mapped interest.
"""

import re
import unicodedata

from django.core.management.base import BaseCommand
from django.db import transaction

from crush_lu.models import CrushProfile, Interest

# Mirrors forms_crush_connect.MAX_INTERESTS and the Event Identity form cap.
MAX_INTERESTS = 8


def _fold(text):
    """Lowercase and strip accents so FR/DE/EN/LU surface forms compare equal."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    return text.lower().strip()


# Each rule maps a set of surface forms (any language, folded at load time) to
# one or more taxonomy slugs. A surface matches when it appears in a folded
# interest token bounded by non-alphanumerics. Order within a multi-slug rule is
# re-sorted by the taxonomy's own sort_order at match time (deterministic cap
# tie-break, spec §8.2).
_RAW_RULES = [
    # --- single interests (broadest / most common first) ---
    (["hiking", "randonnée", "wandern", "wanderung", "rando"], ["hiking"]),
    (["travel", "voyage", "reisen", "travelling", "traveling", "reise"], ["city-trips"]),
    (["music", "musique", "musik", "concert", "concerts", "konzert"], ["live-music"]),
    (["cooking", "cuisine", "kochen", "gastronomie", "cook"], ["cooking"]),
    (["baking", "backen", "pâtisserie"], ["baking"]),
    (["yoga"], ["yoga"]),
    (["cinema", "cinéma", "kino", "film", "movies", "ciné"], ["cinema"]),
    (["reading", "lesen", "lecture", "books", "livres", "read"], ["reading"]),
    (["photography", "photographie", "fotografie", "photo"], ["photography"]),
    (["football", "soccer", "fußball", "fussball"], ["football"]),
    (["running", "laufen", "course à pied", "jogging"], ["running"]),
    (["cycling", "radfahren", "vélo", "biking", "cyclisme"], ["cycling"]),
    (["swimming", "schwimmen", "natation"], ["swimming"]),
    (["fitness", "gym", "musculation", "workout"], ["fitness"]),
    (["wine", "vin", "wein"], ["wine"]),
    (["coffee", "café", "kaffee"], ["coffee"]),
    (["dining out", "restaurant", "restaurants", "essen gehen"], ["dining-out"]),
    (["museum", "museums", "musée", "museen"], ["museums"]),
    (["theatre", "theater", "théâtre"], ["theatre"]),
    (["painting", "drawing", "malen", "zeichnen", "peinture", "dessin"], ["painting"]),
    (["camping"], ["camping"]),
    (["gardening", "gärtnern", "jardinage", "garden"], ["gardening"]),
    (["climbing", "klettern", "escalade", "bouldering"], ["climbing"]),
    (["board games", "brettspiele", "jeux de société", "boardgames"], ["board-games"]),
    (["video games", "videospiele", "jeux vidéo", "gaming", "gamer"], ["video-games"]),
    (["chess", "schach", "échecs"], ["chess"]),
    (["esports", "e-sport", "esport"], ["esports"]),
    (["meditation", "méditation"], ["meditation"]),
    (["spa", "wellness"], ["spa"]),
    (["mindfulness", "achtsamkeit", "pleine conscience"], ["mindfulness"]),
    (["singing", "singen", "chant", "chanter"], ["singing"]),
    (["instrument", "guitar", "piano", "gitarre"], ["instruments"]),
    (["electronic", "techno", "electro", "elektronische"], ["electronic"]),
    (["classical", "klassik", "classique"], ["classical"]),
    # --- O3 additions ---
    (["dancing", "dance", "tanzen", "danse"], ["dancing"]),
    (["nightlife", "going out", "ausgehen", "nachtleben", "clubbing", "sortir"], ["nightlife"]),
    (["animals", "pets", "animaux", "tiere", "haustiere", "dogs", "cats"], ["animals-pets"]),
    (["cars", "motorcycles", "autos", "motorrad", "voitures", "moto"], ["cars-motorcycles"]),
    (["anime", "manga"], ["anime-manga"]),
    (["winter sports", "ski", "skiing", "snowboard", "wintersport"], ["winter-sports"]),
    (["languages", "sprachen", "langues"], ["languages"]),
    (["self-development", "personal development", "persönliche entwicklung", "développement personnel"], ["self-development"]),
    (["diy", "crafts", "bricolage", "heimwerken", "basteln", "knitting", "crochet"], ["diy-crafts"]),
    (["tech", "technology", "technik", "coding", "programming", "informatique"], ["tech"]),
    (["water sports", "surfing", "kayak", "paddle", "wassersport"], ["water-sports"]),
    (["badminton", "squash", "padel", "table tennis", "ping pong", "volleyball", "basketball", "tennis"], ["ball-racket-sports"]),
    # --- inflections & regional surface forms (from the 2026-07-23 prod
    # dry-run's unmatched sample; each is an unambiguous FR/DE/LU rendering of
    # a concept the taxonomy already has, never a new one) ---
    (["sport"], ["fitness"]),
    (["promenade", "walking", "montagne", "spazieren"], ["hiking"]),
    (["voyager", "sightseeing"], ["city-trips"]),
    (["katzen", "kaatzen", "hund", "hunde", "chien"], ["animals-pets"]),
    (["gärtneren", "jardin"], ["gardening"]),
    (["sauna"], ["spa"]),
    (["tanze"], ["dancing"]),
    (["koche"], ["cooking"]),
    (["handball"], ["ball-racket-sports"]),
    (["eishockey", "eis-hockey"], ["winter-sports"]),
    # Luxembourgish spellings — the national language, absent from the first
    # pass (DE/FR/EN only). These recur across the 2026-07-23 re-run's remaining
    # unmatched set, always as an existing concept: liesen/liersen = lesen,
    # lafen = laufen, spazéieren = spazieren, Déieren = Tiere, bastelen = basteln.
    (["liesen", "liersen"], ["reading"]),
    (["lafen"], ["running"]),
    (["spazéieren", "spazeieren"], ["hiking"]),
    (["déieren"], ["animals-pets"]),
    (["bastelen"], ["diy-crafts"]),
    # FR/PT/EN renderings of existing concepts, each from the same unmatched set.
    (["piscine", "piscina", "natation"], ["swimming"]),
    (["caminhada"], ["hiking"]),
    (["literature", "littérature"], ["reading"]),
    (["cake", "gâteau", "kuchen"], ["baking"]),
    (["photoshoot"], ["photography"]),
    (["cardio", "hiit"], ["fitness"]),
    # --- verbatim create-profile wizard category labels (spec §2) ---
    (["sports & fitness", "sport & fitness"], ["fitness"]),
    (["outdoors & travel"], ["hiking", "city-trips"]),
    (["arts & culture"], ["museums"]),
    (["food & wine"], ["dining-out", "wine"]),
    (["business & tech", "business and tech"], ["tech"]),
]

# Pre-fold surface forms and compile a bounded matcher for each. The boundaries
# are Unicode-aware (``\w``, not ``[a-z0-9]``): folding strips combining accents
# but leaves standalone letters like ``ß`` intact, so an ASCII class would treat
# them as separators — "Spaß" would match the "spa" surface form and wrongly
# pick up spa-wellness on a German profile.
#
# A trailing ``s?`` absorbs plurals, which members write constantly:
# "Randonnées", "voyages", "danses" all missed their singular surface form
# before. Measured on the 2026-07-23 production dry-run's unmatched sample, this
# alone recovers ~19% of them. Deliberately NOT ``e?s?`` — that matches the same
# sample but also turns "blue skies" into winter-sports.
_RULES = [
    (
        [re.compile(r"(?<!\w)" + re.escape(_fold(s)) + r"s?(?!\w)") for s in surfaces],
        slugs,
    )
    for surfaces, slugs in _RAW_RULES
]

_SPLIT_RE = re.compile(r"[,;/\n|]+")


def match_slugs(interests_text, sort_key):
    """Return taxonomy slugs matched in ``interests_text`` in the order they
    appear in the text. Tokens are consumed left-to-right, and within a token
    matches are ordered by their character position — so ``"yoga and hiking"``
    (no split delimiter) yields ``yoga`` before ``hiking`` rather than rule
    order. Slugs surfaced at the same position (a multi-slug rule) fall back to
    the taxonomy's ``(category, sort_order)`` tie-break (spec §8.2). This keeps
    the >8 cap honest: it retains the interests that appear earliest."""
    ordered = []
    seen = set()
    for raw_token in _SPLIT_RE.split(interests_text or ""):
        folded = _fold(raw_token)
        if not folded:
            continue
        # (position, tie-break, slug) for every rule that matches this token.
        hits = []
        for matchers, slugs in _RULES:
            matches = [m.search(folded) for m in matchers]
            positions = [mo.start() for mo in matches if mo]
            if positions:
                pos = min(positions)
                for slug in slugs:
                    hits.append((pos, sort_key.get(slug, (99, 9999)), slug))
        for _pos, _tiebreak, slug in sorted(hits):
            if slug not in seen:
                seen.add(slug)
                ordered.append(slug)
    return ordered


class Command(BaseCommand):
    help = "Migrate legacy free-text CrushProfile.interests into the interests_new taxonomy M2M."

    def add_arguments(self, parser):
        parser.add_argument(
            "--execute",
            action="store_true",
            help="Write interests_new. Without this flag the command only reports (dry-run).",
        )
        parser.add_argument(
            "--repopulate",
            action="store_true",
            help="Also process profiles that already have interests_new (default: skip them, for idempotency).",
        )

    def handle(self, *args, **options):
        execute = options["execute"]
        repopulate = options["repopulate"]

        active = Interest.objects.filter(is_active=True)
        by_slug = {i.slug: i for i in active}
        sort_key = {i.slug: (i.category, i.sort_order) for i in active}

        profiles = (
            CrushProfile.objects.exclude(interests="")
            .exclude(interests__isnull=True)
            .prefetch_related("interests_new")
        )

        total = matched = unmatched = skipped = overflow_profiles = 0
        assigned_total = 0

        for profile in profiles.iterator(chunk_size=500):
            total += 1
            if not repopulate and profile.interests_new.exists():
                skipped += 1
                continue

            slugs = match_slugs(profile.interests, sort_key)
            interests = [by_slug[s] for s in slugs if s in by_slug]
            capped = interests[:MAX_INTERESTS]
            overflow = interests[MAX_INTERESTS:]

            if not capped:
                unmatched += 1
                continue

            matched += 1
            assigned_total += len(capped)
            if overflow:
                overflow_profiles += 1
                self.stdout.write(
                    f"  overflow: profile {profile.pk} matched {len(interests)} "
                    f"→ kept {MAX_INTERESTS}, {len(overflow)} left in legacy field "
                    f"({', '.join(i.slug for i in overflow)})"
                )

            if execute:
                with transaction.atomic():
                    # Non-destructive: only add inferred interests the profile
                    # doesn't already have, up to the cap. This never removes a
                    # selection a member made through the Event Identity UI, so
                    # --repopulate (rerun after refining the keyword map) is safe.
                    # Reuse the prefetched cache; clamp room at 0 so an already-
                    # over-cap profile (reachable via the admin's unvalidated
                    # filter_horizontal widget) gets nothing added, not a
                    # negative-index slice.
                    existing_ids = {i.pk for i in profile.interests_new.all()}
                    room = max(0, MAX_INTERESTS - len(existing_ids))
                    to_add = [i for i in capped if i.pk not in existing_ids][:room]
                    if to_add:
                        profile.interests_new.add(*to_add)

        # Acceptance metric (spec §14): the share of legacy-interest profiles
        # that end up with ≥1 taxonomy interest. Already-populated ("skipped")
        # profiles succeeded on a prior run, so they count toward the rate —
        # otherwise a dry-run after --execute would report a misleading 0%.
        with_interest = matched + skipped
        rate = (with_interest / total * 100) if total else 0.0
        mode = "EXECUTE" if execute else "DRY-RUN"
        self.stdout.write("")
        self.stdout.write(f"[{mode}] profiles with legacy interests : {total}")
        self.stdout.write(f"          with ≥1 taxonomy interest      : {with_interest}  ({rate:.1f}%)")
        self.stdout.write(f"          newly matched this run         : {matched}")
        self.stdout.write(f"          unmatched (no rule hit)        : {unmatched}")
        self.stdout.write(f"          skipped (already populated)    : {skipped}")
        self.stdout.write(f"          profiles with overflow (>8)    : {overflow_profiles}")
        self.stdout.write(f"          taxonomy interests assigned    : {assigned_total}")
        if not execute:
            self.stdout.write(
                self.style.WARNING("Dry-run only — no writes. Re-run with --execute to apply.")
            )
        else:
            self.stdout.write(self.style.SUCCESS("Done. Legacy interests text left untouched."))
