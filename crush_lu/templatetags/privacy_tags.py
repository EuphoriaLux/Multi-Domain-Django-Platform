"""Template filters for showing personal data without fully exposing it."""

from django import template

register = template.Library()


@register.filter
def mask_email(value):
    """Partially hide an email address for display: ``t***@gmail.com``.

    Keeps the first character of the local part (so a user recognises their
    own address) and the whole domain (so they know which inbox to check),
    masking everything else. Falls back to the original value for anything
    that isn't a plain ``local@domain`` string (empty, no ``@``, etc.) so a
    caller can safely ``{% if email %}`` around it either way.
    """
    if not value or "@" not in value:
        return value
    local, _, domain = value.partition("@")
    if not local or not domain:
        return value
    if len(local) <= 1:
        masked_local = local + "*"
    else:
        masked_local = local[0] + "*" * len(local[1:])
    return f"{masked_local}@{domain}"
