"""
VinsDelux URL Configuration - Simplified Portfolio Version

This is now a simple portfolio showcase with just the homepage.
All product, journey, and API URLs have been removed.
Original functionality is preserved in git history.
"""

from django.urls import path
from . import views

app_name = 'vinsdelux'

urlpatterns = [
    # Homepage - simple portfolio showcase
    path('', views.home, name='home'),
]

# ===== ARCHIVED URL PATTERNS =====
# All removed URLs are preserved in git history and can be restored if needed.
# Removed: coffret_list, coffret_detail, adoption_plan_detail, producer_list,
#          producer_detail, plot_selector, enhanced_plot_selector, API endpoints,
#          journey interactive forms, test runners, and journey step landing pages.
