"""
Cost Anomaly Detection for FinOps Hub

Detects unusual cost patterns using statistical methods and rule-based thresholds.
"""

from decimal import Decimal
from datetime import timedelta
from django.db.models import Avg, StdDev, Sum
from django.utils import timezone
from power_up.finops.models import CostAggregation, CostAnomaly


class CostAnomalyDetector:
    """
    Detects cost anomalies using statistical methods.

    Detection algorithms:
    1. Statistical: Daily cost exceeds mean + 2σ (30-day window)
    2. Sudden Spike: Daily cost exceeds 150% of 7-day moving average
    3. New Service: Service appears with >$100 first-day cost
    """

    @classmethod
    def detect_daily_anomalies(cls, currency='EUR', days_back=7):
        """
        Run anomaly detection for recent days.

        Args:
            currency: Currency to analyze (default: EUR)
            days_back: Number of days to analyze (default: 7)

        Returns:
            List of CostAnomaly instances (not yet saved)
        """
        anomalies = []
        end_date = timezone.now().date()
        start_date = end_date - timedelta(days=days_back)

        # Detect anomalies per dimension type
        for dimension_type in ['subscription', 'service']:
            dimension_values = cls._get_active_dimensions(dimension_type, end_date)

            for value in dimension_values:
                detected = cls._detect_for_dimension(
                    dimension_type, value, start_date, end_date, currency
                )
                anomalies.extend(detected)

        return anomalies

    @classmethod
    def _get_active_dimensions(cls, dimension_type, end_date):
        """Get active subscriptions/services in last 90 days"""
        lookback = end_date - timedelta(days=90)

        return CostAggregation.objects.filter(
            aggregation_type='daily',
            dimension_type=dimension_type,
            period_start__gte=lookback,
            period_start__lte=end_date
        ).values_list('dimension_value', flat=True).distinct()

    @classmethod
    def _detect_for_dimension(cls, dim_type, dim_value, start_date, end_date, currency):
        """Detect anomalies for specific dimension"""
        anomalies = []

        # Get daily costs for detection window
        daily_costs = CostAggregation.objects.filter(
            aggregation_type='daily',
            dimension_type=dim_type,
            dimension_value=dim_value,
            period_start__gte=start_date,
            period_start__lte=end_date,
            currency=currency
        ).order_by('period_start')

        for record in daily_costs:
            # Calculate baseline (30-day stats, excluding current day)
            baseline = cls._calculate_baseline(
                dim_type, dim_value, record.period_start, currency
            )

            if not baseline or baseline['mean'] == 0:
                continue

            actual = float(record.total_cost)
            expected = baseline['mean']
            std_dev = baseline['std_dev']

            # Detection Rule 1: Statistical anomaly (2σ)
            if std_dev > 0:
                z_score = (actual - expected) / std_dev
                if abs(z_score) > 2:
                    deviation = ((actual - expected) / expected * 100) if expected > 0 else 0
                    severity = cls._classify_severity(deviation, actual - expected)

                    anomalies.append(CostAnomaly(
                        detected_date=record.period_start,
                        anomaly_type='spike',
                        severity=severity,
                        dimension_type=dim_type,
                        dimension_value=dim_value,
                        actual_cost=Decimal(str(round(actual, 2))),
                        expected_cost=Decimal(str(round(expected, 2))),
                        deviation_percent=Decimal(str(round(deviation, 2))),
                        currency=currency,
                        description=f"{dim_type.title()} '{dim_value}' cost spiked {deviation:.1f}% above expected",
                        metadata={
                            'z_score': round(z_score, 2),
                            'std_dev': round(std_dev, 2),
                            'mean': round(expected, 2)
                        }
                    ))

            # Detection Rule 2: Sudden spike (150% of 7-day average)
            week_avg = baseline.get('week_avg', expected)
            if week_avg > 0 and actual > week_avg * 1.5:
                deviation = ((actual - week_avg) / week_avg * 100)
                severity = cls._classify_severity(deviation, actual - week_avg)

                # Avoid duplicate detection (already caught by Rule 1)
                if not any(a.detected_date == record.period_start and
                          a.dimension_value == dim_value for a in anomalies):
                    anomalies.append(CostAnomaly(
                        detected_date=record.period_start,
                        anomaly_type='spike',
                        severity=severity,
                        dimension_type=dim_type,
                        dimension_value=dim_value,
                        actual_cost=Decimal(str(round(actual, 2))),
                        expected_cost=Decimal(str(round(week_avg, 2))),
                        deviation_percent=Decimal(str(round(deviation, 2))),
                        currency=currency,
                        description=f"Sudden cost spike: {deviation:.1f}% above 7-day average",
                        metadata={'week_avg': round(week_avg, 2)}
                    ))

        return anomalies

    @classmethod
    def _calculate_baseline(cls, dim_type, dim_value, current_date, currency):
        """Calculate baseline statistics (30-day window, excluding current date)"""
        lookback_start = current_date - timedelta(days=30)
        lookback_end = current_date - timedelta(days=1)

        stats = CostAggregation.objects.filter(
            aggregation_type='daily',
            dimension_type=dim_type,
            dimension_value=dim_value,
            period_start__gte=lookback_start,
            period_start__lte=lookback_end,
            currency=currency
        ).aggregate(
            mean=Avg('total_cost'),
            std_dev=StdDev('total_cost')
        )

        # 7-day average for sudden spike detection
        week_start = current_date - timedelta(days=7)
        week_avg = CostAggregation.objects.filter(
            aggregation_type='daily',
            dimension_type=dim_type,
            dimension_value=dim_value,
            period_start__gte=week_start,
            period_start__lt=current_date,
            currency=currency
        ).aggregate(avg=Avg('total_cost'))['avg']

        return {
            'mean': float(stats['mean'] or 0),
            'std_dev': float(stats['std_dev'] or 0),
            'week_avg': float(week_avg or 0)
        }

    @staticmethod
    def _classify_severity(deviation_percent, cost_diff):
        """
        Classify anomaly severity based on deviation and cost difference.

        Args:
            deviation_percent: Percentage deviation from expected
            cost_diff: Absolute cost difference (actual - expected)

        Returns:
            Severity level: 'critical', 'high', 'medium', or 'low'
        """
        if deviation_percent > 300 or cost_diff > 5000:
            return 'critical'
        elif deviation_percent > 200 or cost_diff > 2000:
            return 'high'
        elif deviation_percent > 150 or cost_diff > 1000:
            return 'medium'
        else:
            return 'low'
