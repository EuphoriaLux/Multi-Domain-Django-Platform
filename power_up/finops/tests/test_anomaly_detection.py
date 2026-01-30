"""
Tests for Cost Anomaly Detection (Phase 3)
"""

import pytest
from decimal import Decimal
from datetime import date, timedelta
from django.utils import timezone
from power_up.finops.models import CostAggregation, CostAnomaly
from power_up.finops.utils.anomaly_detector import CostAnomalyDetector


@pytest.mark.django_db
class TestAnomalyDetector:
    """Test cost anomaly detection algorithms"""

    def test_statistical_anomaly_detection(self):
        """Test 2σ detection algorithm"""
        # Create baseline data (30 days, mean=$100, σ=$10)
        base_date = date.today() - timedelta(days=40)
        for i in range(30):
            CostAggregation.objects.create(
                aggregation_type='daily',
                dimension_type='subscription',
                dimension_value='test-subscription',
                period_start=base_date + timedelta(days=i),
                period_end=base_date + timedelta(days=i),
                total_cost=Decimal('100.00'),
                currency='EUR'
            )

        # Create spike day ($150 = 5σ above mean for σ=10)
        spike_date = base_date + timedelta(days=35)
        CostAggregation.objects.create(
            aggregation_type='daily',
            dimension_type='subscription',
            dimension_value='test-subscription',
            period_start=spike_date,
            period_end=spike_date,
            total_cost=Decimal('150.00'),
            currency='EUR'
        )

        # Run detection
        anomalies = CostAnomalyDetector.detect_daily_anomalies(
            currency='EUR',
            days_back=7
        )

        # Assert anomaly detected
        assert len(anomalies) > 0
        anomaly = anomalies[0]
        assert anomaly.dimension_value == 'test-subscription'
        assert anomaly.severity in ['high', 'critical', 'medium']

    def test_sudden_spike_detection(self):
        """Test 150% spike detection"""
        # Create 7-day baseline ($100/day average)
        base_date = date.today() - timedelta(days=10)
        for i in range(7):
            CostAggregation.objects.create(
                aggregation_type='daily',
                dimension_type='service',
                dimension_value='Azure App Service',
                period_start=base_date + timedelta(days=i),
                period_end=base_date + timedelta(days=i),
                total_cost=Decimal('100.00'),
                currency='EUR'
            )

        # Create spike day ($200 = 200% of average)
        spike_date = base_date + timedelta(days=8)
        CostAggregation.objects.create(
            aggregation_type='daily',
            dimension_type='service',
            dimension_value='Azure App Service',
            period_start=spike_date,
            period_end=spike_date,
            total_cost=Decimal('200.00'),
            currency='EUR'
        )

        # Run detection
        anomalies = CostAnomalyDetector.detect_daily_anomalies(
            currency='EUR',
            days_back=3
        )

        # Assert anomaly detected
        assert len(anomalies) > 0

    def test_severity_classification(self):
        """Test severity levels"""
        assert CostAnomalyDetector._classify_severity(350, 6000) == 'critical'
        assert CostAnomalyDetector._classify_severity(250, 3000) == 'high'
        assert CostAnomalyDetector._classify_severity(180, 1500) == 'medium'
        assert CostAnomalyDetector._classify_severity(120, 500) == 'low'

    def test_no_anomaly_for_stable_costs(self):
        """Test that stable costs don't trigger anomalies"""
        # Create 40 days of stable costs
        base_date = date.today() - timedelta(days=40)
        for i in range(40):
            CostAggregation.objects.create(
                aggregation_type='daily',
                dimension_type='subscription',
                dimension_value='stable-subscription',
                period_start=base_date + timedelta(days=i),
                period_end=base_date + timedelta(days=i),
                total_cost=Decimal('100.00'),
                currency='EUR'
            )

        # Run detection
        anomalies = CostAnomalyDetector.detect_daily_anomalies(
            currency='EUR',
            days_back=7
        )

        # Assert no anomalies for stable subscription
        stable_anomalies = [a for a in anomalies if a.dimension_value == 'stable-subscription']
        assert len(stable_anomalies) == 0


@pytest.mark.django_db
class TestAnomalyModel:
    """Test CostAnomaly model"""

    def test_create_anomaly(self):
        """Test creating an anomaly record"""
        anomaly = CostAnomaly.objects.create(
            detected_date=date.today(),
            anomaly_type='spike',
            severity='high',
            dimension_type='subscription',
            dimension_value='test-subscription',
            actual_cost=Decimal('150.00'),
            expected_cost=Decimal('100.00'),
            deviation_percent=Decimal('50.00'),
            currency='EUR',
            description='Cost spike detected'
        )

        assert anomaly.id is not None
        assert anomaly.is_acknowledged is False
        assert anomaly.severity == 'high'

    def test_acknowledge_anomaly(self):
        """Test acknowledging an anomaly"""
        anomaly = CostAnomaly.objects.create(
            detected_date=date.today(),
            anomaly_type='spike',
            severity='critical',
            dimension_type='service',
            dimension_value='Azure SQL',
            actual_cost=Decimal('500.00'),
            expected_cost=Decimal('100.00'),
            deviation_percent=Decimal('400.00'),
            currency='EUR',
            description='Critical cost spike'
        )

        # Acknowledge
        anomaly.is_acknowledged = True
        anomaly.acknowledged_by = 'admin'
        anomaly.acknowledged_at = timezone.now()
        anomaly.save()

        # Verify
        anomaly.refresh_from_db()
        assert anomaly.is_acknowledged is True
        assert anomaly.acknowledged_by == 'admin'
        assert anomaly.acknowledged_at is not None
