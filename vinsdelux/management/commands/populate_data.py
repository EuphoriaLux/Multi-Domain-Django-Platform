from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from vinsdelux.models import VdlUserProfile, VdlAddress, VdlCategory, VdlProducer, VdlProduct, VdlProductImage, VdlAdoptionTier, VdlOrder, VdlOrderItem, VdlBlogPostCategory, VdlBlogPost
from django.utils.text import slugify
import random

class Command(BaseCommand):
    help = 'Populates the database with test data'

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('Starting data population...'))

        # Create users
        users = []
        for i in range(5):
            username = f'user{i}'
            if User.objects.filter(username=username).exists():
                user = User.objects.get(username=username)
            else:
                user = User.objects.create_user(username=username, password='password')
            users.append(user)
            if not VdlUserProfile.objects.filter(user=user).exists():
                VdlUserProfile.objects.create(user=user, phone_number='123-456-7890')

        # Create categories
        categories = []
        for i in range(3):
            name = f'Category {i}'
            slug = slugify(name)
            if VdlCategory.objects.filter(slug=slug).exists():
                category = VdlCategory.objects.get(slug=slug)
            else:
                category = VdlCategory.objects.create(name=name, slug=slug)
            categories.append(category)

        # Create producers
        producers = []
        for i in range(3):
            name = f'Producer {i}'
            slug = slugify(name)
            if VdlProducer.objects.filter(slug=slug).exists():
                producer = VdlProducer.objects.get(slug=slug)
            else:
                producer = VdlProducer.objects.create(name=name, slug=slug)
            producers.append(producer)

        # Create products
        for i in range(10):
            name = f'Product {i}'
            slug = slugify(name)
            if VdlProduct.objects.filter(slug=slug).exists():
                product = VdlProduct.objects.get(slug=slug)
            else:
                product = VdlProduct.objects.create(
                    name=name,
                    slug=slug,
                    category=random.choice(categories),
                    producer=random.choice(producers),
                    short_description='A short description',
                    full_description='A full description',
                    price=random.randint(10, 100),
                    stock_quantity=random.randint(10, 100),
                    is_available=True,
                    is_featured=random.choice([True, False]),
                    main_image='products/main/test_main.jpg'
                )

            # Create product images
            for j in range(2):
                VdlProductImage.objects.create(
                    product=product,
                    image='products/gallery/test_gallery.jpg',
                    alt_text=f'Product {i} Image {j}',
                    is_feature=random.choice([True, False])
                )

        # Create orders
        for i in range(5):
            order_number = f'ORDER{i}'
            if VdlOrder.objects.filter(order_number=order_number).exists():
                order = VdlOrder.objects.get(order_number=order_number)
            else:
                order = VdlOrder.objects.create(
                    user=random.choice(users),
                    order_number=order_number,
                    first_name='John',
                    last_name='Doe',
                    email='john.doe@example.com',
                    shipping_address=VdlAddress.objects.create(user=random.choice(users), address_line_1='123 Main St', city='Anytown', country='USA'),
                    billing_address=VdlAddress.objects.create(user=random.choice(users), address_line_1='123 Main St', city='Anytown', country='USA'),
                    total_paid=random.randint(50, 500),
                )

            # Create order items
            for j in range(3):
                VdlOrderItem.objects.create(
                    order=order,
                    product=random.choice(VdlProduct.objects.all()),
                    price_at_purchase=random.randint(10, 100),
                    quantity=random.randint(1, 5)
                )

        # Create blog post categories
        blog_categories = []
        for i in range(2):
            name = f'Blog Category {i}'
            slug = slugify(name)
            if VdlBlogPostCategory.objects.filter(slug=slug).exists():
                blog_category = VdlBlogPostCategory.objects.get(slug=slug)
            else:
                blog_category = VdlBlogPostCategory.objects.create(name=name, slug=slug)
            blog_categories.append(blog_category)

        # Create blog posts
        for i in range(5):
            title = f'Blog Post {i}'
            slug = slugify(title)
            if VdlBlogPost.objects.filter(slug=slug).exists():
                blog_post = VdlBlogPost.objects.get(slug=slug)
            else:
                VdlBlogPost.objects.create(
                    title=title,
                    slug=slug,
                    author=random.choice(users),
                    category=random.choice(blog_categories),
                    content='Blog post content',
                    excerpt='Blog post excerpt',
                    status='published'
                )

        self.stdout.write(self.style.SUCCESS('Data population complete!'))
