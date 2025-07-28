from django.core.management.base import BaseCommand
from vinsdelux.models import VdlProducer, VdlCoffret, VdlAdoptionPlan, VdlProductImage
from django.core.files import File
from django.conf import settings
import os
from django.contrib.contenttypes.models import ContentType

class Command(BaseCommand):
    help = 'Populates the database with sample data for Producers, Coffrets, and Adoption Plans'

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('Starting data population...'))

        # --- Producers ---
        producer_names = ['Château Margaux', 'Domaine de la Romanée-Conti', 'Penfolds', 'Antinori', 'Catena Zapata']
        producer_regions = ['Bordeaux, France', 'Burgundy, France', 'South Australia, Australia', 'Tuscany, Italy', 'Mendoza, Argentina']
        producer_descriptions = [
            'One of the most prestigious wine estates in Bordeaux.',
            'The most famous and sought-after wine producer in Burgundy.',
            'An iconic Australian winery with a rich history.',
            'A renowned Italian wine producer with centuries of tradition.',
            'A leading Argentine winery known for its high-altitude vineyards.'
        ]
        # Assuming you have logo images in media/producers/logos/
        producer_logos = ['producers/logos/producer1.png', 'producers/logos/producer2.png', 'producers/logos/producer3.png', 'producers/logos/producer4.png', 'producers/logos/producer5.png']
        producer_photos = ['homepage/producer1.png', 'homepage/producer1.png', 'homepage/producer1.png', 'homepage/producer1.png', 'homepage/producer1.png']

        producers = []
        for i in range(5):
            producer_name = producer_names[i]
            producer_slug = producer_name.lower().replace(' ', '-')

            # Check if producer already exists
            if VdlProducer.objects.filter(slug=producer_slug).exists():
                self.stdout.write(self.style.WARNING(f'Producer already exists: {producer_name}'))
                producer = VdlProducer.objects.get(slug=producer_slug)
                producers.append(producer)
                continue

            producer = VdlProducer(
                name=producer_name,
                slug=producer_slug,
                description=producer_descriptions[i],
                region=producer_regions[i],
            )

            # Handle logo image
            logo_path = os.path.join(settings.MEDIA_ROOT, producer_logos[i])
            if os.path.exists(logo_path):
                with open(logo_path, 'rb') as logo_file:
                    producer.logo.save(os.path.basename(logo_path), File(logo_file), save=False)
            else:
                self.stdout.write(self.style.WARNING(f'Logo file not found: {logo_path}'))

            # Handle producer photo
            photo_path = os.path.join(settings.MEDIA_ROOT, producer_photos[i])
            if os.path.exists(photo_path):
                with open(photo_path, 'rb') as photo_file:
                    producer.producer_photo.save(os.path.basename(photo_path), File(photo_file), save=False)
            else:
                self.stdout.write(self.style.WARNING(f'Producer photo file not found: {photo_path}'))

            producer.save()
            producers.append(producer)
            self.stdout.write(self.style.SUCCESS(f'Created producer: {producer.name}'))

        # --- Coffrets ---
        coffret_names = ['The Bordeaux Collection', 'Burgundy Discovery Set', 'Australian Shiraz Showcase', 'Tuscan Classics', 'Argentine Malbec Selection']
        coffret_prices = [250.00, 350.00, 200.00, 300.00, 220.00]
        coffret_descriptions = [
            'A curated selection of top Bordeaux wines.',
            'Discover the elegance and complexity of Burgundy.',
            'Experience the bold flavors of Australian Shiraz.',
            'A journey through the heart of Tuscan winemaking.',
            'Explore the rich and fruity Malbecs of Argentina.'
        ]
        # Assuming you have product images in media/products/main/
        coffret_images = ['products/gallery/winebottle1.png', 'products/gallery/winebottle2.png', 'products/gallery/winebottle3.png', 'products/gallery/winebottle4.png', 'products/gallery/winebottle5.png']

        coffrets = []
        for i in range(5):
            coffret_name = coffret_names[i]
            coffret_slug = coffret_name.lower().replace(' ', '-')

            # Check if coffret already exists
            if VdlCoffret.objects.filter(slug=coffret_slug).exists():
                self.stdout.write(self.style.WARNING(f'Coffret already exists: {coffret_name}'))
                coffret = VdlCoffret.objects.get(slug=coffret_slug)
                coffrets.append(coffret)
            else:
                coffret = VdlCoffret(
                    name=coffret_name,
                    slug=coffret_slug,
                    price=coffret_prices[i],
                    short_description=coffret_descriptions[i],
                    full_description=coffret_descriptions[i],
                    producer=producers[i],
                    stock_quantity=10,
                )
                coffret.save()  # Save the coffret before creating the image
                coffrets.append(coffret)
                self.stdout.write(self.style.SUCCESS(f'Created coffret: {coffret.name}'))

            # Handle coffret image
            image_path = os.path.join(settings.MEDIA_ROOT, coffret_images[i])
            if os.path.exists(image_path):
                with open(image_path, 'rb') as image_file:
                    # Get content type for VdlCoffret
                    content_type = ContentType.objects.get_for_model(VdlCoffret)
                    VdlProductImage.objects.create(
                        content_type=content_type,
                        object_id=coffret.id,
                        product=coffret,
                        image=File(image_file, name=os.path.basename(image_path)),
                        alt_text=coffret_name
                    )
            else:
                self.stdout.write(self.style.WARNING(f'Coffret image file not found: {image_path}'))

        # --- Adoption Plans ---
        plan_names = ['Bordeaux Adoption Plan', 'Burgundy Adoption Plan', 'Shiraz Adoption Plan', 'Tuscan Adoption Plan', 'Malbec Adoption Plan']
        plan_prices = [500.00, 700.00, 400.00, 600.00, 440.00]
        plan_descriptions = [
            'Adopt a vine in Bordeaux and receive exclusive benefits.',
            'Become a part of the Burgundy winemaking tradition.',
            'Experience the best of Australian Shiraz.',
            'Support a Tuscan vineyard and enjoy its exquisite wines.',
            'Embrace the Argentine Malbec culture with this unique plan.'
        ]
        # Assuming you have product images in media/products/main/
        plan_images = ['products/gallery/winebottle6.png', 'products/gallery/winebottle7.png', 'products/gallery/winebottle8.png', 'products/gallery/winebottle1.png', 'products/gallery/winebottle2.png']

        for i in range(5):
            plan_name = plan_names[i]
            plan_slug = plan_name.lower().replace(' ', '-')

            if VdlAdoptionPlan.objects.filter(slug=plan_slug).exists():
                self.stdout.write(self.style.WARNING(f'Adoption Plan already exists: {plan_name}'))
                continue

            plan = VdlAdoptionPlan(
                name=plan_name,
                slug=plan_slug,
                price=plan_prices[i],
                short_description=plan_descriptions[i],
                full_description=plan_descriptions[i],
                associated_coffret=coffrets[i],
                producer=producers[i],
                duration_months=12,
                coffrets_per_year=2,
                includes_visit=True,
                visit_details='A guided tour of the vineyard and a tasting session.',
                includes_medallion=True,
                includes_club_membership=True,
            )

            plan.save() # Save the plan before creating the image

            # Handle adoption plan image
            image_path = os.path.join(settings.MEDIA_ROOT, plan_images[i])
            if os.path.exists(image_path):
                with open(image_path, 'rb') as image_file:
                    # Get content type for VdlAdoptionPlan
                    content_type = ContentType.objects.get_for_model(VdlAdoptionPlan)
                    VdlProductImage.objects.create(
                        content_type=content_type,
                        object_id=plan.id,
                        product=plan,
                        image=File(image_file, name=os.path.basename(image_path)),
                        alt_text=plan_name
                    )
            else:
                self.stdout.write(self.style.WARNING(f'Adoption plan image file not found: {image_path}'))

            self.stdout.write(self.style.SUCCESS(f'Created adoption plan: {plan.name}'))

        self.stdout.write(self.style.SUCCESS('Data population complete!'))
