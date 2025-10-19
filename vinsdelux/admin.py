from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User
from django.contrib.contenttypes.admin import GenericTabularInline
from django.utils.html import format_html
from django.urls import reverse

from .models import (
    VdlUserProfile, VdlAddress, VdlCategory, VdlProducer,
    VdlCoffret, VdlAdoptionPlan, VdlProductImage,
    VdlOrder, VdlOrderItem, HomepageContent,
    VdlBlogPost, VdlBlogPostCategory, VdlAdoptionPlanImage,
    VdlPlot, VdlPlotReservation, PlotStatus
)

# --- User Admin ---
class VdlUserProfileInline(admin.StackedInline):
    model = VdlUserProfile
    can_delete = False
    verbose_name_plural = 'VinsDelux Profile'
    fields = ('phone_number', 'default_shipping_address', 'default_billing_address')

# Dynamic inlines for other app profiles
class EntrepreneurProfileInline(admin.StackedInline):
    """Show EntrepreneurProfile if exists"""
    from entreprinder.models import EntrepreneurProfile
    model = EntrepreneurProfile
    can_delete = False
    verbose_name_plural = 'PowerUP / Entreprinder Profile'
    fields = ('bio', 'company', 'industry', 'location', 'linkedin_profile')
    readonly_fields = ('bio', 'company', 'industry', 'location', 'linkedin_profile')
    extra = 0

    def has_add_permission(self, request, obj=None):
        return False

class CrushProfileInline(admin.StackedInline):
    """Show CrushProfile if exists"""
    from crush_lu.models import CrushProfile
    model = CrushProfile
    can_delete = False
    verbose_name_plural = 'Crush.lu Dating Profile'
    fields = ('phone_number', 'gender', 'location', 'bio', 'is_approved', 'approved_at', 'completion_status')
    readonly_fields = ('is_approved', 'approved_at', 'completion_status')
    extra = 0

    def has_add_permission(self, request, obj=None):
        return False

class CrushCoachInline(admin.StackedInline):
    """Show CrushCoach if exists"""
    from crush_lu.models import CrushCoach
    model = CrushCoach
    can_delete = False
    verbose_name_plural = 'Crush.lu Coach Status'
    fields = ('bio', 'specializations', 'is_active', 'max_active_reviews')
    extra = 0

    def has_add_permission(self, request, obj=None):
        return False

class UserAdmin(BaseUserAdmin):
    inlines = (EntrepreneurProfileInline, CrushProfileInline, CrushCoachInline, VdlUserProfileInline)
    list_display = ('username', 'email', 'first_name', 'last_name', 'is_staff',
                    'has_entreprinder_profile', 'has_crush_profile', 'has_vinsdelux_profile', 'is_crush_coach',
                    'profile_count')
    list_filter = BaseUserAdmin.list_filter + ('is_staff', 'is_active', 'date_joined')

    # Add search by profile status
    search_fields = ('username', 'email', 'first_name', 'last_name')

    def profile_count(self, obj):
        """Count total number of profiles across all apps"""
        count = 0
        try:
            if obj.entrepreneurprofile:
                count += 1
        except:
            pass
        try:
            if obj.crushprofile:
                count += 1
        except:
            pass
        try:
            if obj.vdluserprofile:
                count += 1
        except:
            pass
        try:
            if obj.crushcoach:
                count += 1
        except:
            pass

        if count >= 3:
            return format_html('<strong style="color: green;">{} profiles</strong>', count)
        elif count >= 2:
            return format_html('<strong style="color: orange;">{} profiles</strong>', count)
        elif count == 1:
            return format_html('{} profile', count)
        else:
            return format_html('<span style="color: red;">No profiles</span>')
    profile_count.short_description = '📊 Total Profiles'

    def has_entreprinder_profile(self, obj):
        """Check if user has Entreprinder/PowerUP profile"""
        try:
            return obj.entrepreneurprofile is not None
        except:
            return False
    has_entreprinder_profile.boolean = True
    has_entreprinder_profile.short_description = '👔 PowerUP'

    def has_crush_profile(self, obj):
        """Check if user has Crush.lu dating profile"""
        try:
            return obj.crushprofile is not None
        except:
            return False
    has_crush_profile.boolean = True
    has_crush_profile.short_description = '💕 Crush.lu'

    def has_vinsdelux_profile(self, obj):
        """Check if user has VinsDelux profile"""
        try:
            return obj.vdluserprofile is not None
        except:
            return False
    has_vinsdelux_profile.boolean = True
    has_vinsdelux_profile.short_description = '🍷 VinsDelux'

    def is_crush_coach(self, obj):
        """Check if user is an active Crush.lu coach"""
        try:
            return obj.crushcoach.is_active if obj.crushcoach else False
        except:
            return False
    is_crush_coach.boolean = True
    is_crush_coach.short_description = '🎯 Coach'

    # Add custom readonly fields to detail page
    readonly_fields = ('get_profile_summary',)

    def get_profile_summary(self, obj):
        """Display a summary of all profiles this user has"""
        from django.utils.safestring import mark_safe

        summary = '<div style="background: #f8f9fa; padding: 15px; border-radius: 8px;">'
        summary += '<h3 style="margin-top: 0;">User Profile Summary</h3>'
        summary += '<table style="width: 100%; border-collapse: collapse;">'

        # Check PowerUP profile
        try:
            if obj.entrepreneurprofile:
                summary += '<tr style="background: #e8f5e9;"><td style="padding: 8px; border: 1px solid #ddd;"><strong>👔 PowerUP/Entreprinder</strong></td>'
                summary += f'<td style="padding: 8px; border: 1px solid #ddd;">✅ Active<br><small>Company: {obj.entrepreneurprofile.company or "Not set"}</small></td></tr>'
            else:
                summary += '<tr><td style="padding: 8px; border: 1px solid #ddd;"><strong>👔 PowerUP/Entreprinder</strong></td>'
                summary += '<td style="padding: 8px; border: 1px solid #ddd;">❌ No profile</td></tr>'
        except:
            summary += '<tr><td style="padding: 8px; border: 1px solid #ddd;"><strong>👔 PowerUP/Entreprinder</strong></td>'
            summary += '<td style="padding: 8px; border: 1px solid #ddd;">❌ No profile</td></tr>'

        # Check Crush.lu profile
        try:
            if obj.crushprofile:
                approval_status = "Approved ✓" if obj.crushprofile.is_approved else "Pending review"
                summary += '<tr style="background: #fce4ec;"><td style="padding: 8px; border: 1px solid #ddd;"><strong>💕 Crush.lu</strong></td>'
                summary += f'<td style="padding: 8px; border: 1px solid #ddd;">✅ Active<br><small>{approval_status} | Status: {obj.crushprofile.completion_status}</small></td></tr>'
            else:
                summary += '<tr><td style="padding: 8px; border: 1px solid #ddd;"><strong>💕 Crush.lu</strong></td>'
                summary += '<td style="padding: 8px; border: 1px solid #ddd;">❌ No profile</td></tr>'
        except:
            summary += '<tr><td style="padding: 8px; border: 1px solid #ddd;"><strong>💕 Crush.lu</strong></td>'
            summary += '<td style="padding: 8px; border: 1px solid #ddd;">❌ No profile</td></tr>'

        # Check Crush Coach status
        try:
            if obj.crushcoach:
                coach_status = "Active 🎯" if obj.crushcoach.is_active else "Inactive"
                summary += '<tr style="background: #fff3e0;"><td style="padding: 8px; border: 1px solid #ddd;"><strong>🎯 Crush Coach</strong></td>'
                summary += f'<td style="padding: 8px; border: 1px solid #ddd;">{coach_status}<br><small>Reviews: {obj.crushcoach.get_active_reviews_count()}/{obj.crushcoach.max_active_reviews}</small></td></tr>'
            else:
                summary += '<tr><td style="padding: 8px; border: 1px solid #ddd;"><strong>🎯 Crush Coach</strong></td>'
                summary += '<td style="padding: 8px; border: 1px solid #ddd;">❌ Not a coach</td></tr>'
        except:
            summary += '<tr><td style="padding: 8px; border: 1px solid #ddd;"><strong>🎯 Crush Coach</strong></td>'
            summary += '<td style="padding: 8px; border: 1px solid #ddd;">❌ Not a coach</td></tr>'

        # Check VinsDelux profile
        try:
            if obj.vdluserprofile:
                summary += '<tr style="background: #f3e5f5;"><td style="padding: 8px; border: 1px solid #ddd;"><strong>🍷 VinsDelux</strong></td>'
                summary += f'<td style="padding: 8px; border: 1px solid #ddd;">✅ Active<br><small>Phone: {obj.vdluserprofile.phone_number or "Not set"}</small></td></tr>'
            else:
                summary += '<tr><td style="padding: 8px; border: 1px solid #ddd;"><strong>🍷 VinsDelux</strong></td>'
                summary += '<td style="padding: 8px; border: 1px solid #ddd;">❌ No profile</td></tr>'
        except:
            summary += '<tr><td style="padding: 8px; border: 1px solid #ddd;"><strong>🍷 VinsDelux</strong></td>'
            summary += '<td style="padding: 8px; border: 1px solid #ddd;">❌ No profile</td></tr>'

        summary += '</table></div>'
        return mark_safe(summary)
    get_profile_summary.short_description = 'Cross-Platform Profile Status'

    # Add fieldsets to organize the detail view
    fieldsets = BaseUserAdmin.fieldsets + (
        ('Cross-Platform Profile Overview', {
            'fields': ('get_profile_summary',),
            'description': 'View all profiles this user has created across PowerUP, Crush.lu, VinsDelux, and Coach status.'
        }),
    )

admin.site.unregister(User)
admin.site.register(User, UserAdmin)

# --- Address Admin ---
@admin.register(VdlAddress)
class VdlAddressAdmin(admin.ModelAdmin):
    list_display = ('user', 'address_line_1', 'city', 'country', 'address_type')
    raw_id_fields = ('user',)

# --- Product Infrastructure Admin ---
@admin.register(VdlCategory)
class VdlCategoryAdmin(admin.ModelAdmin):
    prepopulated_fields = {'slug': ('name',)}

@admin.register(VdlProducer)
class VdlProducerAdmin(admin.ModelAdmin):
    list_display = ('name', 'region', 'website', 'is_featured_on_homepage')
    list_filter = ('region', 'is_featured_on_homepage')
    search_fields = ('name', 'description')
    prepopulated_fields = {'slug': ('name',)}
    inlines = []

class VdlCoffretInline(admin.TabularInline):
    model = VdlCoffret
    extra = 0
    fields = ('name', 'category', 'price', 'stock_quantity', 'is_available')
    readonly_fields = ('name', 'category', 'price', 'stock_quantity', 'is_available')

class VdlAdoptionPlanInline(admin.TabularInline):
    model = VdlAdoptionPlan
    extra = 0
    fields = ('name', 'associated_coffret', 'price', 'duration_months', 'coffrets_per_year', 'is_available')
    readonly_fields = ('name', 'associated_coffret', 'price', 'duration_months', 'coffrets_per_year', 'is_available')

VdlProducerAdmin.inlines = [VdlCoffretInline, VdlAdoptionPlanInline]

# --- NEW, IMPROVED Product Administration ---

class ProductImageInline(GenericTabularInline):
    model = VdlProductImage
    extra = 1

class VdlAdoptionPlanInline(admin.TabularInline):
    model = VdlAdoptionPlan
    extra = 0
    fields = ('name', 'price', 'duration_months', 'is_available', 'edit_details_link')
    readonly_fields = ('edit_details_link',)
    verbose_name_plural = "Associated Adoption Plans"

    def edit_details_link(self, instance):
        if instance.pk:
            url = reverse('admin:vinsdelux_vdladoptionplan_change', args=[instance.pk])
            return format_html('<a href="{}" target="_blank">Configure Full Details →</a>', url)
        return "Save and continue editing to enable."
    edit_details_link.short_description = 'Actions'

@admin.register(VdlCoffret)
class VdlCoffretAdmin(admin.ModelAdmin):
    list_display = ('name', 'producer', 'price', 'stock_quantity', 'is_available', 'main_image_thumbnail')
    list_filter = ('is_available', 'producer', 'category') # Add price range filter later if needed
    search_fields = ('name', 'sku')
    prepopulated_fields = {'slug': ('name',)}
    inlines = [ProductImageInline, VdlAdoptionPlanInline]

    def main_image_thumbnail(self, obj):
        if obj.main_image:
            return format_html('<img src="{}" style="max-height: 50px; max-width: 50px;" />', obj.main_image.url)
        return None
    main_image_thumbnail.short_description = 'Main Image'

# Inline for Adoption Plan Images
class VdlAdoptionPlanImageInline(admin.TabularInline):
    model = VdlAdoptionPlanImage
    extra = 1
    fields = ('image', 'order', 'is_primary', 'caption')
    ordering = ['order']

# Restore the full admin for Adoption Plan to handle detailed editing
@admin.register(VdlAdoptionPlan)
class VdlAdoptionPlanAdmin(admin.ModelAdmin):
    list_display = ('name', 'associated_coffret', 'price', 'duration_months', 'is_available')
    list_filter = ('is_available', 'duration_months', 'associated_coffret__producer')
    search_fields = ('name', 'associated_coffret__name')
    readonly_fields = ('associated_coffret', 'producer')
    inlines = [VdlAdoptionPlanImageInline, ProductImageInline] # New image inline + existing product images

    fieldsets = (
        (None, {
            'fields': ('name', 'slug', 'associated_coffret', 'producer', 'category', 'sku')
        }),
        ('Plan Details', {
            'fields': ('price', 'duration_months', 'coffrets_per_year', 'is_available', )
        }),
        ('Descriptions', {
            'fields': ('short_description', 'full_description', 'welcome_kit_description'),
            'classes': ('collapse',)
        }),
        ('Included Features', {
            'fields': ('includes_visit', 'visit_details', 'includes_medallion', 'includes_club_membership'),
            'classes': ('collapse',)
        }),
        ('Gift Option', {
            'fields': ('avant_premiere_price',),
            'classes': ('collapse',)
        }),
    )

# Register VdlAdoptionPlanImage for direct management
@admin.register(VdlAdoptionPlanImage)
class VdlAdoptionPlanImageAdmin(admin.ModelAdmin):
    list_display = ('adoption_plan', 'order', 'is_primary', 'caption', 'created_at')
    list_filter = ('is_primary', 'adoption_plan')
    search_fields = ('adoption_plan__name', 'caption')
    ordering = ['adoption_plan', 'order']

# --- Order Admin ---
class VdlOrderItemInline(admin.TabularInline):
    model = VdlOrderItem
    extra = 0
    readonly_fields = ('product', 'price_at_purchase', 'quantity')
    
@admin.register(VdlOrder)
class VdlOrderAdmin(admin.ModelAdmin):
    list_display = ('order_number', 'user', 'created_at', 'total_paid', 'status')
    inlines = [VdlOrderItemInline]

# --- Blog & CMS Admin ---
@admin.register(VdlBlogPostCategory)
class VdlBlogPostCategoryAdmin(admin.ModelAdmin):
    prepopulated_fields = {'slug': ('name',)}

@admin.register(VdlBlogPost)
class VdlBlogPostAdmin(admin.ModelAdmin):
    prepopulated_fields = {'slug': ('title',)}

@admin.register(HomepageContent)
class HomepageContentAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return self.model.objects.count() == 0


# --- Plot Selection Admin ---

class VdlPlotReservationInline(admin.TabularInline):
    model = VdlPlotReservation
    extra = 0
    fields = ('user', 'reserved_at', 'expires_at', 'is_confirmed', 'is_expired_status')
    readonly_fields = ('reserved_at', 'is_expired_status')
    
    def is_expired_status(self, instance):
        if instance.pk:
            return "Expired" if instance.is_expired else "Active"
        return "-"
    is_expired_status.short_description = 'Status'


@admin.register(VdlPlot)
class VdlPlotAdmin(admin.ModelAdmin):
    list_display = ('name', 'plot_identifier', 'producer', 'status', 'base_price', 'is_premium', 'is_available')
    list_filter = ('status', 'is_premium', 'producer', 'grape_varieties')
    search_fields = ('name', 'plot_identifier', 'producer__name', 'wine_profile')
    readonly_fields = ('created_at', 'updated_at', 'latitude', 'longitude', 'display_coordinates', 'primary_grape_variety')
    inlines = [VdlPlotReservationInline]
    
    fieldsets = (
        (None, {
            'fields': ('name', 'plot_identifier', 'producer', 'status', 'is_premium')
        }),
        ('Geographic Information', {
            'fields': ('coordinates', 'latitude', 'longitude', 'display_coordinates', 'plot_size', 'elevation'),
            'classes': ('collapse',)
        }),
        ('Viticulture', {
            'fields': ('soil_type', 'sun_exposure', 'microclimate_notes', 'grape_varieties', 'primary_grape_variety', 'vine_age', 'harvest_year'),
            'classes': ('collapse',)
        }),
        ('Wine Characteristics', {
            'fields': ('wine_profile', 'expected_yield'),
            'classes': ('collapse',)
        }),
        ('Pricing & Availability', {
            'fields': ('base_price', 'adoption_plans'),
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    filter_horizontal = ('adoption_plans',)
    
    def display_coordinates(self, obj):
        """Display formatted coordinates"""
        return obj.get_display_coordinates()
    display_coordinates.short_description = 'Formatted Coordinates'
    
    def primary_grape_variety(self, obj):
        """Display primary grape variety"""
        return obj.get_primary_grape_variety() or "Not specified"
    primary_grape_variety.short_description = 'Primary Grape'


@admin.register(VdlPlotReservation)
class VdlPlotReservationAdmin(admin.ModelAdmin):
    list_display = ('plot', 'user', 'reserved_at', 'expires_at', 'is_confirmed', 'is_expired_status')
    list_filter = ('is_confirmed', 'reserved_at', 'plot__producer')
    search_fields = ('plot__name', 'plot__plot_identifier', 'user__username', 'user__email')
    readonly_fields = ('reserved_at', 'is_expired_status')
    raw_id_fields = ('plot', 'user', 'adoption_plan')
    
    fieldsets = (
        (None, {
            'fields': ('plot', 'user', 'adoption_plan')
        }),
        ('Reservation Details', {
            'fields': ('reserved_at', 'expires_at', 'is_confirmed', 'confirmation_date', 'is_expired_status')
        }),
        ('Additional Information', {
            'fields': ('notes', 'session_data'),
            'classes': ('collapse',)
        }),
    )
    
    def is_expired_status(self, obj):
        """Display if reservation is expired"""
        if obj.is_expired:
            return format_html('<span style="color: red;">Expired</span>')
        elif obj.is_confirmed:
            return format_html('<span style="color: green;">Confirmed</span>')
        else:
            return format_html('<span style="color: orange;">Pending</span>')
    is_expired_status.short_description = 'Status'
