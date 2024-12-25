from django.contrib import admin
from django.utils.html import format_html
from .models import EntrepreneurProfile, Skill, Industry
from django.db import models
from .widgets import AdminImageWidget
from .forms import EntrepreneurProfileAdminForm

@admin.register(Industry)
class IndustryAdmin(admin.ModelAdmin):
    list_display = ('name',)
    search_fields = ('name',)

@admin.register(EntrepreneurProfile)
class EntrepreneurProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'company', 'industry', 'location', 'is_mentor', 'is_investor', 'profile_picture_preview')
    list_filter = ('industry', 'location', 'is_mentor', 'is_investor')
    search_fields = ('user__username', 'user__email', 'company', 'industry__name', 'location')
    autocomplete_fields = ['skills', 'industry']
    form = EntrepreneurProfileAdminForm
    
    # formfield_overrides = {
    #     models.ImageField: {'widget': AdminImageWidget},
    # }

    def profile_picture_preview(self, obj):
        if obj.profile_picture:
            return format_html('<img src="{}" width="50" height="50" style="border-radius: 50%;" />', obj.profile_picture.url)
        return "No picture"
    profile_picture_preview.short_description = 'Profile Picture'

@admin.register(Skill)
class SkillAdmin(admin.ModelAdmin):
    list_display = ('name',)
    search_fields = ('name',)

# Customize the admin site header and title
admin.site.site_header = "Entreprinder Administration"
admin.site.site_title = "Entreprinder Admin Portal"
admin.site.index_title = "Welcome to Entreprinder Admin"
