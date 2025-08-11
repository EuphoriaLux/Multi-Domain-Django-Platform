"""
Management command to update plot descriptions based on their producers
"""

from django.core.management.base import BaseCommand
from vinsdelux.models import VdlPlot


class Command(BaseCommand):
    help = 'Update plot wine profiles based on their producers'

    def handle(self, *args, **options):
        # Producer-specific wine profiles
        producer_profiles = {
            'Château Margaux': {
                'wine_profiles': [
                    'Classic Bordeaux blend with Cabernet Sauvignon dominance, offering complex notes of blackcurrant, cedar, and tobacco',
                    'Elegant Margaux style with silky tannins, featuring violet aromatics and a long, refined finish',
                    'Grand Vin quality potential with exceptional aging capability, showing graphite and dark fruit complexity'
                ],
                'grape_varieties': [
                    ['Cabernet Sauvignon', 'Merlot', 'Petit Verdot'],
                    ['Cabernet Sauvignon', 'Merlot', 'Cabernet Franc'],
                    ['Merlot', 'Cabernet Sauvignon']
                ]
            },
            'Domaine de la Romanée-Conti': {
                'wine_profiles': [
                    'Exceptional Pinot Noir with ethereal complexity, showing red cherry, forest floor, and Asian spices',
                    'Burgundian excellence with perfect balance, featuring rose petals, wild strawberry, and mineral undertones',
                    'Grand Cru quality potential with incredible finesse, displaying truffle, leather, and dark cherry notes'
                ],
                'grape_varieties': [
                    ['Pinot Noir'],
                    ['Pinot Noir'],
                    ['Pinot Noir', 'Chardonnay']
                ]
            },
            'Penfolds': {
                'wine_profiles': [
                    'Bold Australian Shiraz with intense dark fruit, chocolate, and signature American oak vanilla notes',
                    'Grange-style potential with concentrated blackberry, licorice, and espresso flavors',
                    'Multi-regional blend showcasing power and elegance, with plum, spice, and mocha complexity'
                ],
                'grape_varieties': [
                    ['Shiraz'],
                    ['Shiraz', 'Cabernet Sauvignon'],
                    ['Shiraz', 'Grenache', 'Mataro']
                ]
            },
            'Antinori': {
                'wine_profiles': [
                    'Super Tuscan style with Sangiovese and international varieties, showing cherry, leather, and Mediterranean herbs',
                    'Tignanello-inspired blend with vibrant acidity, featuring red fruits, tobacco, and balsamic notes',
                    'Classic Chianti character with violet, cherry, and earthy undertones, perfect expression of terroir'
                ],
                'grape_varieties': [
                    ['Sangiovese', 'Cabernet Sauvignon', 'Cabernet Franc'],
                    ['Sangiovese', 'Merlot'],
                    ['Sangiovese']
                ]
            },
            'Catena Zapata': {
                'wine_profiles': [
                    'High-altitude Malbec with intense violet color, showing plum, blackberry, and floral aromatics',
                    'Adrianna Vineyard style with mineral complexity, featuring black fruit, graphite, and mountain herbs',
                    'Argentine excellence with velvety tannins, displaying chocolate, fig, and spice notes'
                ],
                'grape_varieties': [
                    ['Malbec'],
                    ['Malbec', 'Cabernet Sauvignon'],
                    ['Malbec', 'Petit Verdot']
                ]
            }
        }

        updated_count = 0
        
        for plot in VdlPlot.objects.select_related('producer').all():
            producer_name = plot.producer.name
            
            if producer_name in producer_profiles:
                profile_data = producer_profiles[producer_name]
                
                # Select profile based on plot ID to vary them
                profile_index = plot.id % len(profile_data['wine_profiles'])
                
                plot.wine_profile = profile_data['wine_profiles'][profile_index]
                plot.grape_varieties = profile_data['grape_varieties'][profile_index]
                
                # Add microclimate notes specific to Luxembourg adaptation
                plot.microclimate_notes = f"Luxembourg terroir adaptation of {producer_name}'s signature style, benefiting from the Moselle valley's unique microclimate"
                
                plot.save()
                updated_count += 1
                
                self.stdout.write(
                    self.style.SUCCESS(f'Updated: {plot.name} - {plot.wine_profile[:50]}...')
                )
        
        self.stdout.write(
            self.style.SUCCESS(f'\nSuccessfully updated {updated_count} plots with producer-specific profiles')
        )