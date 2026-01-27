"""
Tests for push subscription refresh and validation endpoints.
"""

import pytest
from django.utils import timezone
from crush_lu.models import PushSubscription


@pytest.mark.django_db
class TestRefreshSubscription:
    """Test the refresh_subscription API endpoint"""

    def test_refresh_subscription_success(self, client, test_user):
        """Test successful subscription refresh"""
        # Create old subscription
        old_sub = PushSubscription.objects.create(
            user=test_user,
            endpoint='https://old.endpoint.com/123',
            p256dh_key='old_p256dh',
            auth_key='old_auth',
            failure_count=3
        )

        client.force_login(test_user)

        response = client.post('/api/push/refresh-subscription/', {
            'oldEndpoint': 'https://old.endpoint.com/123',
            'subscription': {
                'endpoint': 'https://new.endpoint.com/456',
                'keys': {
                    'p256dh': 'new_p256dh',
                    'auth': 'new_auth'
                }
            }
        }, content_type='application/json')

        assert response.status_code == 200
        data = response.json()
        assert data['success'] is True

        # Verify subscription was updated
        old_sub.refresh_from_db()
        assert old_sub.endpoint == 'https://new.endpoint.com/456'
        assert old_sub.p256dh_key == 'new_p256dh'
        assert old_sub.auth_key == 'new_auth'
        assert old_sub.failure_count == 0  # Reset on refresh

    def test_refresh_subscription_without_old_endpoint(self, client, test_user):
        """Test refresh when oldEndpoint is not provided"""
        # Create subscription with new endpoint already
        existing_sub = PushSubscription.objects.create(
            user=test_user,
            endpoint='https://new.endpoint.com/456',
            p256dh_key='old_p256dh',
            auth_key='old_auth'
        )

        client.force_login(test_user)

        response = client.post('/api/push/refresh-subscription/', {
            'subscription': {
                'endpoint': 'https://new.endpoint.com/456',
                'keys': {
                    'p256dh': 'updated_p256dh',
                    'auth': 'updated_auth'
                }
            }
        }, content_type='application/json')

        assert response.status_code == 200
        data = response.json()
        assert data['success'] is True
        assert 'already exists' in data['message']

        # Verify keys were updated
        existing_sub.refresh_from_db()
        assert existing_sub.p256dh_key == 'updated_p256dh'
        assert existing_sub.auth_key == 'updated_auth'

    def test_refresh_subscription_not_found(self, client, test_user):
        """Test refresh when original subscription doesn't exist"""
        client.force_login(test_user)

        response = client.post('/api/push/refresh-subscription/', {
            'oldEndpoint': 'https://nonexistent.endpoint.com/999',
            'subscription': {
                'endpoint': 'https://new.endpoint.com/456',
                'keys': {
                    'p256dh': 'new_p256dh',
                    'auth': 'new_auth'
                }
            }
        }, content_type='application/json')

        assert response.status_code == 404
        data = response.json()
        assert data['success'] is False
        assert 'not found' in data['message'].lower()

    def test_refresh_subscription_missing_keys(self, client, test_user):
        """Test refresh with missing subscription keys"""
        client.force_login(test_user)

        response = client.post('/api/push/refresh-subscription/', {
            'oldEndpoint': 'https://old.endpoint.com/123',
            'subscription': {
                'endpoint': 'https://new.endpoint.com/456',
                'keys': {
                    'p256dh': 'new_p256dh'
                    # Missing 'auth'
                }
            }
        }, content_type='application/json')

        assert response.status_code == 400
        data = response.json()
        assert data['success'] is False
        assert 'keys' in data['message'].lower()

    def test_refresh_subscription_requires_auth(self, client):
        """Test that endpoint requires authentication"""
        response = client.post('/api/push/refresh-subscription/', {
            'subscription': {
                'endpoint': 'https://new.endpoint.com/456',
                'keys': {
                    'p256dh': 'new_p256dh',
                    'auth': 'new_auth'
                }
            }
        }, content_type='application/json')

        # Should redirect to login (302) or return 403
        assert response.status_code in [302, 403]


@pytest.mark.django_db
class TestValidateSubscription:
    """Test the validate_subscription API endpoint"""

    def test_validate_healthy_subscription(self, client, test_user):
        """Test validation of healthy subscription"""
        sub = PushSubscription.objects.create(
            user=test_user,
            endpoint='https://test.endpoint.com/123',
            p256dh_key='test_p256dh',
            auth_key='test_auth',
            enabled=True,
            failure_count=0
        )

        client.force_login(test_user)

        response = client.post('/api/push/validate-subscription/', {
            'endpoint': 'https://test.endpoint.com/123'
        }, content_type='application/json')

        assert response.status_code == 200
        data = response.json()
        assert data['success'] is True
        assert data['valid'] is True
        assert data['message'] == 'Subscription is healthy'

    def test_validate_old_subscription(self, client, test_user):
        """Test validation of old subscription (>90 days)"""
        sub = PushSubscription.objects.create(
            user=test_user,
            endpoint='https://test.endpoint.com/123',
            p256dh_key='test_p256dh',
            auth_key='test_auth',
            enabled=True,
            failure_count=0
        )
        # Manually set created_at to 100 days ago
        sub.created_at = timezone.now() - timezone.timedelta(days=100)
        sub.save()

        client.force_login(test_user)

        response = client.post('/api/push/validate-subscription/', {
            'endpoint': 'https://test.endpoint.com/123'
        }, content_type='application/json')

        assert response.status_code == 200
        data = response.json()
        assert data['success'] is True
        assert data['valid'] is True
        assert data['warning'] == 'old_subscription'
        assert data['age_days'] >= 100

    def test_validate_failing_subscription(self, client, test_user):
        """Test validation of subscription with high failure count"""
        sub = PushSubscription.objects.create(
            user=test_user,
            endpoint='https://test.endpoint.com/123',
            p256dh_key='test_p256dh',
            auth_key='test_auth',
            enabled=True,
            failure_count=4
        )

        client.force_login(test_user)

        response = client.post('/api/push/validate-subscription/', {
            'endpoint': 'https://test.endpoint.com/123'
        }, content_type='application/json')

        assert response.status_code == 200
        data = response.json()
        assert data['success'] is True
        assert data['valid'] is False
        assert data['reason'] == 'high_failure_count'

    def test_validate_nonexistent_subscription(self, client, test_user):
        """Test validation of subscription that doesn't exist"""
        client.force_login(test_user)

        response = client.post('/api/push/validate-subscription/', {
            'endpoint': 'https://nonexistent.endpoint.com/999'
        }, content_type='application/json')

        assert response.status_code == 200
        data = response.json()
        assert data['success'] is True
        assert data['valid'] is False
        assert data['reason'] == 'not_found'

    def test_validate_disabled_subscription(self, client, test_user):
        """Test validation of disabled subscription"""
        sub = PushSubscription.objects.create(
            user=test_user,
            endpoint='https://test.endpoint.com/123',
            p256dh_key='test_p256dh',
            auth_key='test_auth',
            enabled=False
        )

        client.force_login(test_user)

        response = client.post('/api/push/validate-subscription/', {
            'endpoint': 'https://test.endpoint.com/123'
        }, content_type='application/json')

        assert response.status_code == 200
        data = response.json()
        assert data['success'] is True
        assert data['valid'] is False
        assert data['reason'] == 'not_found'  # Disabled is treated as not found

    def test_validate_subscription_missing_endpoint(self, client, test_user):
        """Test validation without providing endpoint"""
        client.force_login(test_user)

        response = client.post('/api/push/validate-subscription/', {
        }, content_type='application/json')

        assert response.status_code == 400
        data = response.json()
        assert data['success'] is False
        assert 'endpoint' in data['message'].lower()

    def test_validate_subscription_requires_auth(self, client):
        """Test that endpoint requires authentication"""
        response = client.post('/api/push/validate-subscription/', {
            'endpoint': 'https://test.endpoint.com/123'
        }, content_type='application/json')

        # Should redirect to login (302) or return 403
        assert response.status_code in [302, 403]
