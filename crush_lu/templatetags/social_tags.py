from django import template

register = template.Library()


@register.simple_tag(takes_context=True)
def get_email_providers(context):
    """Return dict mapping email addresses to their social provider names."""
    request = context.get("request")
    if not request or not request.user.is_authenticated:
        return {}
    email_providers = {}
    for sa in request.user.socialaccount_set.all():
        if sa.provider == "microsoft":
            email = (
                sa.extra_data.get("mail")
                or sa.extra_data.get("userPrincipalName")
                or sa.extra_data.get("email")
            )
        else:
            email = sa.extra_data.get("email")
        if email:
            email_providers.setdefault(email.lower(), []).append(sa.provider)
    return email_providers


@register.filter
def lookup(dictionary, key):
    """Look up a key in a dictionary. Returns None if not found."""
    if not isinstance(dictionary, dict):
        return None
    return dictionary.get(key)


@register.filter
def any_unverified(emailaddresses):
    """Return True if any email address in the list is unverified."""
    return any(not ea.verified for ea in emailaddresses)
