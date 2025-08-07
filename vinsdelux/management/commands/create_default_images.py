from django.core.management.base import BaseCommand
import os
import shutil
from django.conf import settings

class Command(BaseCommand):
    help = 'Creates default vineyard images for different wine types and regions'

    def handle(self, *args, **options):
        # Base path for default images
        base_path = os.path.join(settings.STATIC_ROOT or 'static', 'images', 'vineyard-defaults')
        
        # Ensure directory exists
        os.makedirs(base_path, exist_ok=True)
        
        # Source images (using journey images as templates)
        source_path = os.path.join('static', 'images', 'journey')
        
        # Image sets to create
        image_sets = [
            'vineyard',  # Default vineyard views
            'red',       # Red wine vineyards
            'white',     # White wine vineyards
            'rose',      # Rosé wine vineyards
            'bordeaux',  # Bordeaux region
            'burgundy',  # Burgundy region
            'luxembourg' # Luxembourg region
        ]
        
        for image_set in image_sets:
            self.stdout.write(f'Creating {image_set} image set...')
            
            for i in range(1, 6):
                source_file = os.path.join(source_path, f'step_0{i}.png')
                dest_file = os.path.join(base_path, f'{image_set}_0{i}.jpg')
                
                if os.path.exists(source_file):
                    # Copy and rename
                    shutil.copy2(source_file, dest_file)
                    self.stdout.write(f'  Created: {image_set}_0{i}.jpg')
                else:
                    self.stdout.write(self.style.WARNING(f'  Source not found: {source_file}'))
        
        self.stdout.write(self.style.SUCCESS('Successfully created default image sets!'))
        self.stdout.write(
            '\nNOTE: These are placeholder images. '
            'Replace them with actual vineyard photos for each category:\n'
            '  - vineyard_01-05.jpg: General vineyard views\n'
            '  - red_01-05.jpg: Red wine vineyard photos\n'
            '  - white_01-05.jpg: White wine vineyard photos\n'
            '  - rose_01-05.jpg: Rosé vineyard photos\n'
            '  - bordeaux_01-05.jpg: Bordeaux region photos\n'
            '  - burgundy_01-05.jpg: Burgundy region photos\n'
            '  - luxembourg_01-05.jpg: Luxembourg vineyard photos\n'
        )