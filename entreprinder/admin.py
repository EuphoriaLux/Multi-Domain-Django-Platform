from django.contrib import admin
from django.utils.html import format_html
from .models import EntrepreneurProfile, Skill, Industry

@admin.register(Industry)
class IndustryAdmin(admin.ModelAdmin):
    list_display = ('name',)
    search_fields = ('name',)


@admin.register(EntrepreneurProfile)
class EntrepreneurProfileAdmin(admin.ModelAdmin):
    list_display = (
        'user', 
        'company', 
        'industry', 
        'location', 
        'is_mentor', 
        'is_investor', 
        'photo_preview',  # Renamed from 'profile_picture_preview'
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


@admin.register(Skill)
class SkillAdmin(admin.ModelAdmin):
    list_display = ('name',)
    search_fields = ('name',)


# Customize the admin site header and title
admin.site.site_header = "Entreprinder Administration"
admin.site.site_title = "Entreprinder Admin Portal"
admin.site.index_title = "Welcome to Entreprinder Admin"
