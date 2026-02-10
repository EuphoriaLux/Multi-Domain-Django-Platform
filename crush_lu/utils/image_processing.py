"""
Image processing utilities for Crush.lu profile photos.

Handles EXIF stripping, orientation correction, and resizing on upload.
"""

import io
import logging
import os

from django.core.files.uploadedfile import InMemoryUploadedFile
from PIL import Image, ImageOps

logger = logging.getLogger(__name__)

# Max dimension on longest edge after processing
MAX_DIMENSION = 1200

# JPEG quality for re-saved images
JPEG_QUALITY = 90


def process_uploaded_image(image_file, filename=None):
    """
    Process an uploaded image: fix orientation, strip EXIF, resize.

    Args:
        image_file: A file-like object (UploadedFile, ContentFile, etc.)
        filename: Optional filename override. If None, uses image_file.name.

    Returns:
        InMemoryUploadedFile with processed image data.
    """
    image_file.seek(0)
    img = Image.open(image_file)

    # Fix orientation from EXIF before stripping metadata
    img = ImageOps.exif_transpose(img)

    # Convert RGBA/P to RGB for JPEG output
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")

    # Resize if larger than MAX_DIMENSION on longest edge
    # thumbnail() modifies in-place, preserves aspect ratio, only downsizes
    img.thumbnail((MAX_DIMENSION, MAX_DIMENSION), Image.LANCZOS)

    # Determine output format
    original_name = filename or getattr(image_file, "name", "photo.jpg")
    ext = os.path.splitext(original_name)[1].lower()

    if ext == ".png":
        output_format = "PNG"
        content_type = "image/png"
    elif ext == ".webp":
        output_format = "WEBP"
        content_type = "image/webp"
    else:
        output_format = "JPEG"
        content_type = "image/jpeg"
        # Ensure .jpg extension
        if ext not in (".jpg", ".jpeg"):
            original_name = os.path.splitext(original_name)[0] + ".jpg"

    # Save to buffer (Pillow strips EXIF by default on save)
    buffer = io.BytesIO()
    save_kwargs = {}
    if output_format == "JPEG":
        save_kwargs["quality"] = JPEG_QUALITY
        save_kwargs["optimize"] = True
    elif output_format == "WEBP":
        save_kwargs["quality"] = JPEG_QUALITY

    img.save(buffer, format=output_format, **save_kwargs)
    buffer.seek(0)

    processed = InMemoryUploadedFile(
        file=buffer,
        field_name=None,
        name=original_name,
        content_type=content_type,
        size=buffer.getbuffer().nbytes,
        charset=None,
    )

    logger.debug(
        "Processed image %s: %dx%d, %s, %d bytes",
        original_name,
        img.width,
        img.height,
        output_format,
        buffer.getbuffer().nbytes,
    )

    return processed
