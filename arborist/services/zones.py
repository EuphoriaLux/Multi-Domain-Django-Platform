"""
Distance and zone calculation engine for Arborist Tom Aakrann (arborist.lu).

Calculates prepayment tiers based on location relative to Altrier (Junglinster region):
- Zone 1: up to 18 km from Altrier (Junglinster, Bech, Consdorf, Echternach, etc.) -> 50 €
- Zone 2: Wider Luxembourg (> 18 km, e.g. Luxembourg-City, South, West, North) -> 100 €
- Rush Order: Emergency / expedited appointment within 24-48h -> +50 € surcharge

The prepayment (Anfahrtspauschale / Vorabzahlung) guarantees the appointment slot
and is 100% credited against the final service invoice upon execution of work.

The zone is decided by distance alone, so the published "up to 18 km" rule is
exactly the rule that is charged. The distance comes from the commune the
postcode resolves to in the CACLR table, else from a postcode-prefix estimate.
The customer's free-text town is only echoed back for display: pricing it would
let anyone type a cheaper commune next to an unknown postcode.
"""

import logging
import re
from decimal import Decimal
from typing import Any, Dict, NamedTuple, Optional

logger = logging.getLogger(__name__)

# Reference base: Altrier (Commune Bech / adjacent to Junglinster)
ALTIER_POSTCODE = "6211"
ALTIER_NAME = "Altrier"
ALTIER_LAT = 49.7544
ALTIER_LON = 6.3149

ZONE_1_FEE = Decimal("50.00")
ZONE_2_FEE = Decimal("100.00")
RUSH_ORDER_FEE = Decimal("50.00")

# Zone 1 is everything up to this distance from Altrier; beyond it is Zone 2.
ZONE_1_MAX_DISTANCE_KM = 18.0

POSTAL_CODE_RE = re.compile(r"\d{4}")

# Approximate centroid distances from Altrier for common Luxembourg communes (in km)
COMMUNE_DISTANCES = {
    "bech": 2.5,
    "consdorf": 4.8,
    "junglinster": 6.2,
    "waldbillig": 7.5,
    "berdorf": 8.9,
    "beaufort": 9.4,
    "heffingen": 9.8,
    "betzdorf": 10.5,
    "larochette": 11.2,
    "echternach": 11.8,
    "fischbach": 12.6,
    "biwer": 13.1,
    "rosport-mompach": 14.2,
    "flaxweiler": 15.0,
    "manternach": 16.4,
    "vallée de l'ernz": 16.8,
    "vallee de l'ernz": 16.8,
    "bettendorf": 17.0,
    "mertert": 17.5,
    "grevenmacher": 17.8,
    "schuttrange": 18.0,
    "niederanven": 18.2,
    "reisdorf": 18.5,
    "diekirch": 19.5,
    "lorentzweiler": 19.8,
    "steinsel": 20.5,
    "mersch": 21.0,
    "luxembourg": 24.5,
    "sandweiler": 22.0,
    "strassen": 26.0,
    "bertrange": 27.5,
    "ettelbruck": 23.0,
    "hesperange": 27.0,
    "mamer": 29.0,
    "bettembourg": 35.0,
    "differdange": 46.0,
    "dudelange": 41.0,
    "esch-sur-alzette": 42.0,
    "petange": 47.0,
    "sanem": 44.0,
    "wiltz": 45.0,
    "clervaux": 50.0,
    "troisvierges": 58.0,
}


class ZoneQuote(NamedTuple):
    zone: int
    zone_name: str
    commune: str
    postal_code: str
    distance_km: float
    base_prepayment_eur: Decimal
    is_rush: bool
    rush_fee_eur: Decimal
    total_prepayment_eur: Decimal
    guarantee_notice: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "zone": self.zone,
            "zone_name": self.zone_name,
            "commune": self.commune,
            "postal_code": self.postal_code,
            "distance_km": self.distance_km,
            "base_prepayment_eur": float(self.base_prepayment_eur),
            "is_rush": self.is_rush,
            "rush_fee_eur": float(self.rush_fee_eur),
            "total_prepayment_eur": float(self.total_prepayment_eur),
            "guarantee_notice": self.guarantee_notice,
        }


def clean_postal_code(postal_code: Optional[str]) -> str:
    """
    Normalize a Luxembourg postal code: strip whitespace and an optional
    'L-' / 'L ' / 'L' prefix.

    Nothing else is dropped or truncated, so '62111' stays '62111' and fails
    :func:`is_valid_postal_code` instead of being priced as 6211.
    """
    if not postal_code:
        return ""
    code = str(postal_code).strip().upper()
    if code.startswith("L-") or code.startswith("L "):
        code = code[2:].strip()
    elif code.startswith("L"):
        code = code[1:].strip()
    return code


def is_valid_postal_code(code: Optional[str]) -> bool:
    """True for exactly four digits (a normalized Luxembourg postal code)."""
    return bool(code) and POSTAL_CODE_RE.fullmatch(code) is not None


def lookup_commune_from_postcode(postal_code: str) -> Optional[str]:
    """Resolve commune name from 4-digit Luxembourg postal code using CACLR table."""
    cleaned = clean_postal_code(postal_code)
    if not cleaned:
        return None
    # Imported here, not at module level: arborist.models imports this module
    # while the app registry loads, and crush_lu.services' package __init__
    # pulls in model-importing services. A failure must surface, not silently
    # re-price the booking from the postcode-prefix fallback.
    from crush_lu.services.echo_lu_postcodes import POSTCODE_TO_COMMUNE

    return POSTCODE_TO_COMMUNE.get(cleaned)


def calculate_zone(
    postal_code: str,
    city_or_commune: Optional[str] = None,
    is_rush: bool = False,
) -> ZoneQuote:
    """
    Calculate the appointment prepayment quote based on location relative to Altrier.

    Raises ValueError for anything but a 4-digit Luxembourg postal code, so a
    malformed code can never be priced as a neighbouring one.
    """
    cleaned_code = clean_postal_code(postal_code)
    if not is_valid_postal_code(cleaned_code):
        raise ValueError(f"Not a 4-digit Luxembourg postal code: {postal_code!r}")

    commune = lookup_commune_from_postcode(cleaned_code) or ""
    commune_key = commune.lower()

    # Estimate distance
    if commune_key in COMMUNE_DISTANCES:
        distance = COMMUNE_DISTANCES[commune_key]
    elif cleaned_code.startswith(("61", "62", "63")):
        distance = 7.0
    elif cleaned_code.startswith(("64", "65", "66")):
        distance = 13.0
    elif cleaned_code.startswith(("67", "68", "69")):
        distance = 16.0
    elif cleaned_code.startswith(("1", "2")):
        distance = 24.0
    elif cleaned_code.startswith(("3", "4")):
        distance = 42.0
    elif cleaned_code.startswith(("7", "8")):
        distance = 25.0
    elif cleaned_code.startswith("9"):
        distance = 45.0
    else:
        distance = 25.0

    # Determine Zone
    if distance <= ZONE_1_MAX_DISTANCE_KM:
        zone = 1
        zone_name = "Zone 1 (Altrier / Junglinster & Umgebung)"
        base_fee = ZONE_1_FEE
    else:
        zone = 2
        zone_name = "Zone 2 (Erweitertes Luxemburg)"
        base_fee = ZONE_2_FEE

    rush_fee = RUSH_ORDER_FEE if is_rush else Decimal("0.00")
    total_fee = base_fee + rush_fee

    guarantee_notice = (
        "Die Anfahrtspauschale wird bei Auftragserteilung zu 100% mit der "
        "Gesamtrechnung der Baumpflege- oder Kontrollarbeiten verrechnet."
    )

    display_commune = commune or (city_or_commune or "").strip() or "Luxembourg"

    return ZoneQuote(
        zone=zone,
        zone_name=zone_name,
        commune=display_commune,
        postal_code=cleaned_code,
        distance_km=distance,
        base_prepayment_eur=base_fee,
        is_rush=is_rush,
        rush_fee_eur=rush_fee,
        total_prepayment_eur=total_fee,
        guarantee_notice=guarantee_notice,
    )
