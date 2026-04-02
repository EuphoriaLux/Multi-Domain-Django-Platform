from django_components import Component, register

@register("card")
class Card(Component):
    template_name = "card/card.html"

    def get_context_data(self, padding=True, variant="default"):
        """
        Card component with Crush.lu styling.

        Args:
            padding: Whether to add default padding (default: True)
            variant: Card style variant (default, section, hero)
        """
        variant_classes = {
            "default": "card",
            "section": "section-card",
            "hero": "hero-section",
        }

        return {
            "padding": padding,
            "variant": variant,
            "variant_class": variant_classes.get(variant, "card"),
        }
