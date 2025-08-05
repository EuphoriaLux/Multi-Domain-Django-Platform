#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Test the API data for plot selection
"""

import requests
import json

def test_adoption_plans_api():
    """Test the adoption plans API endpoint"""
    
    api_url = "http://localhost:8000/de/vinsdelux/api/adoption-plans/"
    
    print("Testing Adoption Plans API")
    print("-" * 40)
    
    try:
        response = requests.get(api_url)
        
        if response.status_code == 200:
            data = response.json()
            
            print(f"Status: {response.status_code} OK")
            print(f"\nData structure:")
            
            # Check for adoption_plans
            if 'adoption_plans' in data:
                plans = data['adoption_plans']
                print(f"  - Found {len(plans)} adoption plans")
                
                if plans:
                    # Show first plan structure
                    first_plan = plans[0]
                    print(f"\nFirst adoption plan structure:")
                    for key in first_plan.keys():
                        value = first_plan[key]
                        if isinstance(value, dict):
                            print(f"    {key}: (dict with {len(value)} keys)")
                        elif isinstance(value, list):
                            print(f"    {key}: (list with {len(value)} items)")
                        else:
                            print(f"    {key}: {type(value).__name__}")
                    
                    # Show plan details
                    print(f"\nAdoption Plans Summary:")
                    for i, plan in enumerate(plans, 1):
                        print(f"\n  Plan {i}:")
                        print(f"    - ID: {plan.get('id', 'N/A')}")
                        print(f"    - Name: {plan.get('name', 'N/A')}")
                        print(f"    - Price: €{plan.get('price', 'N/A')}")
                        
                        producer = plan.get('producer', {})
                        print(f"    - Producer: {producer.get('name', 'N/A')}")
                        print(f"    - Region: {producer.get('region', 'N/A')}")
                        
                        features = plan.get('features', {})
                        print(f"    - Features:")
                        print(f"        Visit: {features.get('includes_visit', False)}")
                        print(f"        Medallion: {features.get('includes_medallion', False)}")
                        print(f"        Club: {features.get('includes_club_membership', False)}")
                else:
                    print("  ⚠️  No adoption plans in response")
            else:
                print("  ❌ Missing 'adoption_plans' key in response")
                
            # Check for filters
            if 'filters' in data:
                filters = data['filters']
                print(f"\nAvailable filters:")
                if 'regions' in filters:
                    print(f"  - Regions: {', '.join(filters['regions']) if filters['regions'] else 'None'}")
                if 'categories' in filters:
                    print(f"  - Categories: {', '.join(filters['categories']) if filters['categories'] else 'None'}")
            
        else:
            print(f"❌ API returned status {response.status_code}")
            print(f"Response: {response.text[:500]}")
            
    except requests.RequestException as e:
        print(f"❌ Error connecting to API: {e}")
    except json.JSONDecodeError as e:
        print(f"❌ Invalid JSON response: {e}")
    except Exception as e:
        print(f"❌ Unexpected error: {e}")

if __name__ == "__main__":
    test_adoption_plans_api()