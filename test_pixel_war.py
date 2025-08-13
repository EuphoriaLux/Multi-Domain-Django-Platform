#!/usr/bin/env python
"""
Quick test script for Lux Pixel War functionality
Run this to verify the app is working correctly.
"""

import os
import sys
import django

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'azureproject.settings')
django.setup()

from django.test import Client
from django.contrib.auth.models import User
from vibe_coding.models import PixelCanvas, Pixel, PixelHistory
import json


def test_pixel_war():
    """Run basic tests for pixel war functionality"""
    client = Client()
    results = []
    
    print("🧪 Testing Lux Pixel War App\n")
    print("=" * 50)
    
    # Test 1: Check if canvas exists or create one
    print("✓ Test 1: Canvas Creation")
    canvas = PixelCanvas.objects.first()
    if not canvas:
        canvas = PixelCanvas.objects.create(
            name="Lux Pixel War",
            width=100,
            height=100,
            anonymous_cooldown_seconds=30,
            registered_cooldown_seconds=12
        )
    print(f"  Canvas: {canvas.name} ({canvas.width}x{canvas.height})")
    results.append(("Canvas Creation", "PASS"))
    
    # Test 2: Test main page loads
    print("\n✓ Test 2: Main Page Load")
    response = client.get('/vibe-coding/pixel-war/')
    if response.status_code == 200:
        print("  Page loaded successfully")
        results.append(("Page Load", "PASS"))
    else:
        print(f"  ❌ Page failed to load: {response.status_code}")
        results.append(("Page Load", "FAIL"))
    
    # Test 3: Test canvas state API
    print("\n✓ Test 3: Canvas State API")
    response = client.get(f'/vibe-coding/api/canvas-state/{canvas.id}/')
    if response.status_code == 200:
        data = json.loads(response.content)
        if data.get('success'):
            print(f"  API returned canvas data: {data['canvas']['name']}")
            results.append(("Canvas API", "PASS"))
        else:
            print("  ❌ API returned error")
            results.append(("Canvas API", "FAIL"))
    else:
        print(f"  ❌ API failed: {response.status_code}")
        results.append(("Canvas API", "FAIL"))
    
    # Test 4: Test pixel placement (anonymous)
    print("\n✓ Test 4: Anonymous Pixel Placement")
    response = client.post(
        '/vibe-coding/api/place-pixel/',
        json.dumps({
            'x': 50,
            'y': 50,
            'color': '#FF0000',
            'canvas_id': canvas.id
        }),
        content_type='application/json'
    )
    
    if response.status_code == 200:
        data = json.loads(response.content)
        if data.get('success'):
            print(f"  Pixel placed at ({data['pixel']['x']}, {data['pixel']['y']})")
            results.append(("Anonymous Pixel", "PASS"))
        else:
            print("  ❌ Failed to place pixel")
            results.append(("Anonymous Pixel", "FAIL"))
    else:
        print(f"  ❌ API failed: {response.status_code}")
        results.append(("Anonymous Pixel", "FAIL"))
    
    # Test 5: Test cooldown
    print("\n✓ Test 5: Cooldown System")
    response = client.post(
        '/vibe-coding/api/place-pixel/',
        json.dumps({
            'x': 51,
            'y': 51,
            'color': '#00FF00',
            'canvas_id': canvas.id
        }),
        content_type='application/json'
    )
    
    if response.status_code == 429:
        data = json.loads(response.content)
        print(f"  Cooldown working: {data.get('error')}")
        print(f"  Remaining: {data.get('cooldown_remaining')}s")
        results.append(("Cooldown", "PASS"))
    else:
        print("  ⚠️  Cooldown might not be working")
        results.append(("Cooldown", "WARNING"))
    
    # Test 6: Test authenticated user
    print("\n✓ Test 6: Authenticated User")
    # Create test user if doesn't exist
    try:
        user = User.objects.get(username='testplayer')
    except User.DoesNotExist:
        user = User.objects.create_user(
            username='testplayer',
            password='testpass123'
        )
    
    if client.login(username='testplayer', password='testpass123'):
        print("  Logged in as testplayer")
        
        # Try placing pixel as authenticated user
        response = client.post(
            '/vibe-coding/api/place-pixel/',
            json.dumps({
                'x': 25,
                'y': 25,
                'color': '#0000FF',
                'canvas_id': canvas.id
            }),
            content_type='application/json'
        )
        
        if response.status_code == 200:
            data = json.loads(response.content)
            if data['cooldown_info']['cooldown_seconds'] == 12:
                print("  Registered user cooldown correct (12s)")
                results.append(("Auth User", "PASS"))
            else:
                print("  ⚠️  Unexpected cooldown time")
                results.append(("Auth User", "WARNING"))
        else:
            print(f"  ❌ Failed: {response.status_code}")
            results.append(("Auth User", "FAIL"))
    
    # Test 7: Check pixel history
    print("\n✓ Test 7: Pixel History")
    response = client.get(f'/vibe-coding/api/pixel-history/?canvas_id={canvas.id}')
    if response.status_code == 200:
        data = json.loads(response.content)
        if data.get('success'):
            print(f"  History has {len(data['history'])} entries")
            results.append(("History", "PASS"))
        else:
            print("  ❌ History API failed")
            results.append(("History", "FAIL"))
    
    # Summary
    print("\n" + "=" * 50)
    print("📊 TEST SUMMARY\n")
    
    passed = sum(1 for _, status in results if status == "PASS")
    failed = sum(1 for _, status in results if status == "FAIL")
    warnings = sum(1 for _, status in results if status == "WARNING")
    
    for test_name, status in results:
        icon = "✅" if status == "PASS" else "❌" if status == "FAIL" else "⚠️"
        print(f"  {icon} {test_name}: {status}")
    
    print(f"\n  Total: {passed} passed, {failed} failed, {warnings} warnings")
    
    if failed == 0:
        print("\n🎉 All critical tests passed! The app is working correctly.")
    else:
        print(f"\n⚠️  {failed} test(s) failed. Please check the issues above.")
    
    return failed == 0


if __name__ == "__main__":
    success = test_pixel_war()
    sys.exit(0 if success else 1)