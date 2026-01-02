"""
Custom admin site configuration for Crush.lu Coach Panel.

Provides a customized admin interface with:
- Coach-only access control
- Grouped model organization
- Custom dashboard integration
"""

from django.contrib import admin
from django.urls import reverse


class CrushLuAdminSite(admin.AdminSite):
    site_header = '💕 Crush.lu Coach Panel'
    site_title = 'Crush.lu Coach Panel'
    index_title = 'Welcome to Crush.lu Coach Management'

    def has_permission(self, request):
        """
        Custom permission check: Only Crush coaches can access this admin panel.
        Superusers can always access.

        Note: We override the default is_staff check to allow coaches access.
        """
        # Superusers always have access
        if request.user.is_superuser:
            return True

        # Check if user is authenticated
        if not request.user.is_authenticated:
            return False

        # Check if user is an active Crush coach
        try:
            coach = request.user.crushcoach
            # Grant access to active coaches even if they're not staff
            if coach.is_active:
                return True
        except:
            pass

        # Fallback to default staff check
        return request.user.is_active and request.user.is_staff

    def has_module_perms(self, request, app_label):
        """
        Allow coaches to see all Crush.lu models.
        """
        if not self.has_permission(request):
            return False

        # Coaches can see crush_lu app
        if app_label == 'crush_lu':
            return True

        # Superusers can see everything
        if request.user.is_superuser:
            return True

        # Default Django permission check for other apps
        return super().has_module_perms(request, app_label)

    def index(self, request, extra_context=None):
        """
        Override index to add custom dashboard link and analytics.
        """
        extra_context = extra_context or {}
        extra_context['show_dashboard_link'] = True
        extra_context['dashboard_url'] = reverse('crush_admin_dashboard')

        # Add coach information to context
        try:
            coach = request.user.crushcoach
            extra_context['is_coach'] = True
            extra_context['coach_name'] = request.user.get_full_name() or request.user.username
        except:
            extra_context['is_coach'] = False

        return super().index(request, extra_context)

    def get_app_list(self, request, app_label=None):
        """
        Override to customize the admin index page grouping.
        Groups models into logical categories for better organization.
        """
        app_list = super().get_app_list(request, app_label)

        # Custom ordering and grouping for admin sidebar
        # Each group uses sequential ordering (1, 2, 3...) for internal sorting
        # Icons provide visual identification without cluttering with numbers
        custom_order = {
            # ═══════════════════════════════════════════════════════════════════
            # GROUP 1: Users & Profiles (Core user management)
            # ═══════════════════════════════════════════════════════════════════
            'crushprofile': {'order': 1, 'icon': '👤', 'group': 'Users & Profiles'},
            'profilesubmission': {'order': 2, 'icon': '📝', 'group': 'Users & Profiles'},
            'crushcoach': {'order': 3, 'icon': '🎓', 'group': 'Users & Profiles'},
            'coachsession': {'order': 4, 'icon': '💬', 'group': 'Users & Profiles'},

            # ═══════════════════════════════════════════════════════════════════
            # GROUP 2: Events & Meetups (Event management)
            # ═══════════════════════════════════════════════════════════════════
            'meetupevent': {'order': 1, 'icon': '🎉', 'group': 'Events & Meetups'},
            'eventregistration': {'order': 2, 'icon': '✅', 'group': 'Events & Meetups'},
            'eventinvitation': {'order': 3, 'icon': '💌', 'group': 'Events & Meetups'},
            'speeddatingpair': {'order': 4, 'icon': '💑', 'group': 'Events & Meetups'},
            'presentationqueue': {'order': 5, 'icon': '📋', 'group': 'Events & Meetups'},
            'presentationrating': {'order': 6, 'icon': '⭐', 'group': 'Events & Meetups'},

            # ═══════════════════════════════════════════════════════════════════
            # GROUP 3: Activity Voting (Event activity polls)
            # ═══════════════════════════════════════════════════════════════════
            'globalactivityoption': {'order': 1, 'icon': '🌐', 'group': 'Activity Voting'},
            'eventactivityoption': {'order': 2, 'icon': '🎯', 'group': 'Activity Voting'},
            'eventactivityvote': {'order': 3, 'icon': '🗳️', 'group': 'Activity Voting'},
            'eventvotingsession': {'order': 4, 'icon': '⏱️', 'group': 'Activity Voting'},

            # ═══════════════════════════════════════════════════════════════════
            # GROUP 4: Connections & Messages (Post-event interactions)
            # ═══════════════════════════════════════════════════════════════════
            'eventconnection': {'order': 1, 'icon': '🔗', 'group': 'Connections'},
            'connectionmessage': {'order': 2, 'icon': '💬', 'group': 'Connections'},

            # ═══════════════════════════════════════════════════════════════════
            # GROUP 5: Special Journey System (VIP personalized experiences)
            # ═══════════════════════════════════════════════════════════════════
            'special_user_experience': {'order': 1, 'icon': '✨', 'group': 'Special Journey'},
            'journeyconfiguration': {'order': 2, 'icon': '🗺️', 'group': 'Special Journey'},
            'journeychapter': {'order': 3, 'icon': '📖', 'group': 'Special Journey'},
            'journeychallenge': {'order': 4, 'icon': '🎯', 'group': 'Special Journey'},
            'journeyreward': {'order': 5, 'icon': '🎁', 'group': 'Special Journey'},
            'journeyprogress': {'order': 6, 'icon': '📊', 'group': 'Special Journey'},
            'chapterprogress': {'order': 7, 'icon': '📈', 'group': 'Special Journey'},
            'challengeattempt': {'order': 8, 'icon': '🎮', 'group': 'Special Journey'},
            'rewardprogress': {'order': 9, 'icon': '🏆', 'group': 'Special Journey'},

            # ═══════════════════════════════════════════════════════════════════
            # GROUP 6: Advent Calendar (Seasonal feature)
            # ═══════════════════════════════════════════════════════════════════
            'adventcalendar': {'order': 1, 'icon': '🎄', 'group': 'Advent Calendar'},
            'adventdoor': {'order': 2, 'icon': '🚪', 'group': 'Advent Calendar'},
            'adventdoorcontent': {'order': 3, 'icon': '📦', 'group': 'Advent Calendar'},
            'adventprogress': {'order': 4, 'icon': '📊', 'group': 'Advent Calendar'},
            'qrcodetoken': {'order': 5, 'icon': '📱', 'group': 'Advent Calendar'},

            # ═══════════════════════════════════════════════════════════════════
            # GROUP 7: Notifications & Settings (User preferences)
            # ═══════════════════════════════════════════════════════════════════
            'pushsubscription': {'order': 1, 'icon': '🔔', 'group': 'Notifications'},
            'coachpushsubscription': {'order': 2, 'icon': '📣', 'group': 'Notifications'},
            'emailpreference': {'order': 3, 'icon': '📧', 'group': 'Notifications'},
            'useractivity': {'order': 4, 'icon': '📊', 'group': 'Notifications'},
            'profilereminder': {'order': 5, 'icon': '📬', 'group': 'Notifications'},
        }

        # Create grouped app list - transform single crush_lu app into multiple sections
        new_app_list = []

        for app in app_list:
            if app['app_label'] == 'crush_lu':
                # Group models by category
                groups = {}

                for model in app['models']:
                    model_name = model['object_name'].lower()

                    # Handle the special case where object_name doesn't match the key
                    # Map known variations
                    model_key = model_name
                    if model_key == 'specialuserexperience':
                        model_key = 'special_user_experience'

                    if model_key in custom_order:
                        config = custom_order[model_key]
                        model['_order'] = config['order']
                        group_name = config['group']

                        # Add icon to model name only if it doesn't already have one
                        icon = config['icon']
                        if not model['name'].startswith(icon):
                            # Remove any existing numbering (e.g., "2. Journey Configurations" -> "Journey Configurations")
                            clean_name = model['name']
                            if '. ' in clean_name and clean_name.split('. ')[0].isdigit():
                                clean_name = '. '.join(clean_name.split('. ')[1:])

                            # Add icon only (no number prefix for cleaner look)
                            model['name'] = f"{icon} {clean_name}"

                        # Add to appropriate group
                        if group_name not in groups:
                            groups[group_name] = []
                        groups[group_name].append(model)

                # Create separate "app" entry for each group
                # Order determines sidebar display order - most used groups first
                group_order = [
                    ('👥 Users & Profiles', 'Users & Profiles'),
                    ('🎉 Events & Meetups', 'Events & Meetups'),
                    ('🗳️ Activity Voting', 'Activity Voting'),
                    ('💕 Connections', 'Connections'),
                    ('✨ Special Journey', 'Special Journey'),
                    ('🎄 Advent Calendar', 'Advent Calendar'),
                    ('🔔 Notifications', 'Notifications'),
                ]

                for display_name, group_key in group_order:
                    if group_key in groups:
                        # Sort models within each group
                        groups[group_key].sort(key=lambda x: x.get('_order', 999))

                        # Create a fake "app" for this group
                        new_app_list.append({
                            'name': display_name,
                            'app_label': f'crush_lu_{group_key.lower().replace(" ", "_").replace("&", "and")}',
                            'app_url': '#',
                            'has_module_perms': True,
                            'models': groups[group_key],
                        })
            else:
                # Keep other apps as-is
                new_app_list.append(app)

        return new_app_list


# Use custom admin site
crush_admin_site = CrushLuAdminSite(name='crush_admin')
