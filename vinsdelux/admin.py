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
    VdlBlogPost, VdlBlogPostCategory
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
    prepopulated_fields = {'slug': ('name',)}

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
    list_display = ('name', 'producer', 'price', 'stock_quantity', 'is_available')
    list_filter = ('is_available', 'producer', 'category')
    search_fields = ('name', 'sku')
    prepopulated_fields = {'slug': ('name',)}
    inlines = [ProductImageInline, VdlAdoptionPlanInline]

# Restore the full admin for Adoption Plan to handle detailed editing
@admin.register(VdlAdoptionPlan)
class VdlAdoptionPlanAdmin(admin.ModelAdmin):
    list_display = ('name', 'associated_coffret', 'price', 'duration_months', 'is_available')
    list_filter = ('is_available', 'duration_months', 'associated_coffret__producer')
    search_fields = ('name', 'associated_coffret__name')
    readonly_fields = ('associated_coffret', 'producer', 'created_at', 'updated_at')
    inlines = [ProductImageInline] # An adoption plan can have its own images too

    fieldsets = (
        (None, {
            'fields': ('name', 'slug', 'associated_coffret', 'producer', 'category', 'sku')
        }),
        ('Plan Details', {
            'fields': ('price', 'duration_months', 'coffrets_per_year', 'is_available', 'is_featured')
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
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

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
