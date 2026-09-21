"""Helpers for crush_lu/components/form_field.html."""

from django import template

register = template.Library()


@register.filter
def with_custom_help(bound_field):
    """Render a field's widget with the caller's ``help=`` text in aria-describedby.

    Django points aria-describedby at ``<auto_id>_helptext`` and
    ``<auto_id>_error`` on its own, but it cannot know about help that
    form_field.html receives from its caller, rendered as ``<auto_id>_help``.
    An aria-describedby passed in ``attrs`` replaces Django's list rather
    than extending it, so Django's own ids are carried over here.
    """
    auto_id = bound_field.auto_id
    # Fieldset widgets (radios, checkbox groups) describe the fieldset, not
    # the inputs, so Django adds nothing there and neither does this.
    if not auto_id or bound_field.is_hidden or bound_field.use_fieldset:
        return bound_field
    ids = (
        bound_field.field.widget.attrs.get("aria-describedby")
        or bound_field.aria_describedby
        or ""
    ).split()
    ids.append(f"{auto_id}_help")
    rendered = bound_field.as_widget(attrs={"aria-describedby": " ".join(ids)})
    if bound_field.field.show_hidden_initial:
        rendered += bound_field.as_hidden(only_initial=True)
    return rendered
