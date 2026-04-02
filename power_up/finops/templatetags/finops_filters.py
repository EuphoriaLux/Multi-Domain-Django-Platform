from django import template

register = template.Library()


@register.filter
def format_currency(value):
    """
    Format a number as currency with thousand separators and 2 decimal places.
    Returns string like "12,345.67".

    Usage: {{ cost|format_currency }}
    """
    try:
        value = float(value)
        return "{:,.2f}".format(value)
    except (ValueError, TypeError):
        return "0.00"


@register.filter
def percentage(value, total):
    """
    Calculate percentage with precision for CSS width.
    Returns a float percentage value (0-100) with US decimal format (dot separator).
    Always uses period as decimal separator for CSS compatibility.

    Usage: {{ service.cost|percentage:summary.total_cost }}
    """
    try:
        # Convert to Decimal for precise calculation
        if isinstance(value, str):
            value = value.replace(',', '.')
        if isinstance(total, str):
            total = total.replace(',', '.')

        value = float(value)
        total = float(total)

        if total == 0:
            return "0"

        pct = (value / total) * 100
        # Format with period as decimal separator for CSS
        return "{:.10f}".format(pct).rstrip('0').rstrip('.')
    except (ValueError, TypeError, ZeroDivisionError):
        return "0"


@register.filter
def format_percentage(value, total):
    """
    Calculate and format percentage as string with % symbol.
    Returns formatted string like "65%".

    Usage: {{ service.cost|format_percentage:summary.total_cost }}
    """
    try:
        # Convert to float, handling comma decimal separators
        if isinstance(value, str):
            value = value.replace(',', '.')
        if isinstance(total, str):
            total = total.replace(',', '.')

        value = float(value)
        total = float(total)

        if total == 0:
            return "0%"

        pct = (value / total) * 100
        # Round to integer for display
        return f"{int(round(pct))}%"
    except (ValueError, TypeError, ZeroDivisionError):
        return "0%"
