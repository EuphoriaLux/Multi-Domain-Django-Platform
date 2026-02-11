from django.contrib import admin

# ============================================================================
# CUSTOM ADMIN SITE - Power-Up Administration
# ============================================================================


class PowerUpAdminSite(admin.AdminSite):
    """
    Custom admin site for Power-Up corporate/investor site.

    This admin panel manages the power-up.lu and powerup.lu domains.
    Currently a static site, but this admin provides a consistent
    experience across all platform admin panels.
    """

    site_header = "Power-Up Administration"
    site_title = "Power-Up Admin"
    index_title = "Corporate Site Management"
    index_template = "admin/power_up_index.html"

    def each_context(self, request):
        context = super().each_context(request)
        context["crm_dashboard_url"] = "/crm/"
        return context

    def get_app_list(self, request, app_label=None):
        """
        Override to group CRM and FinOps models into logical sidebar sections.
        """
        app_list = super().get_app_list(request, app_label)

        # Model → group/order/icon mapping (keys are lowercase object_name)
        custom_order = {
            # GROUP 1: Customers
            "customergroup": {"order": 0, "icon": "🏢", "group": "Customers"},
            "entity": {"order": 1, "icon": "🏛️", "group": "Customers"},
            "tenant": {"order": 2, "icon": "☁️", "group": "Customers"},
            # GROUP 2: Contacts & Access
            "authorizedcontact": {
                "order": 0,
                "icon": "👤",
                "group": "Contacts & Access",
            },
            "contacttenantpermission": {
                "order": 1,
                "icon": "🔐",
                "group": "Contacts & Access",
            },
            "userrole": {"order": 2, "icon": "🎭", "group": "Contacts & Access"},
            # GROUP 3: Plans & Contracts
            "plan": {"order": 0, "icon": "📋", "group": "Plans & Contracts"},
            "contract": {"order": 1, "icon": "📄", "group": "Plans & Contracts"},
            # GROUP 4: Account Management
            "accountmanager": {"order": 0, "icon": "👔", "group": "Account Management"},
            "groupaccountmanager": {
                "order": 1,
                "icon": "🤝",
                "group": "Account Management",
            },
            # GROUP 5: Support Tickets
            "ticket": {"order": 0, "icon": "🎫", "group": "Support Tickets"},
            "ticketcomment": {
                "order": 1,
                "icon": "💬",
                "group": "Support Tickets",
            },
            "ticketusageperiod": {
                "order": 2,
                "icon": "📊",
                "group": "Support Tickets",
            },
            # GROUP 6: Onboarding
            "onboardingsession": {"order": 0, "icon": "🚀", "group": "Onboarding"},
            "onboardingemail": {"order": 1, "icon": "📧", "group": "Onboarding"},
            # GROUP 7: FinOps Hub
            "costexport": {"order": 0, "icon": "📊", "group": "FinOps Hub"},
            "costrecord": {"order": 1, "icon": "💰", "group": "FinOps Hub"},
            "costaggregation": {"order": 2, "icon": "📈", "group": "FinOps Hub"},
            "costanomaly": {"order": 3, "icon": "⚠️", "group": "FinOps Hub"},
            "costforecast": {"order": 4, "icon": "🔮", "group": "FinOps Hub"},
            "reservationcost": {"order": 5, "icon": "🏷️", "group": "FinOps Hub"},
            "costbudget": {"order": 6, "icon": "💵", "group": "FinOps Hub"},
        }

        # Ordered display groups
        group_order = [
            ("🏢 Customers", "Customers"),
            ("👤 Contacts & Access", "Contacts & Access"),
            ("📋 Plans & Contracts", "Plans & Contracts"),
            ("👔 Account Management", "Account Management"),
            ("🎫 Support Tickets", "Support Tickets"),
            ("🚀 Onboarding", "Onboarding"),
            ("📊 FinOps Hub", "FinOps Hub"),
        ]

        new_app_list = []
        grouped_labels = {"crm", "finops", "onboarding"}

        for app in app_list:
            if app["app_label"] not in grouped_labels:
                new_app_list.append(app)
                continue

            # Distribute models into groups
            for model in app["models"]:
                model_key = model["object_name"].lower()
                config = custom_order.get(model_key)
                if not config:
                    continue
                model["_order"] = config["order"]
                icon = config["icon"]
                if not model["name"].startswith(icon):
                    model["name"] = f"{icon} {model['name']}"

        # Build grouped sections from all collected models
        groups = {}
        for app in app_list:
            if app["app_label"] not in grouped_labels:
                continue
            for model in app["models"]:
                model_key = model["object_name"].lower()
                config = custom_order.get(model_key)
                if not config:
                    continue
                group_name = config["group"]
                groups.setdefault(group_name, []).append(model)

        for display_name, group_key in group_order:
            if group_key in groups:
                groups[group_key].sort(key=lambda x: x.get("_order", 999))
                new_app_list.append(
                    {
                        "name": display_name,
                        "app_label": f'power_up_{group_key.lower().replace(" ", "_").replace("&", "and")}',
                        "app_url": "#",
                        "has_module_perms": True,
                        "models": groups[group_key],
                    }
                )

        return new_app_list


# Instantiate the custom admin site
power_up_admin_site = PowerUpAdminSite(name="power_up_admin")


# ============================================================================
# IMPORT SUBMODULE ADMIN REGISTRATIONS
# ============================================================================
# Import finops admin to register models with power_up_admin_site
# This must be done after power_up_admin_site is created
from power_up.finops import admin as finops_admin  # noqa: F401, E402
from power_up.crm import admin as crm_admin  # noqa: F401, E402
from power_up.onboarding import admin as onboarding_admin  # noqa: F401, E402
