from django.contrib import admin
from .models import VdlUserProfile, VdlAddress, VdlCategory, VdlProducer, VdlProduct, VdlProductImage, VdlAdoptionTier, VdlOrder, VdlOrderItem, VdlBlogPostCategory, VdlBlogPost

@admin.register(VdlUserProfile)
class VdlUserProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'phone_number')
    search_fields = ('user__username', 'phone_number')

@admin.register(VdlAddress)
class VdlAddressAdmin(admin.ModelAdmin):
    list_display = ('user', 'address_line_1', 'city', 'country', 'address_type')
    list_filter = ('country', 'address_type')
    search_fields = ('user__username', 'address_line_1', 'city')

@admin.register(VdlCategory)
class VdlCategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'is_active')
    list_filter = ('is_active',)
    search_fields = ('name', 'slug')
    prepopulated_fields = {'slug': ('name',)}

@admin.register(VdlProducer)
class VdlProducerAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'region')
    search_fields = ('name', 'slug', 'region')
    prepopulated_fields = {'slug': ('name',)}

@admin.register(VdlProduct)
class VdlProductAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'category', 'producer', 'price', 'stock_quantity', 'is_available', 'is_featured')
    list_filter = ('category', 'producer', 'is_available', 'is_featured')
    search_fields = ('name', 'slug', 'short_description', 'full_description')
    prepopulated_fields = {'slug': ('name',)}

@admin.register(VdlProductImage)
class VdlProductImageAdmin(admin.ModelAdmin):
    list_display = ('product', 'alt_text', 'is_feature')
    list_filter = ('is_feature',)
    search_fields = ('product__name', 'alt_text')

@admin.register(VdlAdoptionTier)
class VdlAdoptionTierAdmin(admin.ModelAdmin):
    list_display = ('product', 'vine_location_name', 'duration_months', 'bottles_included')
    search_fields = ('product__name', 'vine_location_name')

@admin.register(VdlOrder)
class VdlOrderAdmin(admin.ModelAdmin):
    list_display = ('order_number', 'user', 'first_name', 'last_name', 'email', 'created_at', 'total_paid', 'status')
    list_filter = ('status', 'created_at')
    search_fields = ('order_number', 'first_name', 'last_name', 'email', 'user__username')

@admin.register(VdlOrderItem)
class VdlOrderItemAdmin(admin.ModelAdmin):
    list_display = ('order', 'product', 'price_at_purchase', 'quantity')
    search_fields = ('order__order_number', 'product__name')

@admin.register(VdlBlogPostCategory)
class VdlBlogPostCategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug')
    search_fields = ('name', 'slug')
    prepopulated_fields = {'slug': ('name',)}

@admin.register(VdlBlogPost)
class VdlBlogPostAdmin(admin.ModelAdmin):
    list_display = ('title', 'slug', 'author', 'category', 'status', 'published_date')
    list_filter = ('status', 'category', 'author')
    search_fields = ('title', 'slug', 'content', 'excerpt')
    prepopulated_fields = {'slug': ('title',)}
