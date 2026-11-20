# entreprinder/vibe/__init__.py
"""
Legacy Vibe Coding ORM ownership module.

Only the Pixel War ORM models remain here so their ``vibe_coding_*`` database
tables stay owned by the ``entreprinder`` app label without requiring schema
migrations. The active Pixel War UI, JSON APIs, admin registrations, and
management commands live in ``power_up.pixel_war``.
"""
