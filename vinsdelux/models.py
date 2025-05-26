from django.db import models
import logging
import os # Added import for os module
logger = logging.getLogger(__name__)
from django.contrib.auth.models import User

class VdlUserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, primary_key=True)
    phone_number = models.CharField(max_length=20, blank=True, null=True)
    default_shipping_address = models.ForeignKey('VdlAddress', related_name='shipping_users', on_delete=models.SET_NULL, blank=True, null=True)
    default_billing_address = models.ForeignKey('VdlAddress', related_name='billing_users', on_delete=models.SET_NULL, blank=True, null=True)

    def __str__(self):
        return self.user.username

class VdlAddress(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='addresses')
    address_line_1 = models.CharField(max_length=255)
    address_line_2 = models.CharField(max_length=255, blank=True, null=True)
    city = models.CharField(max_length=100)
    state_province = models.CharField(max_length=100)
    postal_code = models.CharField(max_length=20)
    country = models.CharField(max_length=50)
    address_type = models.CharField(max_length=10, choices=[('billing', 'Billing'), ('shipping', 'Shipping')], default='shipping')
    is_default_shipping = models.BooleanField(default=False)
    is_default_billing = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.address_line_1}, {self.city}, {self.country}"

WINE_TYPE_CHOICES = [
    ('Red Wine', 'Red Wine'),
    ('White Wine', 'White Wine'),
    ('Rosé', 'Rosé'),
    ('Sparkling Wine', 'Sparkling Wine'),
    ('Dessert Wine', 'Dessert Wine'),
]

class VdlCategory(models.Model):
    name = models.CharField(max_length=100, choices=WINE_TYPE_CHOICES) # Use choices
    slug = models.SlugField(max_length=120, unique=True, help_text="URL-friendly version of the name")
    description = models.TextField(blank=True, null=True)
    image = models.ImageField(upload_to='categories/', blank=True, null=True)
    parent = models.ForeignKey('self', null=True, blank=True, related_name='children', on_delete=models.CASCADE)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name

class VdlProducer(models.Model):
    name = models.CharField(max_length=150, unique=True)
    slug = models.SlugField(max_length=170, unique=True)
    description = models.TextField(blank=True)
    logo = models.ImageField(upload_to='producers/logos/', blank=True, null=True) # Updated upload_to for clarity
    producer_photo = models.ImageField(upload_to='producers/photos/', blank=True, null=True, help_text="Photo of the producer")
    website = models.URLField(blank=True, null=True)
    region = models.CharField(max_length=100, blank=True)
    is_featured_on_homepage = models.BooleanField(default=False, help_text="Feature this producer on the homepage?")

    def __str__(self):
        return self.name

PRODUCT_TYPE_CHOICES = [
    ('coffret', 'Coffret'),
    ('adoption', 'Adoption Plan'),
]

class VdlProduct(models.Model):
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220, unique=True)
    product_type = models.CharField(max_length=20, choices=PRODUCT_TYPE_CHOICES, default='coffret') # Added product type field
    category = models.ForeignKey(VdlCategory, related_name='products', on_delete=models.SET_NULL, null=True, blank=True)
    producer = models.ForeignKey(VdlProducer, related_name='products', on_delete=models.SET_NULL, null=True, blank=True)
    short_description = models.TextField(help_text="Brief overview for product listings.")
    full_description = models.TextField(blank=True, null=True, help_text="Detailed description for product page.")
    
    # Fields for Coffret Products
    price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, help_text="Price per coffret")
    stock_quantity = models.PositiveIntegerField(default=0, null=True, blank=True, help_text="Stock for standalone coffrets")
    sku = models.CharField(max_length=50, unique=True, blank=True, null=True, help_text="Stock Keeping Unit")
    is_available = models.BooleanField(default=True, help_text="Is the product available for purchase?")
    is_featured = models.BooleanField(default=False, help_text="Feature on homepage or special sections?")
    main_image = models.ImageField(upload_to='products/main/')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Fields for Adoption Plan Products (moved from VdlAdoptionPackage)
    duration_months = models.IntegerField(null=True, blank=True, help_text="Duration of the adoption in months")
    coffrets_per_year = models.IntegerField(null=True, blank=True, help_text="Number of seasonal coffrets included per year")
    bottles_per_coffret = models.IntegerField(null=True, blank=True, help_text="Number of bottles included in each seasonal coffret (e.g., 1 personalized + 1 surprise)")
    includes_visit = models.BooleanField(default=False, help_text="Does this package include a vineyard visit?")
    visit_details = models.TextField(blank=True, null=True, help_text="Details about the vineyard visit")
    includes_medallion = models.BooleanField(default=False, help_text="Does this package include a personalized medallion?")
    includes_club_membership = models.BooleanField(default=False, help_text="Does this package include Club Cuvée Privée membership?")
    adoption_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, help_text="Price for the entire adoption package") # Renamed price to adoption_price for clarity
    avant_premiere_price = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True, help_text="Price for the Avant-Première gift option")
    welcome_kit_description = models.TextField(blank=True, null=True, help_text="Description of what's included in the welcome kit")

    def __str__(self):
        return self.name

class VdlProductImage(models.Model):
    product = models.ForeignKey(VdlProduct, related_name='images', on_delete=models.CASCADE)
    image = models.ImageField(upload_to='products/gallery/')
    alt_text = models.CharField(max_length=100, blank=True, help_text="Description for SEO and accessibility")
    is_feature = models.BooleanField(default=False, help_text="Is this the primary display image?")

    def __str__(self):
        return self.alt_text

# VdlAdoptionPackage and VdlUserAdoption models are removed

class VdlOrder(models.Model):
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='orders')
    order_number = models.CharField(max_length=32, unique=True, editable=False)
    first_name = models.CharField(max_length=50)
    last_name = models.CharField(max_length=50)
    email = models.EmailField()
    phone = models.CharField(max_length=20, blank=True)
    shipping_address = models.ForeignKey('VdlAddress', related_name='shipping_orders', on_delete=models.PROTECT, null=True, blank=True)
    billing_address = models.ForeignKey('VdlAddress', related_name='billing_orders', on_delete=models.PROTECT, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    total_paid = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    status = models.CharField(max_length=50, choices=[('pending', 'Pending'), ('processing', 'Processing'), ('shipped', 'Shipped'), ('delivered', 'Delivered'), ('cancelled', 'Cancelled')], default='pending')
    is_gift = models.BooleanField(default=False)
    gift_message = models.TextField(blank=True, null=True)
    payment_intent_id = models.CharField(max_length=100, blank=True, null=True)
    notes = models.TextField(blank=True, null=True, help_text="Customer notes or internal notes")

    def __str__(self):
        return self.order_number

class VdlOrderItem(models.Model):
    order = models.ForeignKey(VdlOrder, related_name='items', on_delete=models.CASCADE)
    product = models.ForeignKey(VdlProduct, related_name='order_items', on_delete=models.PROTECT) # Link directly to VdlProduct
    price_at_purchase = models.DecimalField(max_digits=10, decimal_places=2, help_text="Price of the item when the order was placed")
    quantity = models.PositiveIntegerField(default=1) # Quantity of coffrets or adoption packages
    personalization_details = models.TextField(blank=True, null=True)

    def __str__(self):
        return f"{self.quantity} x {self.product.name} in Order {self.order.order_number}"


class VdlBlogPostCategory(models.Model):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=120, unique=True)

    def __str__(self):
        return self.name

class VdlBlogPost(models.Model):
    title = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220, unique=True)
    author = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='blog_posts')
    category = models.ForeignKey(VdlBlogPostCategory, related_name='posts', on_delete=models.SET_NULL, null=True, blank=True)
    content = models.TextField()
    excerpt = models.TextField(blank=True, help_text="A short summary for list views.")
    featured_image = models.ImageField(upload_to='blog/', blank=True, null=True)
    status = models.CharField(max_length=10, choices=[('draft', 'Draft'), ('published', 'Published')], default='draft')
    published_date = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    meta_description = models.CharField(max_length=160, blank=True)
    meta_keywords = models.CharField(max_length=255, blank=True)

    def __str__(self):
        return self.title

class HomepageContent(models.Model):
    hero_title = models.CharField(max_length=200, help_text="Title for the homepage hero section")
    hero_subtitle = models.TextField(help_text="Subtitle for the homepage hero section")
    hero_background_image = models.ImageField(upload_to='', help_text="Background image for the homepage hero section") # Removed 'homepage/' from upload_to

    class Meta:
        verbose_name_plural = "Homepage Content"

    def save(self, *args, **kwargs):
        logger.debug(f"HomepageContent save method called for instance: {self.pk}")
        if self.hero_background_image:
            logger.debug(f"hero_background_image exists. Type: {type(self.hero_background_image)}")
            logger.debug(f"Original File name: {self.hero_background_image.name}")
            logger.debug(f"File size: {self.hero_background_image.size} bytes")
            try:
                # Attempt to read a small part of the file to check accessibility
                # Reset file pointer before reading if it's an UploadedFile
                if hasattr(self.hero_background_image, 'seek'):
                    self.hero_background_image.seek(0)
                
                # Read first 100 bytes
                first_bytes = self.hero_background_image.read(100)
                logger.debug(f"First 100 bytes of file: {first_bytes[:50]}...")
                
                # Reset file pointer for actual save operation
                if hasattr(self.hero_background_image, 'seek'):
                    self.hero_background_image.seek(0)

            except Exception as e:
                logger.error(f"Error reading hero_background_image in save method: {e}", exc_info=True)
        else:
            logger.debug("hero_background_image is None or not set.")

        super().save(*args, **kwargs)
        logger.debug("super().save() completed successfully.")

    def __str__(self):
        return "Homepage Content"
