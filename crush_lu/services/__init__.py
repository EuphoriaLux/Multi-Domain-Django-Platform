# crush_lu/services/__init__.py
"""
Services package for Crush.lu business logic.

Contains external integrations and service classes.
"""

from .blocking import (
    block_exists_subquery,
    blocked_user_ids,
    cancel_legacy_sparks,
    decline_active_sparks,
    is_blocked_pair,
    purge_user_from_connect_queues,
    terminate_active_connections,
    withdraw_active_coach_picks,
)
from .credits import (
    available_credit_cents,
    credit_paid_registrations_for_cancelled_event,
    issue_cancellation_credit,
    issue_credit,
    redeem_for_registration,
    settle_pending_resale_credit,
    void_credit,
)
from .crush_connect import get_eligible_pool, get_or_create_daily_drop
from .crush_leads import call_by, coach_action_queue, reminder_due
from .echo_lu import (
    EchoLuClient,
    EchoLuError,
    build_experience_payload,
    events_needing_sync,
    should_publish,
    sync_event,
    withdraw_event,
)
from .echo_lu import is_sync_enabled as is_echo_sync_enabled
from .graph_contacts import GraphContactsService, is_sync_enabled

__all__ = [
    'GraphContactsService',
    'is_sync_enabled',
    'EchoLuClient',
    'EchoLuError',
    'build_experience_payload',
    'events_needing_sync',
    'is_echo_sync_enabled',
    'should_publish',
    'sync_event',
    'withdraw_event',
    'get_eligible_pool',
    'get_or_create_daily_drop',
    'call_by',
    'coach_action_queue',
    'reminder_due',
    'block_exists_subquery',
    'blocked_user_ids',
    'is_blocked_pair',
    'terminate_active_connections',
    'withdraw_active_coach_picks',
    'decline_active_sparks',
    'purge_user_from_connect_queues',
    'cancel_legacy_sparks',
    # Crush Credit
    'available_credit_cents',
    'issue_credit',
    'issue_cancellation_credit',
    'settle_pending_resale_credit',
    'void_credit',
    'credit_paid_registrations_for_cancelled_event',
    'redeem_for_registration',
]
