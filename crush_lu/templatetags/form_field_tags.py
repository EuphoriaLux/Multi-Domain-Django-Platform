"""Helpers for crush_lu/components/form_field.html."""

from django import template

register = template.Library()


@register.filter
def with_descriptions(bound_field, custom_help=""):
    """Render a field's widget with every description id in aria-describedby.

    Django points aria-describedby at ``<auto_id>_helptext`` and
    ``<auto_id>_error`` on its own, but adds neither when the widget already
    sets aria-describedby, and it cannot know about help that form_field.html
    receives from its caller, rendered as ``<auto_id>_help``. In those two
    cases the list is built here: the widget's own ids first, then the ones
    form_field.html renders. Otherwise Django's rendering is left alone.
    """
    auto_id = bound_field.auto_id
    preset = bound_field.field.widget.attrs.get("aria-describedby", "")
    # Fieldset widgets (radios, checkbox groups) describe the fieldset, not
    # the inputs, so Django adds nothing there and neither does this.
    if (
        not (preset or custom_help)
        or not auto_id
        or bound_field.is_hidden
        or bound_field.use_fieldset
    ):
        return bound_field
    ids = preset.split()
    if bound_field.help_text:
        ids.append(f"{auto_id}_helptext")
    if bound_field.errors:
        ids.append(f"{auto_id}_error")
    if custom_help:
        ids.append(f"{auto_id}_help")
    describedby = " ".join(dict.fromkeys(ids))
    rendered = bound_field.as_widget(attrs={"aria-describedby": describedby})
    if bound_field.field.show_hidden_initial:
        rendered += bound_field.as_hidden(only_initial=True)
    return rendered
