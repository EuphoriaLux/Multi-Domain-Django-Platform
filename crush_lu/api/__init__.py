"""
API package for Crush.lu

REST API endpoints organized by domain.
All API views are re-exported here for backward compatibility.
"""

# General API views
from .views import voting_status_api, submit_vote_api, voting_results_api

# Push notifications
from .push import (
    get_vapid_public_key, subscribe_push, refresh_subscription,
    validate_subscription, unsubscribe_push, delete_push_subscription,
    list_subscriptions, update_subscription_preferences,
    send_test_push, mark_pwa_user, get_pwa_status,
    run_subscription_health_check,
)

# Coach push notifications
from .coach_push import (
    get_vapid_public_key as coach_get_vapid_public_key,
    subscribe_push as coach_subscribe_push,
    unsubscribe_push as coach_unsubscribe_push,
    delete_push_subscription as coach_delete_push_subscription,
    list_subscriptions as coach_list_subscriptions,
    update_subscription_preferences as coach_update_subscription_preferences,
    send_test_push as coach_send_test_push,
)

# Journey API
from .journey import (
    submit_challenge, unlock_hint, get_progress, save_state,
    record_final_response, unlock_puzzle_piece, get_reward_progress,
)

# PWA device registration
from .pwa import register_pwa_installation

# Referral API
from .referral import referral_me, redeem_points

# Admin sync API
from .admin_sync import (
    sync_contacts_endpoint, delete_all_contacts_endpoint, sync_contacts_health,
)

__all__ = [
    # General API
    'voting_status_api', 'submit_vote_api', 'voting_results_api',
    # Push notifications
    'get_vapid_public_key', 'subscribe_push', 'refresh_subscription',
    'validate_subscription', 'unsubscribe_push', 'delete_push_subscription',
    'list_subscriptions', 'update_subscription_preferences',
    'send_test_push', 'mark_pwa_user', 'get_pwa_status',
    'run_subscription_health_check',
    # Coach push (prefixed)
    'coach_get_vapid_public_key', 'coach_subscribe_push',
    'coach_unsubscribe_push', 'coach_delete_push_subscription',
    'coach_list_subscriptions', 'coach_update_subscription_preferences',
    'coach_send_test_push',
    # Journey
    'submit_challenge', 'unlock_hint', 'get_progress', 'save_state',
    'record_final_response', 'unlock_puzzle_piece', 'get_reward_progress',
    # PWA
    'register_pwa_installation',
    # Referral
    'referral_me', 'redeem_points',
    # Admin sync
    'sync_contacts_endpoint', 'delete_all_contacts_endpoint', 'sync_contacts_health',
]
