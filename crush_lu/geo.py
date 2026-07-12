"""
Pure-Python geodesy helpers for Crush Cache proximity checks.

Deliberately not GeoDjango: the only operation the hunt needs is
point-to-point distance from a submitted position to the team's current
station — never a spatial query — so GDAL/GEOS/PostGIS would add
deployment weight for zero benefit.
"""

import math

# Mean Earth radius in meters (IUGG)
EARTH_RADIUS_M = 6_371_008.8


def haversine_m(lat1, lng1, lat2, lng2):
    """Great-circle distance in meters between two WGS84 points.

    Accepts floats or Decimals (as stored on CacheStation/MeetupEvent).
    """
    phi1 = math.radians(float(lat1))
    phi2 = math.radians(float(lat2))
    dphi = math.radians(float(lat2) - float(lat1))
    dlambda = math.radians(float(lng2) - float(lng1))

    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def bearing_deg(lat1, lng1, lat2, lng2):
    """Initial bearing in degrees (0-360, clockwise from north) from
    point 1 toward point 2. Used for the compass navigation mode.
    """
    phi1 = math.radians(float(lat1))
    phi2 = math.radians(float(lat2))
    dlambda = math.radians(float(lng2) - float(lng1))

    x = math.sin(dlambda) * math.cos(phi2)
    y = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(
        dlambda
    )
    return (math.degrees(math.atan2(x, y)) + 360) % 360
