"""
Custom template filters for math operations.

Usage:
    {% load math_filters %}
    {{ value|mul:100 }}
    {{ value|div:total }}
    {{ value|percentage:total }}
"""
from django import template

register = template.Library()


@register.filter
def mul(value, arg):
    """Multiply the value by the argument."""
    try:
        return float(value) * float(arg)
    except (ValueError, TypeError):
        return 0


@register.filter
def div(value, arg):
    """Divide the value by the argument."""
    try:
        arg = float(arg)
        if arg == 0:
            return 0
        return float(value) / arg
    except (ValueError, TypeError):
        return 0


@register.filter
def percentage(value, total):
    """Calculate the percentage of value relative to total."""
    try:
        total = float(total)
        if total == 0:
            return 0
        return (float(value) / total) * 100
    except (ValueError, TypeError):
        return 0


@register.filter
def get_item(dictionary, key):
    """
    Get an item from a dictionary by key.

    Usage:
        {{ my_dict|get_item:key_variable }}
    """
    if dictionary is None:
        return None
    try:
        return dictionary.get(key)
    except (AttributeError, TypeError):
        return None
