"""
QR code generation utility for Journey Gifts.

Generates styled QR codes with Crush.lu branding for the gift sharing feature.
"""

from io import BytesIO

import qrcode
from django.core.files.base import ContentFile


def generate_gift_qr_code(gift, domain='crush.lu'):
    """
    Generate a styled QR code for a journey gift.

    Args:
        gift: JourneyGift instance
        domain: Domain to use in the URL (default: crush.lu)

    Returns:
        ContentFile: The generated QR code image as a Django ContentFile
    """
    # Build the gift landing page URL
    gift_url = f"https://{domain}/journey/gift/{gift.gift_code}/"

    # Create QR code with appropriate settings
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(gift_url)
    qr.make(fit=True)

    # Create image with Crush.lu purple color
    img = qr.make_image(fill_color="#9B59B6", back_color="white")

    # Save to bytes buffer
    buffer = BytesIO()
    img.save(buffer, format='PNG')
    buffer.seek(0)

    # Return as Django ContentFile
    return ContentFile(buffer.read(), name=f'{gift.gift_code}.png')


def save_gift_qr_code(gift, domain='crush.lu'):
    """
    Generate and save QR code to the gift's qr_code_image field.

    Args:
        gift: JourneyGift instance
        domain: Domain to use in the URL (default: crush.lu)

    Returns:
        The gift instance with the QR code saved
    """
    qr_content = generate_gift_qr_code(gift, domain)
    gift.qr_code_image.save(f'{gift.gift_code}.png', qr_content, save=True)
    return gift
