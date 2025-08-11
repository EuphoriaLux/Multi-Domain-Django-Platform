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
    verbose_name_plural = 'User Profile'

class UserAdmin(BaseUserAdmin):
    inlines = (VdlUserProfileInline,)
    list_display = ('username', 'email', 'first_name', 'last_name', 'is_staff')

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
