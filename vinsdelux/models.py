from django.db import models
from django.contrib.auth.models import User
from django.contrib.contenttypes.fields import GenericForeignKey, GenericRelation
from django.contrib.contenttypes.models import ContentType
import logging
from django.utils.translation import gettext_lazy as _

logger = logging.getLogger(__name__)

# --- User Profile & Address Models ---

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

    def __str__(self):
        return f"{self.user.username}'s address: {self.address_line_1}"

    class Meta:
        verbose_name_plural = "Addresses"


# --- Core Product Infrastructure ---

class WineType(models.TextChoices):
    RED_WINE = 'Red Wine', 'Red Wine'
    WHITE_WINE = 'White Wine', 'White Wine'
    ROSE = 'Rosé', 'Rosé'
    SPARKLING = 'Sparkling Wine', 'Sparkling Wine'
    DESSERT = 'Dessert Wine', 'Dessert Wine'

class VdlCategory(models.Model):
    name = models.CharField(max_length=100, choices=WineType.choices)
    slug = models.SlugField(max_length=120, unique=True, help_text="URL-friendly version of the name")
    description = models.TextField(blank=True, null=True)
    image = models.ImageField(upload_to='categories/', blank=True, null=True)
    parent = models.ForeignKey('self', null=True, blank=True, related_name='children', on_delete=models.CASCADE)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Category"
        verbose_name_plural = "Categories"

    def __str__(self):
        return self.get_name_display()

class VdlProducer(models.Model):
    name = models.CharField(max_length=150, unique=True)
    slug = models.SlugField(max_length=170, unique=True)
    description = models.TextField(blank=True)
    logo = models.ImageField(upload_to='producers/logos/', blank=True, null=True)
    producer_photo = models.ImageField(upload_to='producers/photos/', blank=True, null=True, help_text="Photo of the producer")
    website = models.URLField(blank=True, null=True)
    region = models.CharField(max_length=100, blank=True)
    is_featured_on_homepage = models.BooleanField(default=False, help_text="Feature this producer on the homepage?")
    
    # Vineyard characteristics
    vineyard_size = models.CharField(max_length=50, blank=True, null=True, help_text="Total vineyard size (e.g., '25 hectares')")
    elevation = models.CharField(max_length=50, blank=True, null=True, help_text="Vineyard elevation (e.g., '150m')")
    soil_type = models.CharField(max_length=100, blank=True, null=True, help_text="Predominant soil type (e.g., 'Clay-limestone')")
    sun_exposure = models.CharField(max_length=50, blank=True, null=True, help_text="Primary sun exposure (e.g., 'South-facing')")
    
    # Map positioning for interactive vineyard map
    map_x_position = models.IntegerField(default=50, help_text="X position on vineyard map (0-100%)")
    map_y_position = models.IntegerField(default=50, help_text="Y position on vineyard map (0-100%)")
    
    # Additional vineyard features
    vineyard_features = models.JSONField(default=list, blank=True, help_text="List of vineyard features (e.g., ['Organic certification', 'Historic estate', 'Award-winning'])")

    def __str__(self):
        return self.name

# --- Refactored Product Models using Inheritance ---

# --- Refactored Product Models using Inheritance ---

class BaseProduct(models.Model):
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220, unique=True)
    category = models.ForeignKey(VdlCategory, on_delete=models.SET_NULL, null=True, blank=True, related_name="%(class)s_set")
    producer = models.ForeignKey(VdlProducer, on_delete=models.SET_NULL, null=True, blank=True, related_name="%(class)s_set")
    short_description = models.TextField(help_text="Brief overview for product listings.")
    full_description = models.TextField(blank=True, null=True, help_text="Detailed description for product page.")
    sku = models.CharField(max_length=50, unique=True, blank=True, null=True, help_text="Stock Keeping Unit")
    is_available = models.BooleanField(default=True, help_text="Is the product available for purchase?")
    
    images = GenericRelation('VdlProductImage')

    class Meta:
        abstract = True

    def __str__(self):
        return self.name

    @property
    def main_image(self):
        """Returns the main featured image from the related images, or the first image as a fallback."""
        return self.images.first().image if self.images.exists() else None

class VdlCoffret(BaseProduct):
    """A one-time purchase wine box (coffret). This is the base for one or more adoption plans."""
    price = models.DecimalField(max_digits=10, decimal_places=2, help_text="Price for a single, one-time purchase of this coffret.")
    stock_quantity = models.PositiveIntegerField(default=0, help_text="Available stock for this coffret")

    class Meta:
        verbose_name = "Coffret"
        verbose_name_plural = "Coffrets"
        
    # No changes needed here, but its related 'adoption_plans' will be accessible.

class VdlAdoptionPlan(BaseProduct):
    """A subscription-style adoption plan for a set duration, based on a specific coffret."""
    # THIS IS THE CORRECT, STRONG RELATIONSHIP
    # An adoption plan MUST be associated with a coffret.
    associated_coffret = models.ForeignKey(
        VdlCoffret,
        on_delete=models.PROTECT,
        # This related_name is key! It lets us do: my_coffret.adoption_plans.all()
        related_name='adoption_plans',
        help_text="The base coffret that this adoption plan sells."
    )

    # This is the price for the PLAN ITSELF, which includes other benefits.
    price = models.DecimalField(max_digits=10, decimal_places=2, help_text="Price for the entire adoption package")

    # ADD DEFAULTS HERE
    duration_months = models.IntegerField(
        help_text="Duration of the adoption in months",
        default=12
    )
    coffrets_per_year = models.IntegerField(
        help_text="Number of seasonal coffrets included per year",
        default=1
    )
    
    includes_visit = models.BooleanField(default=False, help_text="Does this plan include a vineyard visit?")
    visit_details = models.TextField(blank=True, null=True, help_text="Details about the vineyard visit")
    includes_medallion = models.BooleanField(default=False, help_text="Does this plan include a personalized medallion?")
    includes_club_membership = models.BooleanField(default=False, help_text="Does this plan include Club Cuvée Privée membership?")
    avant_premiere_price = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True, help_text="Price for the Avant-Première gift option")
    welcome_kit_description = models.TextField(blank=True, null=True, help_text="Description of what's included in the welcome kit")

    class Meta:
        verbose_name = "Adoption Plan"
        verbose_name_plural = "Adoption Plans"
    
    def __str__(self):
        if self.associated_coffret:
            return f"{self.name} (for {self.associated_coffret.name})"
        return self.name

    def save(self, *args, **kwargs):
        """
        Overrides the default save method to ensure the plan's producer
        is always in sync with its associated coffret's producer.
        """
        if self.associated_coffret:
            self.producer = self.associated_coffret.producer
        super().save(*args, **kwargs) # Call the original save method
    
class VdlProductImage(models.Model):
    """An image that can be associated with any product type (Coffret or AdoptionPlan)."""
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE, help_text="The type of product this image is for.", limit_choices_to={'model__in': ('vdlcoffret', 'vdladoptionplan')})
    object_id = models.PositiveIntegerField(help_text="The ID of the specific product instance.")
    product = GenericForeignKey('content_type', 'object_id')
    image = models.ImageField(upload_to='products/gallery/')
    alt_text = models.CharField(max_length=100, blank=True, help_text="Description for SEO and accessibility")

    def __str__(self):
        return self.alt_text or f"Image for {self.product}"

# --- Order Models --- (No changes needed here)

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
    status = models.CharField(max_length=50, choices=[('pending', 'Pending'), ('processing', 'Processing'), ('shipped', 'Shipped'), ('delivered', 'Cancelled')], default='pending')
    is_gift = models.BooleanField(default=False)
    gift_message = models.TextField(blank=True, null=True)
    payment_intent_id = models.CharField(max_length=100, blank=True, null=True)
    notes = models.TextField(blank=True, null=True, help_text="Customer notes or internal notes")

    def __str__(self):
        return f"Order {self.order_number}"

class VdlOrderItem(models.Model):
    order = models.ForeignKey(VdlOrder, related_name='items', on_delete=models.CASCADE)
    content_type = models.ForeignKey(ContentType, on_delete=models.PROTECT, limit_choices_to={'model__in': ('vdlcoffret', 'vdladoptionplan')})
    object_id = models.PositiveIntegerField()
    product = GenericForeignKey('content_type', 'object_id')
    price_at_purchase = models.DecimalField(max_digits=10, decimal_places=2, help_text="Price of the item when the order was placed")
    quantity = models.PositiveIntegerField(default=1)
    personalization_details = models.TextField(blank=True, null=True)

    def __str__(self):
        return f"{self.quantity} x {self.product.name if self.product else 'N/A'} in Order {self.order.order_number}"

# --- Blog & CMS Models --- (No changes needed here)

class VdlBlogPostCategory(models.Model):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=120, unique=True)

    class Meta:
        verbose_name = "Blog Post Category"
        verbose_name_plural = "Blog Post Categories"

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
    hero_background_image = models.ImageField(upload_to='homepage/', help_text="Background image for the homepage hero section")

    class Meta:
        verbose_name_plural = "Homepage Content"

    def __str__(self):
        return "Homepage Content"
