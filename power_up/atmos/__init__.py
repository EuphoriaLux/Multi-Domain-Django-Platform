"""Atmos — QR bar ordering with a Speakeasy Noir chronicle on the drink ticket.

The `lore` and `printing` subpackages are deliberately **Django-free**: they are
pure functions over dataclasses so they can be unit-tested without a database,
a settings module, a bar, or a printer. Django models and views wrap them later.
"""
