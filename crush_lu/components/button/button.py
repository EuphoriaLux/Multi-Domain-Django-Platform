from django_components import Component, register

@register("button")
class Button(Component):
    template_name = "button/button.html"

    def get_context_data(self, variant="primary", size="md", type="button", disabled=False):
        """
        Button component with Crush.lu styling.

        Args:
            variant: Button style variant (primary, outline, gradient, danger)
            size: Button size (sm, md, lg)
            type: HTML button type (button, submit, reset)
            disabled: Whether button is disabled
        """
        # Map variants to CSS classes
        variant_classes = {
            "primary": "btn-crush-primary",
            "outline": "btn-crush-outline",
            "gradient": "btn-crush-gradient",
            "danger": "btn-crush-danger",
        }

        # Map sizes to CSS classes
        size_classes = {
            "sm": "btn-sm",
            "md": "",  # Default size
            "lg": "btn-lg",
        }

        return {
            "variant": variant,
            "size": size,
            "type": type,
            "disabled": disabled,
            "variant_class": variant_classes.get(variant, "btn-crush-primary"),
            "size_class": size_classes.get(size, ""),
        }
