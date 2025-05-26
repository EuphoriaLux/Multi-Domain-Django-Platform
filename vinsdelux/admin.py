from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User
from .models import VdlUserProfile, VdlAddress, VdlCategory, VdlProducer, VdlProduct, VdlProductImage, VdlOrder, VdlOrderItem, VdlBlogPostCategory, VdlBlogPost, HomepageContent # Removed VdlAdoptionPackage, VdlUserAdoption

# Inline for VdlUserProfile in User admin
class VdlUserProfileInline(admin.StackedInline):
    model = VdlUserProfile
    can_delete = False
    verbose_name_plural = 'User Profile'

# Customize User admin
class UserAdmin(BaseUserAdmin):
    inlines = (VdlUserProfileInline,)
    list_display = ('username', 'email', 'first_name', 'last_name', 'is_staff', 'get_phone_number')
    list_select_related = ('vdluserprofile',)

    def get_phone_number(self, instance):
        return instance.vdluserprofile.phone_number
    get_phone_number.short_description = 'Phone Number'

# Re-register User model
admin.site.unregister(User)
admin.site.register(User, UserAdmin)


@admin.register(VdlAddress)
class VdlAddressAdmin(admin.ModelAdmin):
    list_display = ('user', 'address_line_1', 'city', 'country', 'address_type', 'is_default_shipping', 'is_default_billing')
    list_filter = ('country', 'address_type', 'is_default_shipping', 'is_default_billing')
    search_fields = ('user__username', 'address_line_1', 'city', 'postal_code')
    raw_id_fields = ('user',)

@admin.register(VdlCategory)
class VdlCategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'parent', 'is_active')
    list_filter = ('is_active', 'parent')
    search_fields = ('name', 'slug')
    prepopulated_fields = {'slug': ('name',)}
    list_editable = ('is_active',)
    raw_id_fields = ('parent',)

class VdlProductInline(admin.TabularInline):
    model = VdlProduct
    extra = 0 # Changed to 0 to not show empty forms by default
    prepopulated_fields = {'slug': ('name',)}
    raw_id_fields = ('category', 'producer')

class VdlProductImageInline(admin.TabularInline):
    model = VdlProductImage
    extra = 0
    list_display = ('image', 'alt_text', 'is_feature')

# Removed VdlAdoptionPackageInline

@admin.register(VdlProducer)
class VdlProducerAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'region', 'is_featured_on_homepage', 'website')
    list_filter = ('is_featured_on_homepage', 'region')
    search_fields = ('name', 'slug', 'region', 'website')
    prepopulated_fields = {'slug': ('name',)}
    list_editable = ('is_featured_on_homepage',)
    inlines = [VdlProductInline] # Removed VdlAdoptionPackageInline


@admin.register(VdlProduct)
class VdlProductAdmin(admin.ModelAdmin):
    list_display = ('name', 'product_type', 'price', 'adoption_price', 'stock_quantity', 'is_available', 'is_featured', 'category', 'producer', 'created_at', 'updated_at') # Added product_type, adoption_price
    list_filter = ('product_type', 'category', 'producer', 'is_available', 'is_featured', 'created_at') # Added product_type
    search_fields = ('name', 'slug', 'short_description', 'full_description', 'sku', 'visit_details', 'personalization_options', 'experience_description', 'welcome_kit_description') # Added adoption fields
    prepopulated_fields = {'slug': ('name',)}
    list_editable = ('is_featured', 'price', 'stock_quantity', 'is_available') # Kept price and stock_quantity editable for coffrets
    raw_id_fields = ('category', 'producer')
    inlines = [VdlProductImageInline]
    readonly_fields = ('created_at', 'updated_at')

    def get_fieldsets(self, request, obj=None):
        fieldsets = (
            (None, {
                'fields': ('name', 'slug', 'product_type', 'sku', 'category', 'producer', 'is_available', 'is_featured')
            }),
            ('Descriptions', {
                'fields': ('short_description', 'full_description'),
                'classes': ('collapse',)
            }),
            ('Images', {
                'fields': ('main_image',),
            }),
            ('Timestamps', {
                'fields': ('created_at', 'updated_at'),
                'classes': ('collapse',)
            }),
        )

        if obj and obj.product_type == 'coffret':
            fieldsets += (
                ('Coffret Details', {
                    'fields': ('price', 'stock_quantity'),
                }),
            )
        elif obj and obj.product_type == 'adoption':
             fieldsets += (
                ('Adoption Plan Details', {
                    'fields': ('adoption_price', 'avant_premiere_price', 'duration_months', 'coffrets_per_year', 'bottles_per_coffret', 'includes_visit', 'visit_details', 'includes_medallion', 'includes_club_membership', 'welcome_kit_description', 'experience_description'),
                }),
            )

        return fieldsets


@admin.register(VdlProductImage)
class VdlProductImageAdmin(admin.ModelAdmin):
    list_display = ('product', 'image', 'alt_text', 'is_feature')
    list_filter = ('is_feature',)
    search_fields = ('product__name', 'alt_text')
    raw_id_fields = ('product',)
    list_editable = ('is_feature',)

# Removed VdlAdoptionPackageAdmin and VdlUserAdoptionAdmin

class VdlOrderItemInline(admin.TabularInline):
    model = VdlOrderItem
    extra = 0
    readonly_fields = ('product', 'price_at_purchase', 'quantity', 'personalization_details') # Make order items read-only in order admin
    raw_id_fields = ('product',) # Removed adoption_package

@admin.register(VdlOrder)
class VdlOrderAdmin(admin.ModelAdmin):
    list_display = ('order_number', 'user', 'first_name', 'last_name', 'email', 'created_at', 'total_paid', 'status', 'is_gift')
    list_filter = ('status', 'created_at', 'is_gift')
    search_fields = ('order_number', 'first_name', 'last_name', 'email', 'user__username', 'shipping_address__address_line_1', 'billing_address__address_line_1')
    readonly_fields = ('order_number', 'created_at', 'updated_at', 'total_paid', 'payment_intent_id')
    raw_id_fields = ('user', 'shipping_address', 'billing_address')
    inlines = [VdlOrderItemInline]
    fieldsets = (
        (None, {
            'fields': ('order_number', 'user', 'status', 'total_paid', 'payment_intent_id')
        }),
        ('Contact Information', {
            'fields': ('first_name', 'last_name', 'email', 'phone')
        }),
        ('Addresses', {
            'fields': ('shipping_address', 'billing_address')
        }),
        ('Gift Information', {
            'fields': ('is_gift', 'gift_message'),
            'classes': ('collapse',)
        }),
        ('Notes', {
            'fields': ('notes',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

@admin.register(VdlOrderItem)
class VdlOrderItemAdmin(admin.ModelAdmin):
    list_display = ('order', 'product', 'price_at_purchase', 'quantity') # Removed adoption_package
    search_fields = ('order__order_number', 'product__name') # Removed adoption_package
    raw_id_fields = ('order', 'product') # Removed adoption_package
    readonly_fields = ('price_at_purchase',) # Price at purchase should not be editable

@admin.register(VdlBlogPostCategory)
class VdlBlogPostCategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug')
    search_fields = ('name', 'slug')
    prepopulated_fields = {'slug': ('name',)}

@admin.register(VdlBlogPost)
class VdlBlogPostAdmin(admin.ModelAdmin):
    list_display = ('title', 'slug', 'author', 'category', 'status', 'published_date', 'created_at', 'updated_at')
    list_filter = ('status', 'category', 'author', 'published_date')
    search_fields = ('title', 'slug', 'content', 'excerpt', 'meta_description', 'meta_keywords')
    prepopulated_fields = {'slug': ('title',)}
    raw_id_fields = ('author', 'category')
    readonly_fields = ('created_at', 'updated_at')
    fieldsets = (
        (None, {
            'fields': ('title', 'slug', 'author', 'category', 'status', 'published_date')
        }),
        ('Content', {
            'fields': ('content', 'excerpt', 'featured_image')
        }),
        ('SEO', {
            'fields': ('meta_description', 'meta_keywords'),
            'classes': ('collapse',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

@admin.register(HomepageContent)
class HomepageContentAdmin(admin.ModelAdmin):
    list_display = ('hero_title', 'hero_subtitle', 'hero_background_image')
    fields = ('hero_title', 'hero_subtitle', 'hero_background_image') # Using fields instead of fieldsets
    # fieldsets = (
    #     (None, {
    #         'fields': ('hero_title', 'hero_subtitle', 'hero_background_image')
    #     }),
    # )
    inlines = [] # Explicitly define no inlines
