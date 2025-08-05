#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Test script to check the VinsDelux plot selection page
"""

import requests
from bs4 import BeautifulSoup
import json
import sys
import os

# Set UTF-8 encoding for Windows console
if sys.platform == 'win32':
    os.system('chcp 65001 > nul')
    sys.stdout.reconfigure(encoding='utf-8')

def test_plot_selection_page():
    """Test the plot selection page for any issues"""
    
    url = "http://localhost:8000/de/vinsdelux/journey/plot-selection/"
    
    print("=" * 60)
    print("Testing VinsDelux Plot Selection Page")
    print("=" * 60)
    print(f"\n📍 Testing URL: {url}\n")
    
    try:
        # Make request to the page
        response = requests.get(url, timeout=10)
        
        # Check status code
        print(f"✅ Status Code: {response.status_code}")
        
        if response.status_code != 200:
            print(f"❌ Error: Expected status 200, got {response.status_code}")
            print(f"Response: {response.text[:500]}...")
            return False
            
        # Parse HTML
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Check for Django error messages
        if "Traceback" in response.text or "Exception" in response.text:
            print("❌ Django Exception Found!")
            error_details = soup.find('div', class_='exception_value')
            if error_details:
                print(f"Error: {error_details.text}")
            return False
            
        print("✅ No Django exceptions found")
        
        # Check for key elements
        print("\n🔍 Checking Page Elements:")
        
        # Check title
        title = soup.find('title')
        if title:
            print(f"  ✅ Page Title: {title.text.strip()}")
        else:
            print("  ❌ No title found")
            
        # Check for main container
        plot_container = soup.find('div', class_='plot-selector-container')
        if plot_container:
            print("  ✅ Plot selector container found")
        else:
            print("  ❌ Plot selector container NOT found")
            
        # Check for filter bar
        filter_bar = soup.find('div', class_='filter-bar')
        if filter_bar:
            print("  ✅ Filter bar found")
        else:
            print("  ❌ Filter bar NOT found")
            
        # Check for plot cards container
        plots_grid = soup.find('div', id='plots-grid')
        if plots_grid:
            print("  ✅ Plots grid found")
            plot_cards = soup.find('div', class_='plot-cards-container')
            if plot_cards:
                print("  ✅ Plot cards container found")
            else:
                print("  ⚠️  Plot cards container NOT found inside grid")
        else:
            print("  ❌ Plots grid NOT found")
            
        # Check for JavaScript files
        print("\n📜 Checking JavaScript Files:")
        scripts = soup.find_all('script', src=True)
        js_files = [s['src'] for s in scripts]
        
        # Look for our custom JS files
        plot_selector_js = any('vinsdelux-plot-selector' in js for js in js_files)
        if plot_selector_js:
            print("  ✅ Plot selector JavaScript found")
        else:
            print("  ⚠️  Plot selector JavaScript NOT found")
            
        # Check for CSS files
        print("\n🎨 Checking CSS Files:")
        stylesheets = soup.find_all('link', rel='stylesheet')
        css_files = [s.get('href', '') for s in stylesheets]
        
        plot_selector_css = any('vinsdelux-plot-selector' in css for css in css_files)
        if plot_selector_css:
            print("  ✅ Plot selector CSS found")
        else:
            print("  ⚠️  Plot selector CSS may be embedded or missing")
            
        # Check for map elements
        print("\n🗺️  Checking Map Elements:")
        map_canvas = soup.find('canvas', id='map-canvas')
        if map_canvas:
            print("  ✅ Map canvas found")
        else:
            print("  ❌ Map canvas NOT found")
            
        map_markers = soup.find('div', id='map-markers')
        if map_markers:
            print("  ✅ Map markers container found")
        else:
            print("  ❌ Map markers container NOT found")
            
        # Check for view toggle buttons
        print("\n🔄 Checking View Controls:")
        grid_view_btn = soup.find('button', id='grid-view-btn')
        map_view_btn = soup.find('button', id='map-view-btn')
        
        if grid_view_btn:
            print("  ✅ Grid view button found")
        else:
            print("  ❌ Grid view button NOT found")
            
        if map_view_btn:
            print("  ✅ Map view button found")
        else:
            print("  ❌ Map view button NOT found")
            
        # Check for plot details panel
        plot_details = soup.find('div', id='plot-details')
        if plot_details:
            print("  ✅ Plot details panel found")
        else:
            print("  ❌ Plot details panel NOT found")
            
        # Test API endpoint
        print("\n🔌 Testing API Endpoint:")
        api_url = "http://localhost:8000/de/vinsdelux/api/adoption-plans/"
        try:
            api_response = requests.get(api_url, timeout=5)
            if api_response.status_code == 200:
                print(f"  ✅ API endpoint accessible: {api_url}")
                try:
                    data = api_response.json()
                    if 'adoption_plans' in data:
                        plan_count = len(data['adoption_plans'])
                        print(f"  ✅ API returned {plan_count} adoption plans")
                    else:
                        print("  ⚠️  API response missing 'adoption_plans' key")
                except json.JSONDecodeError:
                    print("  ❌ API response is not valid JSON")
            else:
                print(f"  ❌ API returned status {api_response.status_code}")
        except requests.RequestException as e:
            print(f"  ❌ Could not reach API: {e}")
            
        # Summary
        print("\n" + "=" * 60)
        print("📊 Test Summary:")
        print("  - Page loads successfully")
        print("  - Check console for JavaScript errors")
        print("  - Verify plot selection interactivity manually")
        print("=" * 60)
        
        return True
        
    except requests.ConnectionError:
        print("❌ Could not connect to server at http://localhost:8000")
        print("   Make sure the Django development server is running")
        return False
    except requests.Timeout:
        print("❌ Request timed out")
        return False
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        return False

if __name__ == "__main__":
    success = test_plot_selection_page()
    sys.exit(0 if success else 1)