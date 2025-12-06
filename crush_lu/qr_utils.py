"""
QR Code Generation Utilities for Advent Calendar

This module provides utilities for generating QR codes for physical gift unlocks.
Each QR code encodes a unique URL that triggers the QR scan endpoint.
"""

import uuid
import io
import base64
from typing import Optional, List, Tuple
from pathlib import Path

# QR code generation imports
try:
    import qrcode
    from qrcode.constants import ERROR_CORRECT_L, ERROR_CORRECT_M, ERROR_CORRECT_H
    HAS_QRCODE = True
except ImportError:
    HAS_QRCODE = False
    ERROR_CORRECT_L = 0
    ERROR_CORRECT_M = 1
    ERROR_CORRECT_H = 2

# PDF generation imports
try:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas
    from reportlab.lib.utils import ImageReader
    HAS_REPORTLAB = True
except ImportError:
    HAS_REPORTLAB = False


def check_dependencies():
    """Check if required dependencies are installed."""
    missing = []
    if not HAS_QRCODE:
        missing.append('qrcode')
    if not HAS_REPORTLAB:
        missing.append('reportlab')
    return missing


def generate_qr_code_image(
    url: str,
    box_size: int = 10,
    border: int = 4,
    error_correction: int = ERROR_CORRECT_M
) -> Optional[bytes]:
    """
    Generate a QR code image as PNG bytes.

    Args:
        url: The URL to encode in the QR code
        box_size: Size of each box in pixels
        border: Width of border (minimum 4 for QR spec)
        error_correction: Error correction level

    Returns:
        PNG image bytes or None if qrcode not installed
    """
    if not HAS_QRCODE:
        raise ImportError("qrcode package not installed. Run: pip install qrcode[pil]")

    qr = qrcode.QRCode(
        version=1,
        error_correction=error_correction,
        box_size=box_size,
        border=border,
    )
    qr.add_data(url)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")

    # Convert to bytes
    img_buffer = io.BytesIO()
    img.save(img_buffer, format='PNG')
    img_buffer.seek(0)

    return img_buffer.getvalue()


def generate_qr_code_base64(url: str, **kwargs) -> str:
    """
    Generate a QR code as a base64-encoded data URL.

    Useful for embedding in HTML without saving to disk.
    """
    png_bytes = generate_qr_code_image(url, **kwargs)
    b64 = base64.b64encode(png_bytes).decode('utf-8')
    return f"data:image/png;base64,{b64}"


def generate_advent_qr_url(token: uuid.UUID, domain: str = "crush.lu") -> str:
    """
    Generate the full URL for an Advent Calendar QR code.

    Args:
        token: The QRCodeToken UUID
        domain: The domain to use (default: crush.lu)

    Returns:
        Full URL string (e.g., https://crush.lu/advent/qr/uuid-here/)
    """
    return f"https://{domain}/advent/qr/{token}/"


def generate_tokens_for_user(user, calendar, save: bool = True) -> List:
    """
    Generate QR code tokens for all doors in a calendar for a specific user.

    Args:
        user: Django User instance
        calendar: AdventCalendar instance
        save: Whether to save tokens to database

    Returns:
        List of QRCodeToken instances
    """
    from .models import QRCodeToken, AdventDoor

    tokens = []
    doors = calendar.doors.all().order_by('door_number')

    for door in doors:
        # Check if token already exists
        existing = QRCodeToken.objects.filter(door=door, user=user).first()
        if existing:
            tokens.append(existing)
            continue

        # Create new token
        token = QRCodeToken(
            door=door,
            user=user,
            token=uuid.uuid4()
        )
        if save:
            token.save()
        tokens.append(token)

    return tokens


def generate_printable_qr_sheet(
    tokens: List,
    output_path: Optional[str] = None,
    title: str = "Advent Calendar QR Codes"
) -> Optional[bytes]:
    """
    Generate a printable PDF sheet with QR codes for all 24 doors.

    The layout is designed for A4 paper with 4 columns x 6 rows of QR codes.

    Args:
        tokens: List of QRCodeToken instances
        output_path: Optional file path to save PDF
        title: Title to display on the PDF

    Returns:
        PDF bytes if output_path is None, otherwise None (saves to file)
    """
    if not HAS_REPORTLAB:
        raise ImportError("reportlab package not installed. Run: pip install reportlab")

    if not HAS_QRCODE:
        raise ImportError("qrcode package not installed. Run: pip install qrcode[pil]")

    # PDF setup
    buffer = io.BytesIO() if output_path is None else None
    pdf_output = buffer if buffer else output_path

    c = canvas.Canvas(pdf_output, pagesize=A4)
    width, height = A4

    # Layout configuration
    margin = 15 * mm
    qr_size = 40 * mm
    cols = 4
    rows = 6
    col_width = (width - 2 * margin) / cols
    row_height = (height - 3 * margin) / rows  # Extra margin for title

    # Title
    c.setFont("Helvetica-Bold", 16)
    c.drawCentredString(width / 2, height - margin, title)

    # Generate QR codes in grid
    for i, token in enumerate(tokens[:24]):  # Max 24 doors
        col = i % cols
        row = i // cols

        x = margin + col * col_width + (col_width - qr_size) / 2
        y = height - 2 * margin - (row + 1) * row_height + (row_height - qr_size) / 2

        # Generate QR code
        url = generate_advent_qr_url(token.token)
        qr_bytes = generate_qr_code_image(url, box_size=5, border=2)
        qr_image = ImageReader(io.BytesIO(qr_bytes))

        # Draw QR code
        c.drawImage(qr_image, x, y, qr_size, qr_size)

        # Draw door number label below
        c.setFont("Helvetica", 10)
        label_y = y - 5 * mm
        c.drawCentredString(x + qr_size / 2, label_y, f"Door {token.door.door_number}")

    c.save()

    if buffer:
        buffer.seek(0)
        return buffer.getvalue()

    return None


def generate_individual_qr_cards(
    tokens: List,
    output_dir: str,
    include_instructions: bool = True
) -> List[str]:
    """
    Generate individual QR code image files for each door.

    Useful for creating individual gift tags or stickers.

    Args:
        tokens: List of QRCodeToken instances
        output_dir: Directory to save images
        include_instructions: Whether to add scan instructions

    Returns:
        List of generated file paths
    """
    if not HAS_QRCODE:
        raise ImportError("qrcode package not installed. Run: pip install qrcode[pil]")

    try:
        from PIL import Image, ImageDraw, ImageFont
        HAS_PIL = True
    except ImportError:
        HAS_PIL = False

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    generated_files = []

    for token in tokens:
        url = generate_advent_qr_url(token.token)

        if HAS_PIL and include_instructions:
            # Create QR code
            qr = qrcode.QRCode(
                version=1,
                error_correction=ERROR_CORRECT_M,
                box_size=10,
                border=4,
            )
            qr.add_data(url)
            qr.make(fit=True)
            qr_img = qr.make_image(fill_color="black", back_color="white")

            # Create card with text
            qr_size = qr_img.size[0]
            card_width = qr_size
            card_height = qr_size + 80  # Extra space for text

            card = Image.new('RGB', (card_width, card_height), 'white')
            card.paste(qr_img, (0, 0))

            # Add text
            draw = ImageDraw.Draw(card)
            try:
                font = ImageFont.truetype("arial.ttf", 20)
                small_font = ImageFont.truetype("arial.ttf", 14)
            except OSError:
                font = ImageFont.load_default()
                small_font = font

            # Door number
            door_text = f"Door {token.door.door_number}"
            text_bbox = draw.textbbox((0, 0), door_text, font=font)
            text_width = text_bbox[2] - text_bbox[0]
            draw.text(
                ((card_width - text_width) / 2, qr_size + 10),
                door_text,
                fill="black",
                font=font
            )

            # Instruction
            instruction = "Scan to unlock!"
            instr_bbox = draw.textbbox((0, 0), instruction, font=small_font)
            instr_width = instr_bbox[2] - instr_bbox[0]
            draw.text(
                ((card_width - instr_width) / 2, qr_size + 40),
                instruction,
                fill="gray",
                font=small_font
            )

            # Save
            file_path = output_path / f"door_{token.door.door_number:02d}.png"
            card.save(file_path)
        else:
            # Simple QR code without text
            qr_bytes = generate_qr_code_image(url)
            file_path = output_path / f"door_{token.door.door_number:02d}.png"
            with open(file_path, 'wb') as f:
                f.write(qr_bytes)

        generated_files.append(str(file_path))

    return generated_files


def get_qr_stats(calendar) -> dict:
    """
    Get statistics about QR tokens for a calendar.

    Returns:
        Dictionary with token statistics
    """
    from .models import QRCodeToken

    tokens = QRCodeToken.objects.filter(door__calendar=calendar)

    return {
        'total_tokens': tokens.count(),
        'used_tokens': tokens.filter(is_used=True).count(),
        'unused_tokens': tokens.filter(is_used=False).count(),
        'expired_tokens': sum(1 for t in tokens if t.expires_at and not t.is_valid()),
        'tokens_per_door': {
            door.door_number: tokens.filter(door=door).count()
            for door in calendar.doors.all()
        }
    }
