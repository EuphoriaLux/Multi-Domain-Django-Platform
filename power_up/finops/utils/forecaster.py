"""
Cost Forecasting Utility

Predicts future costs using linear regression with weekly seasonality adjustment.
"""

from decimal import Decimal
from datetime import timedelta
from django.utils import timezone
from django.db.models import Sum
from power_up.finops.models import CostAggregation, CostForecast


class CostForecaster:
    """Generate cost forecasts using statistical methods"""

    @classmethod
    def forecast_costs(
        cls,
        dimension_type='overall',
        dimension_value='all',
        forecast_days=30,
        training_days=90,
        currency='EUR'
    ):
        """
        Generate cost forecast using linear regression with seasonality

        Args:
            dimension_type: 'overall', 'subscription', 'service'
            dimension_value: specific value or 'all' for overall
            forecast_days: number of days to forecast (default 30)
            training_days: historical days to use for training (default 90)
            currency: currency code (default 'EUR')

        Returns:
            List of CostForecast instances (not yet saved)
        """
        # Fetch historical data
        end_date = timezone.now().date()
        start_date = end_date - timedelta(days=training_days)

        historical = CostAggregation.objects.filter(
            aggregation_type='daily',
            dimension_type=dimension_type,
            dimension_value=dimension_value,
            period_start__gte=start_date,
            period_start__lte=end_date,
            currency=currency
        ).order_by('period_start')

        if historical.count() < 30:
            return []  # Not enough data for reliable forecast

        # Extract time series
        dates = [h.period_start for h in historical]
        costs = [float(h.total_cost) for h in historical]

        # Fit linear trend
        x = list(range(len(costs)))
        slope, intercept = cls._simple_linear_regression(x, costs)

        # Extract weekly seasonality
        seasonality = cls._extract_weekly_seasonality(dates, costs)

        # Calculate residual standard error for confidence intervals
        residuals = [costs[i] - (slope * i + intercept) for i in range(len(costs))]
        std_error = cls._calculate_std(residuals)

        # Generate forecasts
        forecasts = []
        for i in range(1, forecast_days + 1):
            forecast_date = end_date + timedelta(days=i)
            day_of_week = forecast_date.weekday()

            # Trend component
            x_future = len(costs) + i - 1
            trend = slope * x_future + intercept

            # Seasonality adjustment
            seasonal_adj = seasonality.get(day_of_week, 1.0)
            forecast_value = trend * seasonal_adj

            # 95% Confidence interval (1.96 * standard error)
            margin = 1.96 * std_error

            forecasts.append(CostForecast(
                forecast_date=forecast_date,
                dimension_type=dimension_type,
                dimension_value=dimension_value,
                forecast_cost=Decimal(str(round(max(0, forecast_value), 2))),
                lower_bound=Decimal(str(round(max(0, forecast_value - margin), 2))),
                upper_bound=Decimal(str(round(forecast_value + margin, 2))),
                confidence=Decimal('95.0'),
                currency=currency,
                model_type='linear_regression_with_seasonality',
                training_period_start=start_date,
                training_period_end=end_date,
                training_days=training_days,
                metadata={
                    'slope': slope,
                    'intercept': intercept,
                    'r_squared': cls._calculate_r_squared(costs, slope, intercept),
                    'rmse': cls._calculate_rmse(costs, slope, intercept),
                    'std_error': std_error
                }
            ))

        return forecasts

    @staticmethod
    def _simple_linear_regression(x, y):
        """
        Calculate slope and intercept for y = mx + b

        Args:
            x: list of x values (typically 0, 1, 2, ...)
            y: list of y values (costs)

        Returns:
            tuple: (slope, intercept)
        """
        n = len(x)
        sum_x = sum(x)
        sum_y = sum(y)
        sum_xy = sum(xi * yi for xi, yi in zip(x, y))
        sum_x2 = sum(xi * xi for xi in x)

        denominator = n * sum_x2 - sum_x * sum_x
        if denominator == 0:
            return 0, sum_y / n  # No trend, return average

        m = (n * sum_xy - sum_x * sum_y) / denominator
        b = (sum_y - m * sum_x) / n
        return m, b

    @staticmethod
    def _extract_weekly_seasonality(dates, costs):
        """
        Calculate average cost multiplier per day of week

        Args:
            dates: list of date objects
            costs: list of cost values

        Returns:
            dict: {day_of_week: multiplier} where 0=Monday, 6=Sunday
        """
        by_day = {i: [] for i in range(7)}

        for date, cost in zip(dates, costs):
            by_day[date.weekday()].append(cost)

        # Calculate average for each day
        avg_by_day = {
            day: sum(vals) / len(vals) if vals else 0
            for day, vals in by_day.items()
        }

        overall_avg = sum(avg_by_day.values()) / 7 if avg_by_day else 1

        # Normalize to multipliers
        return {
            day: avg / overall_avg if overall_avg > 0 else 1.0
            for day, avg in avg_by_day.items()
        }

    @staticmethod
    def _calculate_std(values):
        """Calculate standard deviation"""
        n = len(values)
        if n < 2:
            return 0
        mean = sum(values) / n
        variance = sum((x - mean) ** 2 for x in values) / (n - 1)
        return variance ** 0.5

    @staticmethod
    def _calculate_r_squared(y_actual, slope, intercept):
        """
        Calculate R² (coefficient of determination)

        Args:
            y_actual: list of actual values
            slope: regression slope
            intercept: regression intercept

        Returns:
            float: R² value (0-1, higher is better)
        """
        y_mean = sum(y_actual) / len(y_actual)
        ss_tot = sum((y - y_mean) ** 2 for y in y_actual)
        ss_res = sum(
            (y_actual[i] - (slope * i + intercept)) ** 2
            for i in range(len(y_actual))
        )
        return 1 - (ss_res / ss_tot) if ss_tot > 0 else 0

    @staticmethod
    def _calculate_rmse(y_actual, slope, intercept):
        """
        Calculate Root Mean Squared Error

        Args:
            y_actual: list of actual values
            slope: regression slope
            intercept: regression intercept

        Returns:
            float: RMSE value (lower is better)
        """
        n = len(y_actual)
        mse = sum(
            (y_actual[i] - (slope * i + intercept)) ** 2
            for i in range(n)
        ) / n
        return mse ** 0.5
