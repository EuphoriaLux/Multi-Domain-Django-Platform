"""
Management command to generate cost forecasts for budget planning.
"""

from django.core.management.base import BaseCommand
from power_up.finops.utils.forecaster import CostForecaster
from power_up.finops.models import CostForecast


class Command(BaseCommand):
    help = 'Generate cost forecasts for next 30/90 days'

    def add_arguments(self, parser):
        parser.add_argument(
            '--forecast-days',
            type=int,
            default=30,
            help='Number of days to forecast (default: 30)'
        )
        parser.add_argument(
            '--training-days',
            type=int,
            default=90,
            help='Historical days to use for training (default: 90)'
        )
        parser.add_argument(
            '--dimension',
            choices=['overall', 'subscription'],
            default='overall',
            help='Forecast dimension type (default: overall)'
        )
        parser.add_argument(
            '--currency',
            type=str,
            default='EUR',
            help='Currency code (default: EUR)'
        )
        parser.add_argument(
            '--refresh',
            action='store_true',
            help='Delete old forecasts before generating new ones'
        )

    def handle(self, *args, **options):
        forecast_days = options['forecast_days']
        training_days = options['training_days']
        dimension = options['dimension']
        currency = options['currency']
        refresh = options['refresh']

        if refresh:
            deleted = CostForecast.objects.filter(dimension_type=dimension).delete()
            self.stdout.write(f'Deleted {deleted[0]} old forecasts')

        self.stdout.write(f'Generating {forecast_days}-day forecast for {dimension}...')
        self.stdout.write(f'Using {training_days} days of historical data')

        forecasts = CostForecaster.forecast_costs(
            dimension_type=dimension,
            dimension_value='all',
            forecast_days=forecast_days,
            training_days=training_days,
            currency=currency
        )

        if not forecasts:
            self.stdout.write(self.style.WARNING('Insufficient data for forecasting'))
            self.stdout.write('Need at least 30 days of historical data')
            return

        # Bulk create or update
        CostForecast.objects.bulk_create(
            forecasts,
            update_conflicts=True,
            update_fields=[
                'forecast_cost', 'lower_bound', 'upper_bound',
                'confidence', 'metadata', 'generated_at'
            ],
            unique_fields=['forecast_date', 'dimension_type', 'dimension_value']
        )

        # Print summary
        sample = forecasts[0]
        r_squared = sample.metadata.get('r_squared', 0)
        rmse = sample.metadata.get('rmse', 0)
        slope = sample.metadata.get('slope', 0)

        self.stdout.write(self.style.SUCCESS(f'✓ Generated {len(forecasts)} forecasts'))
        self.stdout.write('')
        self.stdout.write('Model Performance:')
        self.stdout.write(f'  R² (goodness of fit): {r_squared:.3f} (0-1, higher is better)')
        self.stdout.write(f'  RMSE: {rmse:.2f} {currency}')
        self.stdout.write(f'  Trend: {"increasing" if slope > 0 else "decreasing"} '
                         f'({abs(slope):.2f} {currency}/day)')
        self.stdout.write('')
        self.stdout.write('Forecast Preview:')
        self.stdout.write(f'  Next 7 days average: '
                         f'{sum(f.forecast_cost for f in forecasts[:7]) / 7:.2f} {currency}/day')
        self.stdout.write(f'  Next 30 days total: '
                         f'{sum(f.forecast_cost for f in forecasts[:30]):.2f} {currency}')
