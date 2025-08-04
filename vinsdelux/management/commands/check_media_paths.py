from django.core.management.base import BaseCommand
from vinsdelux.models import VdlProducer, VdlProductImage, HomepageContent
from django.conf import settings

class Command(BaseCommand):
    help = 'Check current media file paths in database'

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('📊 Checking current media file paths...'))
        
        # Check producers
        self.stdout.write('\n🏭 Producer Images:')
        for producer in VdlProducer.objects.all():
            if producer.logo:
                url = producer.logo.url if hasattr(producer.logo, 'url') else str(producer.logo)
                self.stdout.write(f'   {producer.name} - Logo: {url}')
            
            if producer.producer_photo:
                url = producer.producer_photo.url if hasattr(producer.producer_photo, 'url') else str(producer.producer_photo)
                self.stdout.write(f'   {producer.name} - Photo: {url}')
        
        # Check product images
        self.stdout.write('\n📦 Product Images:')
        for image in VdlProductImage.objects.all()[:10]:  # Show first 10
            if image.image:
                url = image.image.url if hasattr(image.image, 'url') else str(image.image)
                self.stdout.write(f'   {image.alt_text}: {url}')
        
        # Check homepage
        self.stdout.write('\n🏠 Homepage Images:')
        for content in HomepageContent.objects.all():
            if content.hero_background_image:
                url = content.hero_background_image.url if hasattr(content.hero_background_image, 'url') else str(content.hero_background_image)
                self.stdout.write(f'   Hero Background: {url}')
        
        self.stdout.write('\n✅ Path check complete!')