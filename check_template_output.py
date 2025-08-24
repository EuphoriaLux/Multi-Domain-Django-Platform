#!/usr/bin/env python3
"""
Check what the templates actually generate
"""

import os
import sys
import django
from pathlib import Path

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'azureproject.settings')
django.setup()

from django.template.loader import get_template
from django.template import Context
from django.conf import settings

def test_template_output():
    """Test what the templates actually generate"""
    print("Template Output Test")
    print("=" * 30)
    
    # Mock context data (similar to what your view would pass)
    context = {
        'canvas': {
            'id': 1,
            'width': 100,
            'height': 100
        },
        'user': {
            'is_authenticated': True
        },
        'debug': settings.DEBUG
    }
    
    templates_to_test = [
        'vibe_coding/pixel_war_pixi.html',
        # Add more if needed
    ]
    
    for template_name in templates_to_test:
        try:
            print(f"\n{template_name}:")
            print("-" * 40)
            
            template = get_template(template_name)
            rendered = template.render(context)
            
            # Extract just the JavaScript loading parts
            lines = rendered.split('\n')
            js_loading_lines = []
            
            for line in lines:
                line = line.strip()
                if ('script' in line and 
                    ('vite' in line or 'pixel-war' in line or 'pixi' in line)):
                    js_loading_lines.append(line)
            
            if js_loading_lines:
                print("JavaScript loading:")
                for line in js_loading_lines:
                    print(f"  {line}")
            else:
                print("No JavaScript loading found in output")
                
        except Exception as e:
            print(f"ERROR rendering {template_name}: {e}")

def check_vite_tags_directly():
    """Test vite_tags directly"""
    print("\n\nVite Tags Direct Test")
    print("=" * 30)
    
    try:
        from vibe_coding.templatetags.vite_tags import vite_asset, vite_preload_deps
        
        print("Testing vite_asset('pixel-war-pixi'):")
        result = vite_asset('pixel-war-pixi')
        print(f"  Output: {result}")
        
        print("\nTesting vite_preload_deps():")
        result = vite_preload_deps()
        print(f"  Output: {result}")
        
    except Exception as e:
        print(f"ERROR: {e}")

if __name__ == "__main__":
    test_template_output()
    check_vite_tags_directly()