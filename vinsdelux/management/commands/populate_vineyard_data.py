from django.core.management.base import BaseCommand
from vinsdelux.models import VdlProducer
import random

class Command(BaseCommand):
    help = 'Populate vineyard characteristics for existing producers'

    def handle(self, *args, **options):
        # Sample data for vineyard characteristics
        soil_types = [
            'Clay-limestone',
            'Sandy loam',
            'Granite and schist',
            'Marl and limestone',
            'Limestone and clay',
            'Volcanic soil',
            'Alluvial soil',
            'Gravel and pebbles'
        ]
        
        exposures = [
            'South-facing',
            'Southeast',
            'Southwest',
            'East-facing',
            'West-facing',
            'North-facing slopes'
        ]
        
        vineyard_features_options = [
            ['Organic certification', 'Historic estate', 'Award-winning'],
            ['Biodynamic practices', 'Century-old vines', 'UNESCO heritage site'],
            ['Sustainable farming', 'Family-owned', 'Small production'],
            ['Premium terroir', 'Hand-harvested', 'Traditional methods'],
            ['Coastal influence', 'Mountain vineyard', 'River valley location'],
            ['Old vines', 'Limited production', 'Estate bottled']
        ]
        
        producers = VdlProducer.objects.all()
        
        if not producers:
            self.stdout.write(self.style.WARNING('No producers found in database'))
            return
            
        for i, producer in enumerate(producers):
            # Generate vineyard data if not already set
            if not producer.vineyard_size:
                producer.vineyard_size = f"{random.randint(5, 50)} hectares"
            
            if not producer.elevation:
                producer.elevation = f"{random.randint(50, 500)}m"
            
            if not producer.soil_type:
                producer.soil_type = random.choice(soil_types)
            
            if not producer.sun_exposure:
                producer.sun_exposure = random.choice(exposures)
            
            # Set map positions (distribute across the map)
            if producer.map_x_position == 50 and producer.map_y_position == 50:
                # Create a grid-like distribution
                producer.map_x_position = 20 + (i % 3) * 30 + random.randint(-5, 5)
                producer.map_y_position = 20 + (i // 3) * 25 + random.randint(-5, 5)
                
                # Keep within bounds
                producer.map_x_position = max(10, min(90, producer.map_x_position))
                producer.map_y_position = max(10, min(90, producer.map_y_position))
            
            if not producer.vineyard_features:
                producer.vineyard_features = random.choice(vineyard_features_options)
            
            producer.save()
            
            self.stdout.write(
                self.style.SUCCESS(
                    f'Updated vineyard data for {producer.name}:\n'
                    f'  - Size: {producer.vineyard_size}\n'
                    f'  - Elevation: {producer.elevation}\n'
                    f'  - Soil: {producer.soil_type}\n'
                    f'  - Exposure: {producer.sun_exposure}\n'
                    f'  - Map position: ({producer.map_x_position}, {producer.map_y_position})\n'
                    f'  - Features: {producer.vineyard_features}\n'
                )
            )
        
        self.stdout.write(
            self.style.SUCCESS(f'Successfully updated vineyard data for {len(producers)} producers')
        )