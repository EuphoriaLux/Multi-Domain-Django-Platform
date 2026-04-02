"""
View utility functions for Crush.lu
Helper functions for views to standardize responses, toasts, etc.
"""

import json
from django.http import HttpResponse


def toast_response(message, toast_type='info', status=200, additional_triggers=None):
    """
    Create an HTMX response with toast notification.

    Usage in views:
        return toast_response('Profile saved!', 'success')
        return toast_response('Error occurred', 'error', status=400)

    Args:
        message (str): Toast message to display
        toast_type (str): Toast type ('success', 'error', 'info', 'warning')
        status (int): HTTP status code (default: 200)
        additional_triggers (dict): Additional HTMX triggers to include

    Returns:
        HttpResponse with HX-Trigger header for toast
    """
    triggers = {
        'showToast': {
            'type': toast_type,
            'message': message
        }
    }

    if additional_triggers:
        triggers.update(additional_triggers)

    response = HttpResponse(status=status)
    response['HX-Trigger'] = json.dumps(triggers)
    return response


def add_toast_trigger(response, message, toast_type='info'):
    """
    Add toast trigger to existing HttpResponse.

    Usage in views:
        response = render(request, 'template.html', context)
        return add_toast_trigger(response, 'Welcome!', 'success')

    Args:
        response (HttpResponse): Existing response object
        message (str): Toast message
        toast_type (str): Toast type ('success', 'error', 'info', 'warning')

    Returns:
        Modified HttpResponse with HX-Trigger header
    """
    toast_data = {
        'showToast': {
            'type': toast_type,
            'message': message
        }
    }

    # Check if HX-Trigger already exists
    existing_trigger = response.get('HX-Trigger')
    if existing_trigger:
        try:
            triggers = json.loads(existing_trigger)
            triggers.update(toast_data)
            response['HX-Trigger'] = json.dumps(triggers)
        except json.JSONDecodeError:
            # If existing trigger is not JSON, replace it
            response['HX-Trigger'] = json.dumps(toast_data)
    else:
        response['HX-Trigger'] = json.dumps(toast_data)

    return response
