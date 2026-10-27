from django import template

from crush_lu.services.donation_bank_transfer import get_bank_transfer_details

register = template.Library()


@register.simple_tag
def donation_bank_transfer(profile=None):
    """``{% donation_bank_transfer profile as bank %}`` for the support card.

    ``None`` unless the bank details are configured and valid. The QR is only
    built when the template reads ``bank.qr``.
    """
    return get_bank_transfer_details(profile or None)
