# vinsdelux/views.py
"""
Simplified VinsDelux views - Portfolio showcase only.

This is now a lightweight demonstration of the wine adoption concept.
All complex product views, API endpoints, and interactive features have been removed.
Original functionality is preserved in git history (commit before this change).
"""

from django.shortcuts import render


def home(request):
    """
    Simple portfolio homepage for VinsDelux wine adoption concept.
    No database queries - all content is static for fast loading.
    """
    # Static journey steps - hardcoded for performance
    client_journey_steps = [
        {
            'step': '01',
            'title': 'Plot Selection',
            'description': 'Choose the vineyard plot that matches your preferences and vision. Each plot offers a unique terroir and a story to tell.',
        },
        {
            'step': '02',
            'title': 'Personalize Your Wine',
            'description': 'Collaborate with our expert winemakers to personalize every aspect of your wine, from the grape variety to the winemaking techniques.',
        },
        {
            'step': '03',
            'title': 'Follow the Production',
            'description': 'Receive regular updates on the growth of your vine and the winemaking process, complete with detailed photos and reports.',
        },
        {
            'step': '04',
            'title': 'Receive and Taste',
            'description': 'Your personalized bottles are delivered directly to your home, ready to be tasted and shared with loved ones.',
        },
        {
            'step': '05',
            'title': 'Create Your Legacy',
            'description': 'Your wine becomes a family legacy, a unique expression of your passion for wine, and an investment in the future.',
        }
    ]

    context = {
        'client_journey_steps': client_journey_steps,
    }
    return render(request, 'vinsdelux/index.html', context)


# ===== ARCHIVED CODE =====
# All complex views have been removed. If you need to restore functionality,
# check git history before this simplification commit.
#
# Removed views:
# - coffret_list, coffret_detail, adoption_plan_detail
# - producer_list, producer_detail
# - plot_selector, EnhancedPlotSelectionView, enhanced_plot_selector
# - PlotListAPIView, PlotDetailAPIView, PlotAvailabilityAPIView
# - PlotReservationAPIView, PlotSelectionAPIView
# - journey_interactive_form, journey_test_runner, journey_test
# - journey_step_plot_selection, journey_step_personalize_wine
# - journey_step_follow_production, journey_step_receive_taste
# - journey_step_create_legacy
# - api_adoption_plans
