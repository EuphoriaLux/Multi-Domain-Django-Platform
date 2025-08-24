#!/usr/bin/env python3
"""
Simple verification script for Pixel War build integration
"""

import os
import json
from pathlib import Path

def check_build_files():
    """Check if build files exist"""
    print("Checking Build Files:")
    print("-" * 30)
    
    dist_dir = Path("vibe_coding/static/vibe_coding/js/dist")
    if not dist_dir.exists():
        print("ERROR: Build directory not found!")
        return False
    
    print("OK: Build directory exists")
    
    # Check manifest
    manifest_file = dist_dir / "manifest.json"
    if manifest_file.exists():
        with open(manifest_file, 'r') as f:
            manifest = json.load(f)
        print("OK: Manifest file exists")
        print(f"  Entries: {list(manifest.keys())}")
        
        # Check bundle files
        for entry, bundle in manifest.items():
            bundle_path = dist_dir / bundle
            if bundle_path.exists():
                size_kb = bundle_path.stat().st_size / 1024
                print(f"  OK: {entry} -> {bundle} ({size_kb:.1f} KB)")
            else:
                print(f"  ERROR: Missing {bundle}")
                return False
    else:
        print("ERROR: Manifest file missing!")
        return False
    
    return True

def check_templates():
    """Check if templates use new build system"""
    print("\nChecking Templates:")
    print("-" * 30)
    
    templates = [
        "vibe_coding/templates/vibe_coding/pixel_war.html",
        "vibe_coding/templates/vibe_coding/pixel_war_pixi.html"
    ]
    
    for template_path in templates:
        template_file = Path(template_path)
        if template_file.exists():
            with open(template_file, 'r', encoding='utf-8') as f:
                content = f.read()
            
            template_name = template_file.name
            print(f"\n{template_name}:")
            
            if "{% load vite_tags %}" in content:
                print("  OK: Loads vite_tags")
            else:
                print("  WARNING: Missing vite_tags load")
            
            if "{% vite_asset" in content:
                print("  OK: Uses vite_asset tags")
            elif "{% static 'vibe_coding/js/pixel_war" in content:
                print("  INFO: Uses static tags (dev fallback)")
            else:
                print("  WARNING: No asset loading found")
            
            if "cdn.jsdelivr.net" in content:
                print("  WARNING: Still has CDN dependencies")
            else:
                print("  OK: No CDN dependencies")
        else:
            print(f"ERROR: Template not found: {template_path}")

def main():
    """Main verification"""
    print("Pixel War Build Verification")
    print("=" * 40)
    
    build_ok = check_build_files()
    check_templates()
    
    print("\n" + "=" * 40)
    if build_ok:
        print("SUCCESS: Build integration appears to be working!")
        print("\nTo test in browser:")
        print("1. Run: python manage.py runserver")
        print("2. Visit: http://localhost:8000/vibe-coding/pixel-wars/")
        print("3. Open browser dev tools (F12)")
        print("4. Check Network tab - you should see:")
        print("   - pixel-war-*.bundle.js files")
        print("   - pixi-core-*.chunk.js")
        print("   - No CDN requests to jsdelivr.net")
    else:
        print("ERROR: Build integration has issues!")

if __name__ == "__main__":
    main()