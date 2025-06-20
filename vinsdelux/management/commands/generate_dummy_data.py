import os
import random
from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from vinsdelux.models import VdlUserProfile, VdlAddress, VdlCategory, VdlProducer, VdlProduct, VdlProductImage, VdlOrder, VdlOrderItem, VdlBlogPostCategory, VdlBlogPost, HomepageContent
from django.utils.text import slugify
from django.core.files import File
from django.conf import settings
from azure.storage.blob import BlobServiceClient

class Command(BaseCommand):
    help = 'Generates dummy data for the vinsdelux app'

    def handle(self, *args, **options):
        self.stdout.write('Generating dummy data...')

        num_instances = 8
        image_dir = 'static/dummy_data/images'
        media_root = settings.MEDIA_ROOT

        # Azure Blob Storage settings
        azure_account_name = os.getenv('AZURE_ACCOUNT_NAME')
        azure_account_key = os.getenv('AZURE_ACCOUNT_KEY')
        azure_container_name = os.getenv('AZURE_CONTAINER_NAME')

        # Ensure media directory exists
        if not os.path.exists(media_root):
            os.makedirs(media_root)
            self.stdout.write(f"Created media directory at: {media_root}")
        else:
            self.stdout.write(f"Media directory already exists at: {media_root}")


        if azure_account_name and azure_account_key and azure_container_name:
            blob_service_client = BlobServiceClient(account_url=f"https://{azure_account_name}.blob.core.windows.net", credential=azure_account_key)
            container_client = blob_service_client.get_container_client(azure_container_name)
            upload_to_azure = True
            self.stdout.write('Azure Blob Storage configured.')
        else:
            upload_to_azure = False
            self.stdout.write('Azure Blob Storage not configured. Skipping image upload.')

        def copy_image_and_upload(image_name, destination_path):
            # Copy image to media directory
            source_path = os.path.join(image_dir, image_name)
            destination_file_path = os.path.join(media_root, destination_path, image_name)
            os.makedirs(os.path.dirname(destination_file_path), exist_ok=True)

            with open(source_path, 'rb') as source_file, open(destination_file_path, 'wb') as destination_file:
                destination_file.write(source_file.read())

            # Upload image to Azure Blob Storage
            if upload_to_azure:
                blob_client = container_client.get_blob_client(os.path.join(destination_path, image_name))
                with open(destination_file_path, 'rb') as data:
                    blob_client.upload_blob(data, overwrite=True)
                return f'https://{azure_account_name}.blob.core.windows.net/{azure_container_name}/{destination_path}/{image_name}'
            else:
                return os.path.join(destination_path, image_name)

        # Create Users and User Profiles
        if not User.objects.exists():
            for i in range(num_instances):
                username = f'user{i}_{random.randint(1000, 9999)}'
                email = f'user{i}@example.com'
                user = User.objects.create_user(username=username, email=email, password='password123')
                VdlUserProfile.objects.create(user=user)

        # Create Addresses
        if not VdlAddress.objects.exists():
            for i in range(num_instances):
                user = User.objects.get(username=f'user{i}')
                VdlAddress.objects.create(
                    user=user,
                    address_line_1='123 Main St',
                    city='Anytown',
                    state_province='CA',
                    postal_code='12345',
                    country='USA',
                    address_type='shipping'
                )

        # Create Categories
        if not VdlCategory.objects.exists():
            wine_types = ['Red Wine', 'White Wine', 'Rosé', 'Sparkling Wine', 'Dessert Wine']
            for wine_type in wine_types:
                slug = slugify(wine_type) + f'_{random.randint(1000, 9999)}'
                VdlCategory.objects.create(name=wine_type, slug=slug)

        # Create Producers
        if not VdlProducer.objects.exists():
            producer_images = ['producer1.png', 'producer2.png', 'producer3.png', 'producer4.png', 'producer5.png', 'producer6.png', 'producer7.png', 'producer8.png']
            luxembourgish_producers = ["Domaine Alice Hartmann", "Domaine Mathis Bastian", "Domaine Henri Ruppert", "Bernard-Massard", "Caves Krier Frères", "Domaine Clos des Rochers", "Vins Fins A. et R. Gales", "Wengler Châteaux et Domaines"]
            for i in range(num_instances):
                producer_name = luxembourgish_producers[i % len(luxembourgish_producers)]
                producer_slug = slugify(producer_name)
                logo_image = producer_images[i % len(producer_images)]
                logo_url = copy_image_and_upload(logo_image, 'producers/logos')
                producer = VdlProducer.objects.create(name=producer_name, slug=producer_slug, producer_photo=logo_url)

        # Create Products
        if not VdlProduct.objects.exists():
            wine_bottle_images = ['winebottle1.png', 'winebottle2.png', 'winebottle3.png', 'winebottle4.png', 'winebottle5.png', 'winebottle6.png', 'winebottle7.png', 'winebottle8.png']
            luxembourgish_producers = ["Domaine Alice Hartmann", "Domaine Mathis Bastian", "Domaine Henri Ruppert", "Bernard-Massard", "Caves Krier Frères", "Domaine Clos des Rochers", "Vins Fins A. et R. Gales", "Wengler Châteaux et Domaines"]
            for i in range(len(luxembourgish_producers)):
                producer_name = luxembourgish_producers[i]
                producer = VdlProducer.objects.get(name=producer_name)
                category = VdlCategory.objects.order_by('?').first()
                main_image = random.choice(wine_bottle_images)
                main_image_url = copy_image_and_upload(main_image, 'products/main')

                # Create Coffret Product
                product_name_coffret = f'{producer_name} Coffret'
                product_slug_coffret = slugify(product_name_coffret) + f'_{random.randint(1000, 9999)}'
                product_coffret = VdlProduct.objects.create(
                    name=product_name_coffret,
                    slug=product_slug_coffret,
                    category=category,
                    producer=producer,
                    short_description='A short description',
                    product_type='coffret',
                    price=random.randint(10, 100),
                    stock_quantity=random.randint(1, 100),
                    main_image=main_image_url
                )

                # Create Adoption Product
                product_name_adoption = f'{producer_name} Adoption'
                product_slug_adoption = slugify(product_name_adoption) + f'_{random.randint(1000, 9999)}'
                product_adoption = VdlProduct.objects.create(
                    name=product_name_adoption,
                    slug=product_slug_adoption,
                    category=category,
                    producer=producer,
                    short_description='A short description',
                    product_type='adoption',
                    duration_months=12,
                    coffrets_per_year=3,
                    bottles_per_coffret=3,
                    includes_visit=True,
                    visit_details='Lorem ipsum dolor sit amet, consectetur adipiscing elit.',
                    includes_medallion=True,
                    includes_club_membership=True,
                    adoption_price=200,
                    avant_premiere_price=180,
                    welcome_kit_description='Lorem ipsum dolor sit amet, consectetur adipiscing elit.',
                    main_image=main_image_url
                )

        # Create Product Images
        if not VdlProductImage.objects.exists():
            for i in range(num_instances):
                 product = VdlProduct.objects.order_by('?').first()
                 image = random.choice(wine_bottle_images)
                 image_url = copy_image_and_upload(image, 'products/gallery')
                 VdlProductImage.objects.create(product=product, image=image_url, alt_text='Product Image')

        # Create Orders
        if not VdlOrder.objects.exists():
            for i in range(num_instances):
                user = User.objects.get(username=f'user{i}')
                order_number = f'{random.randint(10000000, 99999999)}'
                order = VdlOrder.objects.create(
                    user=user,
                    order_number=order_number,
                    first_name='John',
                    last_name='Doe',
                    email=user.email,
                    shipping_address=VdlAddress.objects.filter(user=user).first(),
                    billing_address=VdlAddress.objects.filter(user=user).first(),
                    total_paid=random.randint(50, 500),
                )

        # Create Order Items
        if not VdlOrderItem.objects.exists():
            for i in range(num_instances):
                order = VdlOrder.objects.order_by('?').first()
                product = VdlProduct.objects.order_by('?').first()
                VdlOrderItem.objects.create(
                    order=order,
                    product=product,
                    price_at_purchase=product.price if product.price else product.adoption_price,
                    quantity=random.randint(1, 5)
                )

        # Create Blog Post Categories
        if not VdlBlogPostCategory.objects.exists():
            blog_categories = ['Wine Education', 'Food Pairing', 'Travel', 'News']
            for category_name in blog_categories:
                category_slug = slugify(category_name) + f'_{random.randint(1000, 9999)}'
                VdlBlogPostCategory.objects.create(name=category_name + f'_{random.randint(1000, 9999)}', slug=category_slug)

        # Create Blog Posts
        if not VdlBlogPost.objects.exists():
            for i in range(num_instances):
                title = f'Blog Post {i}'
                slug = slugify(title) + f'_{random.randint(1000, 9999)}'
                author = User.objects.order_by('?').first()
                category = VdlBlogPostCategory.objects.order_by('?').first()
                VdlBlogPost.objects.create(
                    title=title,
                    slug=slug,
                    author=author,
                    category=category,
                    content='Lorem ipsum dolor sit amet',
                    excerpt='Lorem ipsum',
                )

       # Create Homepage Content
        if not HomepageContent.objects.exists():
            HomepageContent.objects.create(
                hero_title='Welcome to Vins Delux',
                hero_subtitle='Discover the world of fine wines',
                hero_background_image=copy_image_and_upload('winebottle1.png', '')  # Use one of the wine bottle images
            )

        self.stdout.write(self.style.SUCCESS('Successfully generated dummy data'))
