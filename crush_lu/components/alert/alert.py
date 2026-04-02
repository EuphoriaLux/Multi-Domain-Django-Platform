from django_components import Component, register

@register("alert")
class Alert(Component):
    template_name = "alert/alert.html"

    def get_context_data(self, type="info", dismissible=False):
        """
        Alert/notification component with Crush.lu styling.

        Args:
            type: Alert type (success, warning, danger, info)
            dismissible: Whether alert can be dismissed (requires Alpine.js)
        """
        # Map types to CSS classes and icons
        type_config = {
            "success": {
                "class": "bg-green-50 border-green-200 text-green-800",
                "icon": "check-circle",
            },
            "warning": {
                "class": "bg-amber-50 border-amber-200 text-amber-800",
                "icon": "exclamation-triangle",
            },
            "danger": {
                "class": "bg-red-50 border-red-200 text-red-800",
                "icon": "x-circle",
            },
            "info": {
                "class": "bg-blue-50 border-blue-200 text-blue-800",
                "icon": "information-circle",
            },
        }

        config = type_config.get(type, type_config["info"])

        return {
            "type": type,
            "dismissible": dismissible,
            "alert_class": config["class"],
            "icon": config["icon"],
        }
