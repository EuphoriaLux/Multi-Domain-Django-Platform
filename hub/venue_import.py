"""Luxembourg restaurant & bar directory extraction and idempotent Hub import.

Pulls hospitality venues from OpenStreetMap (Overpass API) and links/imports
venues from the echo.lu venue registry into ``hub.Location`` with
``partnership_stage="Prospect"`` so Crush Coaches can browse and claim venues
without double-prospecting.

Spec: ai-memory-hub/specs/2026-11-12-crush-hub-venue-extraction-and-claiming.md
"""

from decimal import Decimal, InvalidOperation
import json
import logging
import re
import unicodedata
from urllib.parse import urlparse

import requests
from django.db import transaction
from django.db.models import Q

from crush_lu.models.echo_lu import EchoVenue
from crush_lu.services import echo_lu
from crush_lu.services.echo_lu_postcodes import POSTCODE_TO_COMMUNE

from .models import Location, LocationContact

logger = logging.getLogger(__name__)

DEFAULT_IMPORTED_CAPACITY = 40

OVERPASS_ENDPOINTS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)

OVERPASS_LUXEMBOURG_QUERY = """
[out:json][timeout:90];
area["ISO3166-1"="LU"]->.searchArea;
(
  node["amenity"~"^(restaurant|bar|pub|cafe|biergarten|nightclub)$"]["name"](area.searchArea);
  way["amenity"~"^(restaurant|bar|pub|cafe|biergarten|nightclub)$"]["name"](area.searchArea);
  relation["amenity"~"^(restaurant|bar|pub|cafe|biergarten|nightclub)$"]["name"](area.searchArea);
);
out center tags;
""".strip()


def _fold(text: str) -> str:
    """Lowercase ASCII-folded key for matching commune and locality names."""
    normalized = unicodedata.normalize("NFKD", (text or "").strip())
    ascii_only = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", ascii_only.lower()).strip()


# Maps folded Luxembourg commune & major locality names to the 12 official
# cantons in ``crush_lu.models.events.CANTON_CHOICES``.
_RAW_COMMUNE_TO_CANTON = {
    # Canton Capellen
    "dippach": "Capellen",
    "garnich": "Capellen",
    "habscht": "Capellen",
    "hobscheid": "Capellen",
    "septfontaines": "Capellen",
    "kaerjeng": "Capellen",
    "bascharage": "Capellen",
    "clemency": "Capellen",
    "kehlen": "Capellen",
    "koerich": "Capellen",
    "kopstal": "Capellen",
    "mamer": "Capellen",
    "capellen": "Capellen",
    "steinfort": "Capellen",
    # Canton Clervaux
    "clervaux": "Clervaux",
    "parc hosingen": "Clervaux",
    "hosingen": "Clervaux",
    "troisvierges": "Clervaux",
    "weiswampach": "Clervaux",
    "wincrange": "Clervaux",
    # Canton Diekirch
    "bettendorf": "Diekirch",
    "bourscheid": "Diekirch",
    "diekirch": "Diekirch",
    "erpeldange sur sure": "Diekirch",
    "erpeldange": "Diekirch",
    "ettelbruck": "Diekirch",
    "feulen": "Diekirch",
    "mertzig": "Diekirch",
    "reisdorf": "Diekirch",
    "schieren": "Diekirch",
    "vallee de l ernz": "Diekirch",
    "medernach": "Diekirch",
    # Canton Echternach
    "beaufort": "Echternach",
    "bech": "Echternach",
    "berdorf": "Echternach",
    "consdorf": "Echternach",
    "echternach": "Echternach",
    "mompach": "Echternach",
    "rosport mompach": "Echternach",
    "rosport": "Echternach",
    "waldbillig": "Echternach",
    # Canton Esch-sur-Alzette
    "bettembourg": "Esch-sur-Alzette",
    "differdange": "Esch-sur-Alzette",
    "oberkorn": "Esch-sur-Alzette",
    "niederkorn": "Esch-sur-Alzette",
    "dudelange": "Esch-sur-Alzette",
    "esch sur alzette": "Esch-sur-Alzette",
    "esch alzette": "Esch-sur-Alzette",
    "esch": "Esch-sur-Alzette",
    "belval": "Esch-sur-Alzette",
    "frisange": "Esch-sur-Alzette",
    "kayl": "Esch-sur-Alzette",
    "tetange": "Esch-sur-Alzette",
    "leudelange": "Esch-sur-Alzette",
    "mondercange": "Esch-sur-Alzette",
    "petange": "Esch-sur-Alzette",
    "rodange": "Esch-sur-Alzette",
    "lamadelaine": "Esch-sur-Alzette",
    "reckange sur mess": "Esch-sur-Alzette",
    "roeser": "Esch-sur-Alzette",
    "rumelange": "Esch-sur-Alzette",
    "sanem": "Esch-sur-Alzette",
    "belvaux": "Esch-sur-Alzette",
    "soleuvre": "Esch-sur-Alzette",
    "ehnen": "Grevenmacher",
    "schifflange": "Esch-sur-Alzette",
    # Canton Grevenmacher
    "betzdorf": "Grevenmacher",
    "biwer": "Grevenmacher",
    "flaxweiler": "Grevenmacher",
    "grevenmacher": "Grevenmacher",
    "junglinster": "Grevenmacher",
    "gonderange": "Grevenmacher",
    "manternach": "Grevenmacher",
    "mertert": "Grevenmacher",
    "wasserbillig": "Grevenmacher",
    "wormeldange": "Grevenmacher",
    # Canton Luxembourg
    "bertrange": "Luxembourg",
    "contern": "Luxembourg",
    "hesperange": "Luxembourg",
    "howald": "Luxembourg",
    "alzingen": "Luxembourg",
    "fentange": "Luxembourg",
    "itzig": "Luxembourg",
    "luxembourg": "Luxembourg",
    "luxembourg ville": "Luxembourg",
    "luxembourg city": "Luxembourg",
    "kirchberg": "Luxembourg",
    "clausen": "Luxembourg",
    "grund": "Luxembourg",
    "pfaffenthal": "Luxembourg",
    "bonnevoie": "Luxembourg",
    "hollerich": "Luxembourg",
    "gare": "Luxembourg",
    "limpertsberg": "Luxembourg",
    "belair": "Luxembourg",
    "merl": "Luxembourg",
    "gasperich": "Luxembourg",
    "cessange": "Luxembourg",
    "hamm": "Luxembourg",
    "beggen": "Luxembourg",
    "dommeldange": "Luxembourg",
    "echerange": "Luxembourg",
    " Eich": "Luxembourg",
    "niederanven": "Luxembourg",
    "senningerberg": "Luxembourg",
    "sandweiler": "Luxembourg",
    "schuttrange": "Luxembourg",
    "munsbach": "Luxembourg",
    "steinsel": "Luxembourg",
    "strassen": "Luxembourg",
    "walferdange": "Luxembourg",
    "bereldange": "Luxembourg",
    "helmsange": "Luxembourg",
    "weiler la tour": "Luxembourg",
    # Canton Mersch
    "bissen": "Mersch",
    "colmar berg": "Mersch",
    "fischbach": "Mersch",
    "heffingen": "Mersch",
    "helperknapp": "Mersch",
    "boevange sur attert": "Mersch",
    "tuntange": "Mersch",
    "larochette": "Mersch",
    "lintgen": "Mersch",
    "lorentzweiler": "Mersch",
    "mersch": "Mersch",
    "nommern": "Mersch",
    # Canton Redange
    "beckerich": "Redange",
    "ell": "Redange",
    "grosbous": "Redange",
    "groussbus wal": "Redange",
    "wahl": "Redange",
    "preizerdaul": "Redange",
    "bettborn": "Redange",
    "rambrouch": "Redange",
    "redange": "Redange",
    "redange attert": "Redange",
    "redange sur attert": "Redange",
    "saeul": "Redange",
    "useldange": "Redange",
    "vichten": "Redange",
    # Canton Remich
    "bous": "Remich",
    "bous waldbredimus": "Remich",
    "waldbredimus": "Remich",
    "dalheim": "Remich",
    "lenningen": "Remich",
    "canach": "Remich",
    "mondorf les bains": "Remich",
    "mondorf": "Remich",
    "remich": "Remich",
    "schengen": "Remich",
    "remerschen": "Remich",
    "wellenstain": "Remich",
    "wellenstein": "Remich",
    "stadtbredimus": "Remich",
    # Canton Vianden
    "putscheid": "Vianden",
    "tandel": "Vianden",
    "vianden": "Vianden",
    # Canton Wiltz
    "boulaide": "Wiltz",
    "esch sur sure": "Wiltz",
    "goesdorf": "Wiltz",
    "kiischpelt": "Wiltz",
    "lac de la haute sure": "Wiltz",
    "wiltz": "Wiltz",
    "winseler": "Wiltz",
}

COMMUNE_TO_CANTON = {_fold(k): v for k, v in _RAW_COMMUNE_TO_CANTON.items()}


def normalize_postcode(raw_postcode: str) -> str:
    """Extract a 4-digit Luxembourg postcode, or return ``""`` if invalid."""
    if not raw_postcode:
        return ""
    cleaned = re.sub(r"^(?:L|LU)\s*[-–]?\s*", "", str(raw_postcode).strip(), flags=re.I)
    match = re.search(r"\b([0-9]{4})\b", cleaned)
    return match.group(1) if match else ""


def resolve_commune_and_canton(postcode: str, raw_city: str = "") -> tuple[str, str]:
    """Return ``(commune_or_town, canton)`` for a Luxembourg postcode and/or city."""
    norm_postcode = normalize_postcode(postcode)
    commune = POSTCODE_TO_COMMUNE.get(norm_postcode, "")
    town = (raw_city or "").strip() or commune or "Luxembourg"
    lookup_commune = commune or town

    canton = COMMUNE_TO_CANTON.get(_fold(lookup_commune), "")
    if not canton and town:
        canton = COMMUNE_TO_CANTON.get(_fold(town), "")

    if not canton and norm_postcode:
        prefix2 = int(norm_postcode[:2])
        if 10 <= prefix2 <= 29:
            canton = "Luxembourg"
        elif 30 <= prefix2 <= 49:
            canton = "Esch-sur-Alzette"
        elif 50 <= prefix2 <= 53:
            canton = "Luxembourg"
        elif 54 <= prefix2 <= 57:
            canton = "Remich"
        elif 58 <= prefix2 <= 59:
            canton = "Luxembourg"
        elif 60 <= prefix2 <= 65:
            canton = "Echternach"
        elif 66 <= prefix2 <= 69:
            canton = "Grevenmacher"
        elif 70 <= prefix2 <= 73:
            canton = "Luxembourg"
        elif 74 <= prefix2 <= 77:
            canton = "Mersch"
        elif 78 <= prefix2 <= 84:
            canton = "Capellen"
        elif 85 <= prefix2 <= 88:
            canton = "Redange"
        elif 89 <= prefix2 <= 93:
            canton = "Diekirch"
        elif prefix2 == 94:
            canton = "Vianden"
        elif 95 <= prefix2 <= 96:
            canton = "Wiltz"
        elif 97 <= prefix2 <= 99:
            canton = "Clervaux"

    return town[:100], canton


def _clean_url(raw_url: str) -> str:
    url = (raw_url or "").strip()
    if not url:
        return ""
    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"
    parsed = urlparse(url)
    if not parsed.netloc or "." not in parsed.netloc or len(url) > 200:
        return ""
    return url


def _quantize_coord(raw_val, low: float, high: float):
    if raw_val is None or raw_val == "":
        return None
    try:
        dec = Decimal(str(raw_val)).quantize(Decimal("0.000001"))
    except (InvalidOperation, ValueError):
        return None
    if dec < Decimal(str(low)) or dec > Decimal(str(high)):
        return None
    return dec


def compose_legacy_address(
    street: str, number: str, postcode: str, town: str, fallback: str = ""
) -> str:
    street_part = " ".join(part for part in (street.strip(), number.strip()) if part)
    town_part = " ".join(
        part for part in (f"L-{postcode}" if postcode else "", town.strip()) if part
    )
    if street_part and town_part:
        return f"{street_part}, {town_part}"
    if street_part:
        return street_part
    if town_part:
        return town_part
    return fallback.strip() or "Luxembourg"


def parse_osm_element(element: dict) -> dict | None:
    """Convert a raw Overpass JSON element into a normalized venue dict."""
    tags = element.get("tags") or {}
    if tags.get("disused:amenity") or tags.get("abandoned:amenity"):
        return None

    name = (
        tags.get("name") or tags.get("name:fr") or tags.get("name:en") or ""
    ).strip()
    if not name:
        return None
    name = name[:200]

    amenity = (tags.get("amenity") or "restaurant").strip().lower()
    osm_type = element.get("type") or "node"
    osm_id = element.get("id")
    external_id = f"osm:{osm_type}:{osm_id}" if osm_id is not None else ""

    street = (tags.get("addr:street") or "")[:200].strip()
    number = (tags.get("addr:housenumber") or "")[:20].strip()
    postcode = normalize_postcode(
        tags.get("addr:postcode") or tags.get("postal_code") or ""
    )
    raw_city = (
        tags.get("addr:city")
        or tags.get("addr:town")
        or tags.get("addr:village")
        or tags.get("addr:municipality")
        or ""
    ).strip()

    town, canton = resolve_commune_and_canton(postcode, raw_city)
    address = compose_legacy_address(street, number, postcode, town)

    lat_raw = element.get("lat")
    lon_raw = element.get("lon")
    if (lat_raw is None or lon_raw is None) and isinstance(element.get("center"), dict):
        lat_raw = element["center"].get("lat")
        lon_raw = element["center"].get("lon")

    latitude = _quantize_coord(lat_raw, -90, 90)
    longitude = _quantize_coord(lon_raw, -180, 180)

    website = _clean_url(
        tags.get("website") or tags.get("contact:website") or tags.get("url") or ""
    )
    opening_hours = (tags.get("opening_hours") or "").strip()
    has_outdoor = (tags.get("outdoor_seating") or "").strip().lower() in (
        "yes",
        "true",
        "1",
    )
    cuisine = (tags.get("cuisine") or "").strip()
    has_kitchen = amenity in ("restaurant", "biergarten") or bool(cuisine)

    raw_cap = tags.get("capacity") or tags.get("seats") or ""
    capacity = DEFAULT_IMPORTED_CAPACITY
    capacity_estimated = True
    if str(raw_cap).strip().isdigit():
        parsed_cap = int(str(raw_cap).strip())
        if 1 <= parsed_cap <= 5000:
            capacity = parsed_cap
            capacity_estimated = False

    venue_tags = ["osm", amenity]
    if cuisine:
        first_cuisine = cuisine.split(";")[0].strip().lower()[:40]
        if first_cuisine:
            venue_tags.append(f"cuisine:{first_cuisine}")
    if capacity_estimated:
        venue_tags.append("capacite-estimee")

    phone = (
        tags.get("phone") or tags.get("contact:phone") or tags.get("telephone") or ""
    ).strip()[:50]
    email = (tags.get("email") or tags.get("contact:email") or "").strip()
    if email and "@" not in email:
        email = ""

    return {
        "external_source": "osm",
        "external_id": external_id,
        "name": name,
        "address": address,
        "city": town,
        "country": "Luxembourg",
        "address_street": street,
        "address_number": number,
        "address_postcode": postcode,
        "address_town": town,
        "canton": canton,
        "latitude": latitude,
        "longitude": longitude,
        "website": website,
        "opening_hours": opening_hours,
        "has_outdoor_space": has_outdoor,
        "has_kitchen": has_kitchen,
        "max_capacity": capacity,
        "tags": venue_tags,
        "contact_phone": phone,
        "contact_email": email,
        "echo_venue": None,
    }


def fetch_osm_luxembourg_elements(timeout: int = 90) -> list[dict]:
    """Query the public Overpass API for Luxembourg restaurants and bars."""
    last_exc = None
    for endpoint in OVERPASS_ENDPOINTS:
        try:
            response = requests.post(
                endpoint,
                data={"data": OVERPASS_LUXEMBOURG_QUERY},
                timeout=timeout,
                headers={"User-Agent": "Crush.lu-Hub-Venue-Importer/1.0"},
            )
            response.raise_for_status()
            payload = response.json()
            return payload.get("elements") or []
        except Exception as exc:  # pragma: no cover - network fallback
            last_exc = exc
            logger.warning("Overpass request to %s failed: %s", endpoint, exc)
    raise RuntimeError(f"All Overpass endpoints failed: {last_exc}") from last_exc


def load_osm_elements_from_file(path: str) -> list[dict]:
    with open(path, "r", encoding="utf-8") as fh:
        payload = json.load(fh)
    if isinstance(payload, list):
        return payload
    return payload.get("elements") or []


def _find_existing_location(parsed: dict) -> Location | None:
    ext_source = parsed.get("external_source") or ""
    ext_id = parsed.get("external_id") or ""
    if ext_id:
        match = Location.objects.filter(
            external_source=ext_source, external_id=ext_id
        ).first()
        if match is not None:
            return match

    echo_venue = parsed.get("echo_venue")
    if echo_venue is not None:
        match = Location.objects.filter(echo_venue=echo_venue).first()
        if match is not None:
            return match

    name = (parsed.get("name") or "").strip()
    postcode = (parsed.get("address_postcode") or "").strip()
    city = (parsed.get("city") or parsed.get("address_town") or "").strip()

    named = Location.objects.filter(name__iexact=name)
    if ext_source and ext_id:
        # A row already holding a *different* id from the same source is a
        # different venue in that source (the id match above would have caught
        # the same one). Without this, same-name outlets with no address tags,
        # which all fall back to city "Luxembourg", would merge into one row.
        named = named.exclude(Q(external_source=ext_source) & ~Q(external_id=""))

    if name and postcode:
        match = named.filter(address_postcode=postcode).first()
        if match is not None:
            return match

    if name and city:
        # Same name in the same town is only the same venue when a postcode is
        # missing on at least one side: two known, different postcodes are two
        # premises (spec §3.3), and merging them would hide one from coaches.
        candidates = named.filter(city__iexact=city)
        if postcode:
            candidates = candidates.filter(address_postcode="")
        match = candidates.first()
        if match is not None:
            return match

    return None


@transaction.atomic
def upsert_venue_record(
    parsed: dict, dry_run: bool = False
) -> tuple[str, Location | None]:
    """Idempotently insert or backfill a ``hub.Location`` in ``Prospect``.

    Never overwrites ``partnership_stage``, ``claimed_by``, ``account_manager``,
    or coach notes on an existing row.
    """
    existing = _find_existing_location(parsed)
    phone = (parsed.get("contact_phone") or "").strip()
    email = (parsed.get("contact_email") or "").strip()

    if existing is None:
        if dry_run:
            return "created", None
        location = Location.objects.create(
            name=parsed["name"],
            address=parsed["address"],
            city=parsed["city"],
            country=parsed.get("country") or "Luxembourg",
            max_capacity=parsed.get("max_capacity") or DEFAULT_IMPORTED_CAPACITY,
            has_outdoor_space=bool(parsed.get("has_outdoor_space")),
            has_kitchen=bool(parsed.get("has_kitchen")),
            partnership_stage=Location.PartnershipStage.PROSPECT,
            address_street=parsed.get("address_street") or "",
            address_number=parsed.get("address_number") or "",
            address_postcode=parsed.get("address_postcode") or "",
            address_town=parsed.get("address_town") or parsed["city"],
            canton=parsed.get("canton") or "",
            latitude=parsed.get("latitude"),
            longitude=parsed.get("longitude"),
            website=parsed.get("website") or "",
            opening_hours=parsed.get("opening_hours") or "",
            tags=parsed.get("tags") or [],
            external_source=parsed.get("external_source") or "",
            external_id=parsed.get("external_id") or "",
            echo_venue=parsed.get("echo_venue"),
        )
        if phone or email:
            LocationContact.objects.create(
                location=location,
                name="Standard / Réception",
                role="Contact général",
                phone=phone,
                email=email,
                is_primary=True,
            )
        return "created", location

    # Backfill only empty fields on an existing Location; never touch stage or claim.
    changed_fields = []
    backfill_Attrs = (
        "external_source",
        "external_id",
        "address_street",
        "address_number",
        "address_postcode",
        "address_town",
        "canton",
        "latitude",
        "longitude",
        "website",
        "opening_hours",
        "echo_venue",
    )
    for field in backfill_Attrs:
        new_val = parsed.get(field)
        cur_val = getattr(existing, field, None)
        if new_val not in (None, "") and cur_val in (None, ""):
            setattr(existing, field, new_val)
            changed_fields.append(field)

    # Merge new tags without removing existing ones.
    existing_tags = list(existing.tags or [])
    for tag in parsed.get("tags") or []:
        if tag and tag not in existing_tags:
            existing_tags.append(tag)
            if "tags" not in changed_fields:
                existing.tags = existing_tags
                changed_fields.append("tags")

    contact_added = False
    if (phone or email) and not existing.contacts.exists():
        contact_added = True
        if not dry_run:
            LocationContact.objects.create(
                location=existing,
                name="Standard / Réception",
                role="Contact général",
                phone=phone,
                email=email,
                is_primary=True,
            )

    if changed_fields and not dry_run:
        changed_fields.append("updated_at")
        existing.save(update_fields=changed_fields)

    if changed_fields or contact_added:
        return "updated", existing
    return "skipped", existing


def parse_echo_lu_venue_record(
    record: dict, echo_venue_obj: EchoVenue | None = None
) -> dict | None:
    """Convert an ``echo.lu`` venue API record into a normalized venue dict."""
    venue_id = echo_lu.extract_experience_id(record)
    title = record.get("title") or ""
    if isinstance(title, dict):
        title = (
            title.get("fr")
            or title.get("en")
            or title.get("de")
            or next((v for v in title.values() if v), "")
        )
    name = str(title or "").strip()[:200]
    if not name:
        return None

    loc_block = record.get("location") or {}
    addr_block = loc_block.get("address") or {}
    street = str(addr_block.get("street") or "").strip()[:200]
    number = str(addr_block.get("number") or "").strip()[:20]
    postcode = normalize_postcode(addr_block.get("postcode") or "")
    raw_town = str(addr_block.get("town") or addr_block.get("commune") or "").strip()

    town, canton = resolve_commune_and_canton(postcode, raw_town)
    address = compose_legacy_address(street, number, postcode, town)

    coords = loc_block.get("coordinates") or loc_block.get("geo") or {}
    latitude = _quantize_coord(coords.get("lat") or coords.get("latitude"), -90, 90)
    longitude = _quantize_coord(
        coords.get("lng") or coords.get("lon") or coords.get("longitude"), -180, 180
    )

    website = _clean_url(record.get("website") or "")

    return {
        "external_source": "echo_lu",
        "external_id": f"echo:{venue_id}" if venue_id else "",
        "name": name,
        "address": address,
        "city": town,
        "country": "Luxembourg",
        "address_street": street,
        "address_number": number,
        "address_postcode": postcode,
        "address_town": town,
        "canton": canton,
        "latitude": latitude,
        "longitude": longitude,
        "website": website,
        "opening_hours": "",
        "has_outdoor_space": False,
        "has_kitchen": False,
        "max_capacity": DEFAULT_IMPORTED_CAPACITY,
        "tags": ["echo-lu", "capacite-estimee"],
        "contact_phone": "",
        "contact_email": "",
        "echo_venue": echo_venue_obj,
    }


def sync_cached_echo_venues_to_locations(dry_run: bool = False) -> dict[str, int]:
    """Link or create ``hub.Location`` rows from cached ``crush_lu.EchoVenue`` rows."""
    counts = {"created": 0, "updated": 0, "skipped": 0}
    for echo_row in EchoVenue.objects.all():
        parts = (echo_row.key or "").split("|")
        postcode = normalize_postcode(parts[1] if len(parts) > 1 else "")
        name = (echo_row.title or parts[0] or "").strip()[:200]
        if not name:
            continue
        town, canton = resolve_commune_and_canton(postcode, "")
        parsed = {
            "external_source": "echo_lu",
            "external_id": f"echo:{echo_row.venue_id}",
            "name": name,
            "address": compose_legacy_address("", "", postcode, town),
            "city": town,
            "country": "Luxembourg",
            "address_street": "",
            "address_number": "",
            "address_postcode": postcode,
            "address_town": town,
            "canton": canton,
            "latitude": None,
            "longitude": None,
            "website": "",
            "opening_hours": "",
            "has_outdoor_space": False,
            "has_kitchen": False,
            "max_capacity": DEFAULT_IMPORTED_CAPACITY,
            "tags": ["echo-lu", "capacite-estimee"],
            "contact_phone": "",
            "contact_email": "",
            "echo_venue": echo_row,
        }
        action, _ = upsert_venue_record(parsed, dry_run=dry_run)
        counts[action] += 1
    return counts
