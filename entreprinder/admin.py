from django.contrib import admin
from django.utils.html import format_html
from .models import EntrepreneurProfile, Skill, Industry, Match, Like, Dislike

# Import FinOps models and admin classes
from .finops.models import CostExport, CostRecord, CostAggregation
from .finops.admin import CostExportAdmin, CostRecordAdmin, CostAggregationAdmin

# Import Vibe models and admin classes
from .vibe.models import PixelCanvas, Pixel, PixelHistory, UserPixelCooldown, UserPixelStats
from .vibe.admin import (
    PixelCanvasAdmin, PixelAdmin, PixelHistoryAdmin,
    UserPixelCooldownAdmin, UserPixelStatsAdmin
)


# ============================================================================
# CUSTOM ADMIN SITE - Entreprinder Administration
# ============================================================================

class EntreprinderAdminSite(admin.AdminSite):
    site_header = 'Entreprinder Administration'
    site_title = 'Entreprinder Admin'
    index_title = 'Entrepreneur Network Management'

    def get_app_list(self, request, app_label=None):
        """
        Override to customize the admin index page grouping.
        Groups models into logical categories for better organization.
        """
        app_list = super().get_app_list(request, app_label)

        # Custom ordering and grouping for Entreprinder models
        custom_order = {
            # 1. Profiles
            'entrepreneurprofile': {'order': 1, 'icon': '👤', 'group': 'Profiles'},

            # 2. Matching
            'match': {'order': 10, 'icon': '🤝', 'group': 'Matching'},
            'like': {'order': 11, 'icon': '💚', 'group': 'Matching'},
            'dislike': {'order': 12, 'icon': '❌', 'group': 'Matching'},

            # 3. Categories
            'industry': {'order': 20, 'icon': '🏭', 'group': 'Categories'},
            'skill': {'order': 21, 'icon': '⚡', 'group': 'Categories'},

            # 4. FinOps (if included)
            'costexport': {'order': 30, 'icon': '📊', 'group': 'FinOps'},
            'costrecord': {'order': 31, 'icon': '💰', 'group': 'FinOps'},
            'costaggregation': {'order': 32, 'icon': '📈', 'group': 'FinOps'},

            # 5. Vibe Coding (if included)
            'pixelcanvas': {'order': 40, 'icon': '🎨', 'group': 'Vibe Coding'},
            'pixel': {'order': 41, 'icon': '🔲', 'group': 'Vibe Coding'},
            'pixelhistory': {'order': 42, 'icon': '📜', 'group': 'Vibe Coding'},
            'userpixelcooldown': {'order': 43, 'icon': '⏱️', 'group': 'Vibe Coding'},
            'userpixelstats': {'order': 44, 'icon': '📊', 'group': 'Vibe Coding'},
        }

        # Create grouped app list
        new_app_list = []

        for app in app_list:
            if app['app_label'] == 'entreprinder':
                # Group models by category
                groups = {}

                for model in app['models']:
                    model_name = model['object_name'].lower()

                    if model_name in custom_order:
                        config = custom_order[model_name]
                        model['_order'] = config['order']
                        group_name = config['group']

                        # Add icon to model name
                        icon = config['icon']
                        if not model['name'].startswith(icon):
                            model['name'] = f"{icon} {model['name']}"

                        if group_name not in groups:
                            groups[group_name] = []
                        groups[group_name].append(model)
                    else:
                        # Models not in custom order go to "Other"
                        if 'Other' not in groups:
                            groups['Other'] = []
                        groups['Other'].append(model)

                # Sort models within each group
                for group_name in groups:
                    groups[group_name].sort(key=lambda x: x.get('_order', 999))

                # Create new apps for each group
                group_order = ['Profiles', 'Matching', 'Categories', 'FinOps', 'Vibe Coding', 'Other']
                group_icons = {
                    'Profiles': '👤',
                    'Matching': '💼',
                    'Categories': '🏷️',
                    'FinOps': '💰',
                    'Vibe Coding': '🎮',
                    'Other': '📋',
                }

                for group_key in group_order:
                    if group_key in groups and groups[group_key]:
                        new_app_list.append({
                            'name': f"{group_icons.get(group_key, '')} {group_key}",
                            'app_label': f'entreprinder_{group_key.lower().replace(" ", "_")}',
                            'app_url': app['app_url'],
                            'has_module_perms': app['has_module_perms'],
                            'models': groups[group_key],
                        })
            else:
                new_app_list.append(app)

        return new_app_list


# Instantiate the custom admin site
entreprinder_admin_site = EntreprinderAdminSite(name='entreprinder_admin')


# ============================================================================
# MODEL ADMIN CLASSES
# ============================================================================

class IndustryAdmin(admin.ModelAdmin):
    list_display = ('name',)
    search_fields = ('name',)


class EntrepreneurProfileAdmin(admin.ModelAdmin):
    list_display = (
        'user',
        'company',
        'industry',
        'location',
        'is_mentor',
        'is_investor',
        'photo_preview',
    )
    list_filter = ('industry', 'location', 'is_mentor', 'is_investor')
    search_fields = ('user__username', 'user__email', 'company', 'industry__name', 'location')
    autocomplete_fields = ['skills', 'industry']
    change_form_template = 'entreprinder/admin/entrepreneurprofile_change_form.html'

    def photo_preview(self, obj):
        """
        Displays a 50x50 circle preview of the LinkedIn photo URL
        (or a fallback message if none).
        """
        if obj.linkedin_photo_url:
            return format_html(
                '<img src="{}" width="50" height="50" style="border-radius: 50%;" />',
                obj.linkedin_photo_url
            )
        return "No LinkedIn Photo"

    photo_preview.short_description = 'LinkedIn Photo'


class SkillAdmin(admin.ModelAdmin):
    list_display = ('name',)
    search_fields = ('name',)


# =============================================================================
# Matching Admin (merged from matching app)
# =============================================================================

class MatchAdmin(admin.ModelAdmin):
    list_display = ('entrepreneur1', 'entrepreneur2', 'created_at')
    list_filter = ('created_at',)
    search_fields = ('entrepreneur1__user__username', 'entrepreneur2__user__username')
    date_hierarchy = 'created_at'


class LikeAdmin(admin.ModelAdmin):
    list_display = ('liker', 'liked', 'created_at')
    list_filter = ('created_at',)
    search_fields = ('liker__user__username', 'liked__user__username')
    date_hierarchy = 'created_at'


class DislikeAdmin(admin.ModelAdmin):
    list_display = ('disliker', 'disliked', 'created_at')
    list_filter = ('created_at',)
    search_fields = ('disliker__user__username', 'disliked__user__username')
    date_hierarchy = 'created_at'


class MatchInline(admin.TabularInline):
    model = Match
    fk_name = 'entrepreneur1'
    extra = 1


# Customize the default admin site header and title
admin.site.site_header = "Entreprinder Administration"
admin.site.site_title = "Entreprinder Admin Portal"
admin.site.index_title = "Welcome to Entreprinder Admin"


# ============================================================================
# REGISTER MODELS TO CUSTOM POWERUP ADMIN SITE
# ============================================================================

# Profiles
entreprinder_admin_site.register(EntrepreneurProfile, EntrepreneurProfileAdmin)

# Matching
entreprinder_admin_site.register(Match, MatchAdmin)
entreprinder_admin_site.register(Like, LikeAdmin)
entreprinder_admin_site.register(Dislike, DislikeAdmin)

# Categories
entreprinder_admin_site.register(Industry, IndustryAdmin)
entreprinder_admin_site.register(Skill, SkillAdmin)

# FinOps
entreprinder_admin_site.register(CostExport, CostExportAdmin)
entreprinder_admin_site.register(CostRecord, CostRecordAdmin)
entreprinder_admin_site.register(CostAggregation, CostAggregationAdmin)

# Vibe Coding
entreprinder_admin_site.register(PixelCanvas, PixelCanvasAdmin)
entreprinder_admin_site.register(Pixel, PixelAdmin)
entreprinder_admin_site.register(PixelHistory, PixelHistoryAdmin)
entreprinder_admin_site.register(UserPixelCooldown, UserPixelCooldownAdmin)
entreprinder_admin_site.register(UserPixelStats, UserPixelStatsAdmin)
