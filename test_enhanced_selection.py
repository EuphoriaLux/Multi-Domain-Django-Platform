#!/usr/bin/env python
"""
Test script to verify enhanced plot selection functionality
"""

import os
import django
import sys

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'azureproject.settings')
django.setup()

from vinsdelux.models import VdlPlot, VdlProducer, PlotStatus
from django.contrib.auth.models import User
import json

def test_plot_data():
    """Test that plot data is properly formatted for the frontend"""
    plots = VdlPlot.objects.select_related('producer').filter(status=PlotStatus.AVAILABLE)
    
    print(f"Found {plots.count()} available plots")
    
    for plot in plots[:3]:  # Test first 3 plots
        print(f"\nPlot: {plot.name} ({plot.plot_identifier})")
        print(f"  Producer: {plot.producer.name if plot.producer else 'No producer'}")
        print(f"  Coordinates: lat={plot.latitude}, lng={plot.longitude}")
        print(f"  Status: {plot.status}")
        print(f"  Price: €{plot.base_price}")
        print(f"  Size: {plot.plot_size}")
        print(f"  Elevation: {plot.elevation}")
        print(f"  Grape varieties: {plot.grape_varieties}")

def test_api_endpoints():
    """Test API endpoints"""
    from django.test import Client
    client = Client()
    
    # Test plot list API
    response = client.get('/vinsdelux/api/plots/')
    print(f"\n/api/plots/ status: {response.status_code}")
    if response.status_code == 200:
        data = response.json()
        print(f"  Returned {len(data)} plots")
    
    # Test plot availability API
    response = client.get('/vinsdelux/api/plots/availability/')
    print(f"\n/api/plots/availability/ status: {response.status_code}")
    if response.status_code == 200:
        data = response.json()
        print(f"  Available: {data.get('available_count', 0)}")
        print(f"  Reserved: {data.get('reserved_count', 0)}")
        print(f"  Adopted: {data.get('adopted_count', 0)}")

def check_static_files():
    """Check if our new JavaScript and CSS files exist"""
    from django.conf import settings
    import os
    
    files_to_check = [
        'vinsdelux/js/vineyard-map.js',
        'vinsdelux/js/plot-selection.js',
        'vinsdelux/css/vineyard-map.css',
        'vinsdelux/css/plot-selection.css'
    ]
    
    print("\nChecking static files:")
    for file_path in files_to_check:
        full_path = os.path.join(settings.STATICFILES_DIRS[0], file_path)
        exists = os.path.exists(full_path)
        print(f"  {file_path}: {'✓ Found' if exists else '✗ Not found'}")
        if exists:
            size = os.path.getsize(full_path)
            print(f"    Size: {size:,} bytes")

def test_enhanced_view():
    """Test the enhanced plot selection view"""
    from django.test import Client
    client = Client()
    
    response = client.get('/vinsdelux/journey/enhanced-plot-selection/')
    print(f"\nEnhanced plot selection view status: {response.status_code}")
    
    if response.status_code == 200:
        content = response.content.decode('utf-8')
        
        # Check for key elements
        checks = [
            ('Leaflet map container', 'id="vineyard-map"' in content),
            ('Plot selection JS', 'plot-selection.js' in content),
            ('Vineyard map JS', 'vineyard-map.js' in content),
            ('Leaflet CSS', 'leaflet.css' in content),
            ('MarkerCluster', 'markercluster' in content.lower()),
            ('Plot data JSON', 'plotsData' in content)
        ]
        
        print("Content checks:")
        for check_name, result in checks:
            print(f"  {check_name}: {'✓' if result else '✗'}")

if __name__ == '__main__':
    print("=" * 60)
    print("Testing Enhanced Plot Selection System")
    print("=" * 60)
    
    try:
        test_plot_data()
        test_api_endpoints()
        check_static_files()
        test_enhanced_view()
        
        print("\n" + "=" * 60)
        print("✓ All tests completed!")
        print("=" * 60)
        print("\nYou can now visit:")
        print("  http://localhost:8000/vinsdelux/journey/enhanced-plot-selection/")
        print("to see the interactive map in action!")
        
    except Exception as e:
        print(f"\n✗ Error during testing: {e}")
        import traceback
        traceback.print_exc()