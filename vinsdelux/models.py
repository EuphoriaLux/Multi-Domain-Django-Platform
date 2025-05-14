from django.db import models
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

class VdlCategory(models.Model):
    name = models.CharField(max_length=100, unique=True)
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
    logo = models.ImageField(upload_to='producers/', blank=True, null=True)
    website = models.URLField(blank=True, null=True)
    region = models.CharField(max_length=100, blank=True)

    def __str__(self):
        return self.name

class VdlProduct(models.Model):
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220, unique=True)
    category = models.ForeignKey(VdlCategory, related_name='products', on_delete=models.SET_NULL, null=True, blank=True)
    producer = models.ForeignKey(VdlProducer, related_name='products', on_delete=models.SET_NULL, null=True, blank=True)
    short_description = models.TextField(help_text="Brief overview for product listings.")
    full_description = models.TextField(blank=True, null=True, help_text="Detailed description for product page.")
    price = models.DecimalField(max_digits=10, decimal_places=2)
    sku = models.CharField(max_length=50, unique=True, blank=True, null=True, help_text="Stock Keeping Unit")
    stock_quantity = models.PositiveIntegerField(default=0)
    is_available = models.BooleanField(default=True, help_text="Is the product available for purchase?")
    is_featured = models.BooleanField(default=False, help_text="Feature on homepage or special sections?")
    main_image = models.ImageField(upload_to='products/main/')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name

class VdlProductImage(models.Model):
    product = models.ForeignKey(VdlProduct, related_name='images', on_delete=models.CASCADE)
    image = models.ImageField(upload_to='products/gallery/')
    alt_text = models.CharField(max_length=100, blank=True, help_text="Description for SEO and accessibility")
    is_feature = models.BooleanField(default=False, help_text="Is this the primary display image?")

    def __str__(self):
        return self.alt_text

class VdlAdoptionTier(models.Model):
    product = models.OneToOneField(VdlProduct, on_delete=models.CASCADE, related_name='adoption_details', help_text="Links to the base product entry")
    vine_location_name = models.CharField(max_length=100, blank=True, help_text="e.g., 'Parcel Les Champs'")
    duration_months = models.IntegerField(default=12, help_text="Duration of the adoption in months")
    bottles_included = models.IntegerField(default=6)
    personalization_options = models.TextField(blank=True, help_text="e.g., 'Name on vine label, personalized certificate'")
    visit_included = models.BooleanField(default=False, help_text="Is a vineyard visit part of this tier?")
    experience_description = models.TextField(blank=True, help_text="What does the adopter get?")

    def __str__(self):
        return f"Adoption Tier for {self.product.name}"

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
    product = models.ForeignKey(VdlProduct, related_name='order_items', on_delete=models.PROTECT)
    price_at_purchase = models.DecimalField(max_digits=10, decimal_places=2, help_text="Price of the item when the order was placed")
    quantity = models.PositiveIntegerField(default=1)
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
