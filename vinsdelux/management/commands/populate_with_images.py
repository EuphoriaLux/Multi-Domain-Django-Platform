from django.core.management.base import BaseCommand
from vinsdelux.models import VdlProducer, VdlCoffret, VdlAdoptionPlan, VdlProductImage, HomepageContent
from django.core.files import File
from django.conf import settings
import os
from django.contrib.contenttypes.models import ContentType
import requests
from io import BytesIO
from django.core.files.uploadedfile import InMemoryUploadedFile

class Command(BaseCommand):
    help = 'Populates the database with sample data and automatically downloads/uploads images'

    def add_arguments(self, parser):
        parser.add_argument(
            '--skip-images',
            action='store_true',
            help='Skip image downloads and uploads',
        )
        parser.add_argument(
            '--force-refresh',
            action='store_true',
            help='Delete existing data and recreate everything',
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('Starting enhanced data population...'))
        
        if options['force_refresh']:
            self.stdout.write('🗑️  Clearing existing data...')
            VdlProductImage.objects.all().delete()
            VdlAdoptionPlan.objects.all().delete()
            VdlCoffret.objects.all().delete()
            VdlProducer.objects.all().delete()
            HomepageContent.objects.all().delete()

        # Sample image URLs from Unsplash (wine-related)
        wine_image_urls = [
            'https://images.unsplash.com/photo-1553361371-9b22f78e8b1d?w=800&h=600&fit=crop&crop=center',  # Red wine
            'https://images.unsplash.com/photo-1506377247377-2a5b3b417ebb?w=800&h=600&fit=crop&crop=center',  # White wine
            'https://images.unsplash.com/photo-1571613316887-6f8d5cbf7ef7?w=800&h=600&fit=crop&crop=center',  # Wine arrangement
            'https://images.unsplash.com/photo-1574870111867-089730e5a72e?w=800&h=600&fit=crop&crop=center',  # Elegant bottle
            'https://images.unsplash.com/photo-1586370434639-0fe43b2d32d6?w=800&h=600&fit=crop&crop=center',  # Premium wine
            'https://images.unsplash.com/photo-1515824065884-5a2d0e6fd06d?w=800&h=600&fit=crop&crop=center',  # Wine collection
            'https://images.unsplash.com/photo-1558618666-fcd25c85cd64?w=800&h=600&fit=crop&crop=center',  # Vintage bottle
            'https://images.unsplash.com/photo-1547595628-c61a29f496f0?w=800&h=600&fit=crop&crop=center',   # Champagne
        ]

        producer_logo_urls = [
            'https://images.unsplash.com/photo-1571613316887-6f8d5cbf7ef7?w=400&h=400&fit=crop&crop=center',
            'https://images.unsplash.com/photo-1586370434639-0fe43b2d32d6?w=400&h=400&fit=crop&crop=center',
            'https://images.unsplash.com/photo-1553361371-9b22f78e8b1d?w=400&h=400&fit=crop&crop=center',
            'https://images.unsplash.com/photo-1506377247377-2a5b3b417ebb?w=400&h=400&fit=crop&crop=center',
            'https://images.unsplash.com/photo-1574870111867-089730e5a72e?w=400&h=400&fit=crop&crop=center',
        ]

        producer_photo_url = 'https://images.unsplash.com/photo-1506905925346-21bda4d32df4?w=600&h=400&fit=crop&crop=center'  # Vineyard

        # --- Create Homepage Content ---
        if not HomepageContent.objects.exists():
            homepage_content = HomepageContent.objects.create(
                hero_title="Discover Premium Wine Experiences",
                hero_subtitle="Adopt a vine, support artisan producers, and enjoy exclusive wine collections delivered to your door."
            )
            
            if not options['skip_images']:
                hero_image_url = 'https://images.unsplash.com/photo-1564760055775-d63b17a55c44?w=1200&h=600&fit=crop&crop=center'
                hero_image = self.download_image(hero_image_url, 'hero-background.jpg')
                if hero_image:
                    homepage_content.hero_background_image.save('hero-background.jpg', hero_image, save=True)
            
            self.stdout.write(self.style.SUCCESS('✅ Created homepage content'))

        # --- Producers ---
        producer_data = [
            {'name': 'Château Margaux', 'region': 'Bordeaux, France', 'desc': 'One of the most prestigious wine estates in Bordeaux.', 'featured': True},
            {'name': 'Domaine de la Romanée-Conti', 'region': 'Burgundy, France', 'desc': 'The most famous and sought-after wine producer in Burgundy.', 'featured': True},
            {'name': 'Penfolds', 'region': 'South Australia, Australia', 'desc': 'An iconic Australian winery with a rich history.', 'featured': True},
            {'name': 'Antinori', 'region': 'Tuscany, Italy', 'desc': 'A renowned Italian wine producer with centuries of tradition.', 'featured': True},
            {'name': 'Catena Zapata', 'region': 'Mendoza, Argentina', 'desc': 'A leading Argentine winery known for its high-altitude vineyards.', 'featured': False},
        ]

        producers = []
        for i, prod_data in enumerate(producer_data):
            slug = prod_data['name'].lower().replace(' ', '-').replace('é', 'e')
            
            producer, created = VdlProducer.objects.get_or_create(
                slug=slug,
                defaults={
                    'name': prod_data['name'],
                    'description': prod_data['desc'],
                    'region': prod_data['region'],
                    'is_featured_on_homepage': prod_data['featured'],
                }
            )
            
            if created and not options['skip_images']:
                # Add logo
                logo_image = self.download_image(producer_logo_urls[i], f'logo_{i+1}.jpg')
                if logo_image:
                    producer.logo.save(f'producer_{i+1}_logo.jpg', logo_image, save=False)
                
                # Add producer photo
                photo_image = self.download_image(producer_photo_url, f'photo_{i+1}.jpg')
                if photo_image:
                    producer.producer_photo.save(f'producer_{i+1}_photo.jpg', photo_image, save=False)
                
                producer.save()
            
            producers.append(producer)
            action = 'Created' if created else 'Found existing'
            self.stdout.write(self.style.SUCCESS(f'✅ {action} producer: {producer.name}'))

        # --- Coffrets ---
        coffret_data = [
            {'name': 'The Bordeaux Collection', 'price': 250.00, 'desc': 'A curated selection of top Bordeaux wines.'},
            {'name': 'Burgundy Discovery Set', 'price': 350.00, 'desc': 'Discover the elegance and complexity of Burgundy.'},
            {'name': 'Australian Shiraz Showcase', 'price': 200.00, 'desc': 'Experience the bold flavors of Australian Shiraz.'},
            {'name': 'Tuscan Classics', 'price': 300.00, 'desc': 'A journey through the heart of Tuscan winemaking.'},
            {'name': 'Argentine Malbec Selection', 'price': 220.00, 'desc': 'Explore the rich and fruity Malbecs of Argentina.'},
        ]

        coffrets = []
        for i, coffret_info in enumerate(coffret_data):
            slug = coffret_info['name'].lower().replace(' ', '-')
            
            coffret, created = VdlCoffret.objects.get_or_create(
                slug=slug,
                defaults={
                    'name': coffret_info['name'],
                    'price': coffret_info['price'],
                    'short_description': coffret_info['desc'],
                    'full_description': f"{coffret_info['desc']} Each bottle is carefully selected to represent the best of the region.",
                    'producer': producers[i],
                    'stock_quantity': 10,
                }
            )
            
            if created and not options['skip_images'] and i < len(wine_image_urls):
                # Add product image
                wine_image = self.download_image(wine_image_urls[i], f'wine_{i+1}.jpg')
                if wine_image:
                    content_type = ContentType.objects.get_for_model(VdlCoffret)
                    VdlProductImage.objects.create(
                        content_type=content_type,
                        object_id=coffret.id,
                        image=wine_image,
                        alt_text=coffret.name
                    )
            
            coffrets.append(coffret)
            action = 'Created' if created else 'Found existing'
            self.stdout.write(self.style.SUCCESS(f'✅ {action} coffret: {coffret.name}'))

        # --- Adoption Plans ---
        plan_data = [
            {'name': 'Bordeaux Adoption Plan', 'price': 500.00, 'desc': 'Adopt a vine in Bordeaux and receive exclusive benefits.'},
            {'name': 'Burgundy Adoption Plan', 'price': 700.00, 'desc': 'Become a part of the Burgundy winemaking tradition.'},
            {'name': 'Shiraz Adoption Plan', 'price': 400.00, 'desc': 'Experience the best of Australian Shiraz.'},
            {'name': 'Tuscan Adoption Plan', 'price': 600.00, 'desc': 'Support a Tuscan vineyard and enjoy its exquisite wines.'},
            {'name': 'Malbec Adoption Plan', 'price': 440.00, 'desc': 'Embrace the Argentine Malbec culture with this unique plan.'},
        ]

        for i, plan_info in enumerate(plan_data):
            slug = plan_info['name'].lower().replace(' ', '-')
            
            plan, created = VdlAdoptionPlan.objects.get_or_create(
                slug=slug,
                defaults={
                    'name': plan_info['name'],
                    'price': plan_info['price'],
                    'short_description': plan_info['desc'],
                    'full_description': f"{plan_info['desc']} Includes seasonal coffrets, vineyard visits, and exclusive member benefits.",
                    'associated_coffret': coffrets[i],
                    'producer': producers[i],
                    'duration_months': 12,
                    'coffrets_per_year': 2,
                    'includes_visit': True,
                    'visit_details': 'A guided tour of the vineyard and a tasting session.',
                    'includes_medallion': True,
                    'includes_club_membership': True,
                }
            )
            
            if created and not options['skip_images'] and (i + 5) < len(wine_image_urls):
                # Add adoption plan image (using different wine images)
                plan_image = self.download_image(wine_image_urls[i + 5], f'plan_{i+1}.jpg')
                if plan_image:
                    content_type = ContentType.objects.get_for_model(VdlAdoptionPlan)
                    VdlProductImage.objects.create(
                        content_type=content_type,
                        object_id=plan.id,
                        image=plan_image,
                        alt_text=plan.name
                    )
            
            action = 'Created' if created else 'Found existing'
            self.stdout.write(self.style.SUCCESS(f'✅ {action} adoption plan: {plan.name}'))

        self.stdout.write(self.style.SUCCESS('🎉 Enhanced data population complete!'))
        if not options['skip_images']:
            self.stdout.write(self.style.SUCCESS('📸 All images downloaded and uploaded to Azure Blob Storage!'))

    def download_image(self, url, filename):
        """Download image from URL and return Django File object"""
        try:
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            
            # Create Django File object
            image_file = InMemoryUploadedFile(
                BytesIO(response.content),
                None,
                filename,
                'image/jpeg',
                len(response.content),
                None
            )
            
            return image_file
            
        except Exception as e:
            self.stdout.write(self.style.WARNING(f'⚠️  Failed to download image from {url}: {e}'))
            return None