#!/usr/bin/env python3
"""
Convert VinsDelux Journey Images to WebP Format

This script converts the 5 journey step PNG images to WebP format with optimized
quality settings, reducing file size by approximately 75% while maintaining
visual quality.

Usage:
    python scripts/convert_vinsdelux_journey_to_webp.py

Requirements:
    pip install Pillow

Output:
    - Creates WebP versions alongside existing PNG files
    - Preserves original PNG files
    - Shows before/after file sizes and savings
"""

import os
from pathlib import Path
from PIL import Image

# Paths
BASE_DIR = Path(__file__).resolve().parent.parent
JOURNEY_DIR = BASE_DIR / "vinsdelux" / "static" / "vinsdelux" / "images" / "journey"

# WebP quality setting (80 is a good balance of quality vs size)
WEBP_QUALITY = 80


def convert_image_to_webp(png_path: Path, webp_path: Path, quality: int = 80) -> tuple[int, int]:
    """
    Convert a PNG image to WebP format.

    Args:
        png_path: Path to source PNG file
        webp_path: Path to output WebP file
        quality: WebP quality (1-100, default 80)

    Returns:
        Tuple of (original_size, new_size) in bytes
    """
    # Open and convert image
    with Image.open(png_path) as img:
        # Convert RGBA to RGB if necessary (WebP handles both)
        if img.mode in ('RGBA', 'LA'):
            # Create white background
            background = Image.new('RGB', img.size, (255, 255, 255))
            background.paste(img, mask=img.split()[-1])  # Use alpha channel as mask
            img = background

        # Save as WebP with optimization
        img.save(
            webp_path,
            'webp',
            quality=quality,
            method=6,  # Maximum compression effort (0-6)
            lossless=False
        )

    # Get file sizes
    original_size = png_path.stat().st_size
    new_size = webp_path.stat().st_size

    return original_size, new_size


def format_size(size_bytes: int) -> str:
    """Format bytes to human-readable string."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.2f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.2f} MB"


def main():
    print("=" * 80)
    print("VinsDelux Journey Images - PNG to WebP Conversion")
    print("=" * 80)
    print(f"Source directory: {JOURNEY_DIR}")
    print(f"WebP quality: {WEBP_QUALITY}")
    print()

    # Check if Pillow is installed
    try:
        from PIL import Image
    except ImportError:
        print("[ERROR] Pillow is not installed")
        print("   Please install it with: pip install Pillow")
        return 1

    # Check if directory exists
    if not JOURNEY_DIR.exists():
        print(f"[ERROR] Journey directory not found: {JOURNEY_DIR}")
        return 1

    # Find all PNG files
    png_files = sorted(JOURNEY_DIR.glob("step_*.png"))

    if not png_files:
        print("[ERROR] No step_*.png files found in journey directory")
        return 1

    print(f"Found {len(png_files)} PNG files to convert\n")

    # Convert each file
    total_original = 0
    total_new = 0
    conversions = []

    for png_path in png_files:
        webp_path = png_path.with_suffix('.webp')

        print(f"Converting: {png_path.name}...", end=" ")

        try:
            original_size, new_size = convert_image_to_webp(png_path, webp_path, WEBP_QUALITY)
            total_original += original_size
            total_new += new_size

            savings_percent = ((original_size - new_size) / original_size) * 100

            conversions.append({
                'name': png_path.name,
                'original': original_size,
                'new': new_size,
                'savings_percent': savings_percent
            })

            print(f"[OK] Done")
            print(f"  PNG:  {format_size(original_size)}")
            print(f"  WebP: {format_size(new_size)} ({savings_percent:.1f}% smaller)")

        except Exception as e:
            print(f"[ERROR] Failed: {e}")

    # Summary
    print("\n" + "=" * 80)
    print("CONVERSION SUMMARY")
    print("=" * 80)

    for conv in conversions:
        print(f"{conv['name']:15s} | PNG: {format_size(conv['original']):>10s} -> "
              f"WebP: {format_size(conv['new']):>10s} | "
              f"Saved: {conv['savings_percent']:5.1f}%")

    print("-" * 80)
    total_savings = total_original - total_new
    total_savings_percent = (total_savings / total_original) * 100

    print(f"{'TOTAL':15s} | PNG: {format_size(total_original):>10s} -> "
          f"WebP: {format_size(total_new):>10s} | "
          f"Saved: {total_savings_percent:5.1f}%")
    print(f"\nTotal savings: {format_size(total_savings)}")

    print("\n" + "=" * 80)
    print("NEXT STEPS")
    print("=" * 80)
    print("1. Test the WebP images locally:")
    print("   - Start dev server: python manage.py runserver")
    print("   - Visit: http://localhost:8000")
    print("   - Check browser console for any errors")
    print()
    print("2. Upload WebP images to Azure Blob Storage:")
    print("   - Container: media")
    print("   - Path: vinsdelux/journey/")
    print("   - Files: step_01.webp through step_05.webp")
    print()
    print("3. Update VINSDELUX_JOURNEY_BASE_URL in settings if needed")
    print()
    print("[SUCCESS] Conversion complete!")
    print("=" * 80)

    return 0


if __name__ == "__main__":
    exit(main())
