# -*- coding: utf-8 -*-
import sys
import io
import os

# Set UTF-8 encoding
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

from playwright.sync_api import sync_playwright

os.makedirs('screenshots/admin-review', exist_ok=True)

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(viewport={'width': 1920, 'height': 1080})
    page = context.new_page()
    
    page.goto('http://crush.localhost:8000/accounts/login/')
    page.wait_for_load_state('networkidle')
    
    try:
        page.click('button:has-text("Accept All")', timeout=2000)
        page.wait_for_timeout(500)
    except:
        pass
    
    page.fill('input[name="login"]', 'test@test.lu')
    page.fill('input[name="password"]', 'test')
    page.click('button:has-text("Login")')
    page.wait_for_load_state('networkidle')
    page.wait_for_timeout(1000)
    
    print('After login:', page.url)
    
    page.goto('http://crush.localhost:8000/crush-admin/')
    page.wait_for_load_state('networkidle')
    page.wait_for_timeout(1000)
    
    print('Admin index:', page.url)
    page.screenshot(path='screenshots/admin-review/01-admin-index.png', full_page=True)
    print('Captured: 01-admin-index.png')
    
    page.goto('http://crush.localhost:8000/crush-admin/dashboard/')
    page.wait_for_load_state('networkidle')
    page.wait_for_timeout(1000)
    
    print('Dashboard:', page.url)
    page.screenshot(path='screenshots/admin-review/02-admin-dashboard-full.png', full_page=True)
    print('Captured: 02-admin-dashboard-full.png')
    
    page.evaluate('window.scrollTo(0, 0)')
    page.wait_for_timeout(500)
    page.screenshot(path='screenshots/admin-review/03-dashboard-top.png')
    print('Captured: 03-dashboard-top.png')
    
    page.evaluate('window.scrollTo(0, 800)')
    page.wait_for_timeout(500)
    page.screenshot(path='screenshots/admin-review/04-user-metrics.png')
    print('Captured: 04-user-metrics.png')
    
    page.evaluate('window.scrollTo(0, 1600)')
    page.wait_for_timeout(500)
    page.screenshot(path='screenshots/admin-review/05-funnel-demographics.png')
    print('Captured: 05-funnel-demographics.png')
    
    page.evaluate('window.scrollTo(0, 2400)')
    page.wait_for_timeout(500)
    page.screenshot(path='screenshots/admin-review/06-coach-events.png')
    print('Captured: 06-coach-events.png')
    
    page.evaluate('window.scrollTo(0, 3200)')
    page.wait_for_timeout(500)
    page.screenshot(path='screenshots/admin-review/07-connections-journey.png')
    print('Captured: 07-connections-journey.png')
    
    page.evaluate('window.scrollTo(0, 4000)')
    page.wait_for_timeout(500)
    page.screenshot(path='screenshots/admin-review/08-email-pwa.png')
    print('Captured: 08-email-pwa.png')
    
    page.evaluate('window.scrollTo(0, 5000)')
    page.wait_for_timeout(500)
    page.screenshot(path='screenshots/admin-review/09-recent-activity.png')
    print('Captured: 09-recent-activity.png')
    
    browser.close()
    print('All screenshots captured!')
