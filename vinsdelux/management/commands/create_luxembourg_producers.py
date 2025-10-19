"""
Management command to create authentic Luxembourg wine producers
"""

from django.core.management.base import BaseCommand
from vinsdelux.models import VdlProducer
from decimal import Decimal


class Command(BaseCommand):
    help = 'Create authentic Luxembourg wine producers'

    def handle(self, *args, **options):
        # Luxembourg wine producers data
        luxembourg_producers = [
            {
                'name': 'Domaine Viticole Poll-Fabaire',
                'slug': 'poll-fabaire',
                'region': 'Wormeldange, Luxembourg',
                'description': 'Family-owned winery in the heart of the Moselle region, specializing in Riesling and Pinot Gris since 1920.',
                'website': 'https://poll-fabaire.lu',
                'founding_year': 1920,
                'vineyard_area': '12 hectares',
                'annual_production': '80,000 bottles',
                'certification': 'Organic certified since 2015',
                'winemaker': 'Marc Poll',
                'specialties': 'Riesling, Pinot Gris, Crémant de Luxembourg',
                'awards': 'Gold Medal Concours Mondial de Bruxelles 2023',
                'philosophy': 'Tradition meets innovation in every bottle',
                'terroir': 'Limestone and marl soils along the Moselle slopes'
            },
            {
                'name': 'Caves Bernard-Massard',
                'slug': 'bernard-massard',
                'region': 'Grevenmacher, Luxembourg',
                'description': 'Luxembourg\'s leading producer of sparkling wines and Crémant since 1921.',
                'website': 'https://bernard-massard.lu',
                'founding_year': 1921,
                'vineyard_area': '42 hectares',
                'annual_production': '3,000,000 bottles',
                'certification': 'ISO 9001 certified',
                'winemaker': 'Antoine Clasen',
                'specialties': 'Crémant de Luxembourg, Cuvée de l\'Écusson',
                'awards': 'Best European Sparkling Wine 2022',
                'philosophy': 'Excellence in traditional method sparkling wines',
                'terroir': 'South-facing slopes with optimal sun exposure'
            },
            {
                'name': 'Domaine Alice Hartmann',
                'slug': 'alice-hartmann',
                'region': 'Remich, Luxembourg',
                'description': 'Boutique winery focusing on natural wines and biodynamic viticulture.',
                'website': 'https://alice-hartmann.lu',
                'founding_year': 1998,
                'vineyard_area': '8 hectares',
                'annual_production': '40,000 bottles',
                'certification': 'Biodynamic Demeter certified',
                'winemaker': 'Alice Hartmann',
                'specialties': 'Natural Riesling, Auxerrois, Pinot Noir',
                'awards': 'Prix Bio Luxembourg 2023',
                'philosophy': 'Minimal intervention winemaking respecting nature',
                'terroir': 'Ancient riverbed soils with high mineral content'
            },
            {
                'name': 'Caves St Martin',
                'slug': 'caves-st-martin',
                'region': 'Remich, Luxembourg',
                'description': 'Historic cooperative winery representing 50 local winegrowers.',
                'website': 'https://caves-st-martin.lu',
                'founding_year': 1919,
                'vineyard_area': '120 hectares',
                'annual_production': '1,000,000 bottles',
                'certification': 'Luxembourg Moselle AOP',
                'winemaker': 'Claude Bentz',
                'specialties': 'Elbling, Rivaner, Gewürztraminer',
                'awards': 'National Wine Trophy Luxembourg 2023',
                'philosophy': 'Cooperative excellence through shared expertise',
                'terroir': 'Diverse terroirs across the Moselle valley'
            },
            {
                'name': 'Château de Schoenfels',
                'slug': 'chateau-schoenfels',
                'region': 'Schoenfels, Luxembourg',
                'description': 'Historic estate winery with medieval castle, producing premium wines since 1780.',
                'website': 'https://chateau-schoenfels.lu',
                'founding_year': 1780,
                'vineyard_area': '25 hectares',
                'annual_production': '150,000 bottles',
                'certification': 'Marque Nationale du Vin Luxembourgeois',
                'winemaker': 'Henri Ruppert',
                'specialties': 'Grand Premier Cru Riesling, Pinot Blanc',
                'awards': 'Decanter World Wine Awards 2023',
                'philosophy': 'Heritage and terroir in perfect harmony',
                'terroir': 'Steep slopes with schist and limestone soils'
            },
            {
                'name': 'Domaine Sunnen-Hoffmann',
                'slug': 'sunnen-hoffmann',
                'region': 'Schengen, Luxembourg',
                'description': 'Modern winery at the tripoint of Luxembourg, France, and Germany.',
                'website': 'https://sunnen-hoffmann.lu',
                'founding_year': 2005,
                'vineyard_area': '15 hectares',
                'annual_production': '100,000 bottles',
                'certification': 'Fair\'n Green certified',
                'winemaker': 'Yves Sunnen',
                'specialties': 'Pinot Noir, Chardonnay, Crémant Rosé',
                'awards': 'Innovation Award Luxembourg Wine 2023',
                'philosophy': 'Sustainable viticulture for future generations',
                'terroir': 'Unique microclimate at the European crossroads'
            },
            {
                'name': 'Clos des Rochers',
                'slug': 'clos-des-rochers',
                'region': 'Grevenmacher, Luxembourg',
                'description': 'Artisanal producer focusing on single-vineyard expressions.',
                'website': 'https://clos-des-rochers.lu',
                'founding_year': 1989,
                'vineyard_area': '6 hectares',
                'annual_production': '30,000 bottles',
                'certification': 'Terra Vitis certified',
                'winemaker': 'Sophie Molitor',
                'specialties': 'Single-vineyard Riesling, Late Harvest wines',
                'awards': 'Best Riesling Luxembourg 2023',
                'philosophy': 'Each vineyard tells its own story',
                'terroir': 'Rocky slopes with exceptional drainage'
            },
            {
                'name': 'Domaine Viticole Schumacher-Knepper',
                'slug': 'schumacher-knepper',
                'region': 'Wintrange, Luxembourg',
                'description': 'Family estate known for traditional winemaking methods.',
                'website': 'https://schumacher-knepper.lu',
                'founding_year': 1957,
                'vineyard_area': '10 hectares',
                'annual_production': '60,000 bottles',
                'certification': 'Luxembourg Moselle AOP',
                'winemaker': 'Aly Schumacher',
                'specialties': 'Elbling, Auxerrois, Pinot Gris',
                'awards': 'Concours National des Vins 2023',
                'philosophy': 'Respect for tradition and terroir',
                'terroir': 'South-facing slopes with clay-limestone soils'
            }
        ]

        # Clear existing producers if requested
        if options.get('clear', False):
            VdlProducer.objects.all().delete()
            self.stdout.write(self.style.SUCCESS('Cleared existing producers'))

        created_count = 0
        updated_count = 0
        
        for producer_data in luxembourg_producers:
            producer, created = VdlProducer.objects.update_or_create(
                slug=producer_data['slug'],
                defaults=producer_data
            )
            
            if created:
                created_count += 1
                self.stdout.write(self.style.SUCCESS(f'Created: {producer.name}'))
            else:
                updated_count += 1
                self.stdout.write(self.style.WARNING(f'Updated: {producer.name}'))
        
        self.stdout.write(
            self.style.SUCCESS(
                f'\nSummary:\n'
                f'Created: {created_count} producers\n'
                f'Updated: {updated_count} producers\n'
                f'Total: {VdlProducer.objects.count()} producers in database'
            )
        )

    def add_arguments(self, parser):
        parser.add_argument(
            '--clear',
            action='store_true',
            help='Clear existing producers before creating new ones',
        )