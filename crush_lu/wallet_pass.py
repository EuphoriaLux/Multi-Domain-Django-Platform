from .models import ReferralCode
from .referrals import build_referral_url


def build_wallet_pass_barcode_value(profile, request=None, base_url=None):
    """
    Build the QR/barcode payload for wallet passes.
    Embeds the referral URL so scans attribute signups.
    """
    referral_code = ReferralCode.get_or_create_for_profile(profile)
    return build_referral_url(referral_code.code, request=request, base_url=base_url)
