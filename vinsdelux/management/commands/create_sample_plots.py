"""
Management command to create sample plot data for testing the enhanced plot selection
"""

from django.core.management.base import BaseCommand
from django.utils import timezone
from vinsdelux.models import VdlPlot, VdlProducer, VdlAdoptionPlan, PlotStatus
from decimal import Decimal
import random


class Command(BaseCommand):
    help = 'Create sample vineyard plots for testing the enhanced plot selection'

    def add_arguments(self, parser):
        parser.add_argument(
            '--count',
            type=int,
            default=15,
            help='Number of plots to create (default: 15)'
        )
        parser.add_argument(
            '--clear',
            action='store_true',
            help='Clear existing plots before creating new ones'
        )

    def handle(self, *args, **options):
        count = options['count']
        clear = options['clear']

        if clear:
            deleted_count = VdlPlot.objects.count()
            VdlPlot.objects.all().delete()
            self.stdout.write(
                self.style.SUCCESS(f'Deleted {deleted_count} existing plots')
            )

        # Get all producers
        producers = list(VdlProducer.objects.all())
        if not producers:
            self.stdout.write(
                self.style.ERROR('No producers found. Please create some producers first.')
            )
            return

        # Get adoption plans
        adoption_plans = list(VdlAdoptionPlan.objects.filter(is_available=True))

        # Sample plot data templates - Luxembourg wine regions
        plot_templates = [
            {
                'name_template': 'Moselle Hillside {}',
                'coordinates_base': [49.6116, 6.1319],  # Luxembourg - Moselle region
                'soil_types': ['Clay-limestone', 'Marl', 'Volcanic'],
                'sun_exposures': ['South-facing', 'South-east facing', 'South-west facing'],
                'grape_varieties': [['Pinot Noir'], ['Chardonnay'], ['Pinot Noir', 'Gamay']],
                'wine_profiles': [
                    'Full-bodied red with notes of dark cherry and spice',
                    'Elegant red with earthy undertones and silky tannins',
                    'Complex red blend with fruit-forward character'
                ]
            },
            {
                'name_template': 'Remich Valley {}',
                'coordinates_base': [49.5447, 6.3669],
                'soil_types': ['Sandy loam', 'Limestone', 'Clay-sand mixture'],
                'sun_exposures': ['East-facing', 'North-east facing', 'Morning sun'],
                'grape_varieties': [['Chardonnay'], ['Sauvignon Blanc'], ['Riesling']],
                'wine_profiles': [
                    'Elegant white with citrus and mineral notes',
                    'Crisp white with herbaceous character',
                    'Aromatic white with floral and stone fruit notes'
                ]
            },
            {
                'name_template': 'Grevenmacher Terrace {}',
                'coordinates_base': [49.6803, 6.4417],
                'soil_types': ['Schist', 'Granite', 'Slate'],
                'sun_exposures': ['West-facing', 'South-west facing', 'Evening sun'],
                'grape_varieties': [['Syrah'], ['Grenache'], ['Mourvèdre']],
                'wine_profiles': [
                    'Bold red with peppery spice and dark fruit',
                    'Warm-climate red with berry flavors',
                    'Structured red with gamey, earthy notes'
                ]
            },
            {
                'name_template': 'Wormeldange Heritage {}',
                'coordinates_base': [49.6114, 6.4003],
                'soil_types': ['Ancient limestone', 'Fossil-rich clay', 'Calcareous'],
                'sun_exposures': ['South-facing slopes', 'Protected valley', 'Terraced hillside'],
                'grape_varieties': [['Pinot Noir'], ['Chasselas'], ['Petite Arvine']],
                'wine_profiles': [
                    'Traditional Burgundian-style Pinot with complexity',
                    'Local Swiss variety with fresh, mineral character',
                    'Rare alpine variety with distinctive mountain terroir'
                ]
            },
            {
                'name_template': 'Schengen Premium {}',
                'coordinates_base': [49.4697, 6.3622],
                'soil_types': ['Premier Cru limestone', 'Marl with fossils', 'Iron-rich clay'],
                'sun_exposures': ['Optimal south exposure', 'Protected from wind', 'Perfect drainage'],
                'grape_varieties': [['Pinot Noir'], ['Chardonnay'], ['Cabernet Sauvignon']],
                'wine_profiles': [
                    'Premium Pinot with exceptional aging potential',
                    'Grand Cru style Chardonnay with minerality',
                    'Bordeaux-style blend with power and elegance'
                ]
            }
        ]

        # Size variations
        plot_sizes = [
            '0.15 hectares', '0.20 hectares', '0.25 hectares', '0.30 hectares', 
            '0.35 hectares', '0.40 hectares', '0.50 hectares'
        ]

        # Elevation variations
        elevations = [
            '320m', '350m', '380m', '420m', '450m', '480m', '520m', '550m'
        ]

        # Expected yields
        expected_yields = [
            '200-250 bottles', '250-300 bottles', '300-350 bottles', 
            '350-400 bottles', '150-200 bottles (premium)', '400-450 bottles'
        ]

        created_plots = []

        for i in range(count):
            # Choose random template and producer
            template = random.choice(plot_templates)
            producer = random.choice(producers)
            
            # Generate coordinates with some variation
            base_lat, base_lng = template['coordinates_base']
            lat_variation = random.uniform(-0.01, 0.01)  # Small variation
            lng_variation = random.uniform(-0.01, 0.01)
            coordinates = {
                'type': 'Point',
                'coordinates': [base_lng + lng_variation, base_lat + lat_variation]
            }

            # Create meaningful plot name based on producer
            plot_name = f"{producer.name} - {template['name_template'].format('')}".replace(' - ', ' ').strip()
            if i % 3 == 0:
                plot_name = f"{producer.name} Reserve Plot"
            elif i % 3 == 1:
                plot_name = f"{producer.name} Heritage Vineyard"
            else:
                plot_name = f"{producer.name} Premier Terroir"
            
            # Create the plot
            plot = VdlPlot.objects.create(
                name=plot_name,
                plot_identifier=f'PLT-{producer.name[:3].upper()}-{i+1:03d}',
                producer=producer,
                coordinates=coordinates,
                plot_size=random.choice(plot_sizes),
                elevation=random.choice(elevations),
                soil_type=random.choice(template['soil_types']),
                sun_exposure=random.choice(template['sun_exposures']),
                microclimate_notes=f'Unique microclimate characteristics for plot {i+1}',
                grape_varieties=random.choice(template['grape_varieties']),
                vine_age=random.randint(8, 35),
                harvest_year=2024 + random.randint(0, 2),  # 2024-2026
                wine_profile=random.choice(template['wine_profiles']),
                expected_yield=random.choice(expected_yields),
                status=random.choices(
                    [PlotStatus.AVAILABLE, PlotStatus.RESERVED],
                    weights=[85, 15]  # 85% available, 15% reserved
                )[0],
                base_price=Decimal(random.randint(1800, 5500)),
                is_premium=random.choices([True, False], weights=[20, 80])[0]  # 20% premium
            )

            # Associate with random adoption plans
            if adoption_plans:
                # Choose 1-3 adoption plans randomly
                selected_plans = random.sample(
                    adoption_plans, 
                    min(random.randint(1, 3), len(adoption_plans))
                )
                plot.adoption_plans.set(selected_plans)

            created_plots.append(plot)

        self.stdout.write(
            self.style.SUCCESS(f'Successfully created {len(created_plots)} sample plots:')
        )
        
        for plot in created_plots:
            status_color = self.style.SUCCESS if plot.is_available else self.style.WARNING
            premium_indicator = ' (Premium)' if plot.is_premium else ''
            self.stdout.write(
                f'  - {status_color(plot.name)} - {plot.producer.name} - €{plot.base_price}{premium_indicator}'
            )

        # Display summary statistics
        total_plots = VdlPlot.objects.count()
        available_plots = VdlPlot.objects.filter(status=PlotStatus.AVAILABLE).count()
        premium_plots = VdlPlot.objects.filter(is_premium=True).count()
        
        self.stdout.write(
            self.style.SUCCESS(f'\nDatabase Summary:')
        )
        self.stdout.write(f'  Total plots: {total_plots}')
        self.stdout.write(f'  Available plots: {available_plots}')
        self.stdout.write(f'  Premium plots: {premium_plots}')
        self.stdout.write(f'  Producers with plots: {VdlPlot.objects.values("producer").distinct().count()}')

        if adoption_plans:
            self.stdout.write(f'  Plots with adoption plans: {VdlPlot.objects.filter(adoption_plans__isnull=False).distinct().count()}')

        self.stdout.write(
            self.style.SUCCESS(f'\nYou can now test the enhanced plot selection at /journey/enhanced-plot-selection/')
        )