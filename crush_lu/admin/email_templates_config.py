# crush_lu/admin/email_templates_config.py
"""
Email Template Manager Configuration

Metadata for all 23 Crush.lu email templates, organized by category.
Used by the Email Template Manager admin page for preview and sending.
"""

# Email template categories with display info
EMAIL_CATEGORIES = {
    'profile': {
        'name': 'Profile & Onboarding',
        'icon': 'user-circle',
        'color': 'purple',
        'description': 'Emails for user registration, profile creation, and approval workflow',
    },
    'events': {
        'name': 'Events & Registrations',
        'icon': 'calendar',
        'color': 'pink',
        'description': 'Event registration confirmations, reminders, and waitlist notifications',
    },
    'connections': {
        'name': 'Connections & Messages',
        'icon': 'heart',
        'color': 'blue',
        'description': 'Post-event connection requests, acceptances, and messaging',
    },
    'invitations': {
        'name': 'Private Invitations',
        'icon': 'envelope-open',
        'color': 'orange',
        'description': 'Private event invitations and approval notifications',
    },
    'journey': {
        'name': 'Journey & Gifts',
        'icon': 'gift',
        'color': 'green',
        'description': 'Wonderland journey gift notifications and delivery',
    },
}


# Email template metadata
# Key: template identifier (used in URL and code)
# Value: metadata dict with template path, context requirements, etc.
EMAIL_TEMPLATE_METADATA = {
    # =========================================================================
    # PROFILE & ONBOARDING CATEGORY (10 templates)
    # =========================================================================
    'welcome': {
        'name': 'Welcome Email',
        'category': 'profile',
        'template': 'crush_lu/emails/welcome.html',
        'subject': 'Welcome to Crush.lu! 🎉 Complete Your Profile',
        'description': 'Sent immediately after user signup to guide them to profile creation',
        'required_context': ['user'],
        'optional_context': [],
        'context_builder': 'build_welcome_context',
    },
    'profile_submission_confirmation': {
        'name': 'Profile Submitted',
        'category': 'profile',
        'template': 'crush_lu/emails/profile_submission_confirmation.html',
        'subject': 'Profile Submitted for Review - Crush.lu',
        'description': 'Sent after user completes and submits their profile for coach review',
        'required_context': ['user'],
        'optional_context': ['profile'],
        'context_builder': 'build_profile_submission_context',
    },
    'coach_assignment': {
        'name': 'Coach Assignment',
        'category': 'profile',
        'template': 'crush_lu/emails/coach_assignment.html',
        'subject': 'New Profile Review Assignment',
        'description': 'Sent to coach when a new profile is assigned for review',
        'required_context': ['user', 'submission'],
        'optional_context': [],
        'context_builder': 'build_coach_assignment_context',
        'recipient_type': 'coach',
    },
    'profile_approved': {
        'name': 'Profile Approved',
        'category': 'profile',
        'template': 'crush_lu/emails/profile_approved.html',
        'subject': 'Welcome to Crush.lu - Your Profile is Approved! 🎉',
        'description': 'Sent when coach approves user profile',
        'required_context': ['user', 'profile'],
        'optional_context': ['coach_notes'],
        'context_builder': 'build_profile_approved_context',
    },
    'profile_revision_request': {
        'name': 'Profile Revision Request',
        'category': 'profile',
        'template': 'crush_lu/emails/profile_revision_request.html',
        'subject': 'Profile Review Feedback - Crush.lu',
        'description': 'Sent when coach requests profile changes from user',
        'required_context': ['user', 'profile'],
        'optional_context': ['feedback'],
        'context_builder': 'build_profile_revision_context',
    },
    'profile_rejected': {
        'name': 'Profile Rejected',
        'category': 'profile',
        'template': 'crush_lu/emails/profile_rejected.html',
        'subject': 'Profile Review Update - Crush.lu',
        'description': 'Sent when coach rejects user profile',
        'required_context': ['user', 'profile'],
        'optional_context': ['reason'],
        'context_builder': 'build_profile_rejected_context',
    },
    'profile_incomplete_24h': {
        'name': '24h Reminder',
        'category': 'profile',
        'template': 'crush_lu/emails/profile_incomplete_24h.html',
        'subject': 'Complete Your Profile - Crush.lu',
        'description': 'First reminder sent 24 hours after signup if profile is incomplete',
        'required_context': ['user'],
        'optional_context': ['profile'],
        'context_builder': 'build_profile_reminder_context',
    },
    'profile_incomplete_72h': {
        'name': '72h Reminder',
        'category': 'profile',
        'template': 'crush_lu/emails/profile_incomplete_72h.html',
        'subject': "Don't Miss Out - Your Profile is Waiting",
        'description': 'Second reminder sent 72 hours after signup if profile is incomplete',
        'required_context': ['user'],
        'optional_context': ['profile'],
        'context_builder': 'build_profile_reminder_context',
    },
    'profile_incomplete_final': {
        'name': 'Final Reminder (7d)',
        'category': 'profile',
        'template': 'crush_lu/emails/profile_incomplete_final.html',
        'subject': 'Last Chance to Complete Your Profile - Crush.lu',
        'description': 'Final reminder sent 7 days after signup if profile is incomplete',
        'required_context': ['user'],
        'optional_context': ['profile'],
        'context_builder': 'build_profile_reminder_context',
    },
    'profile_recontact': {
        'name': 'Recontact After Missed Call',
        'category': 'profile',
        'template': 'crush_lu/emails/profile_recontact.html',
        'subject': 'Your Crush Coach Needs to Speak With You',
        'description': 'Sent when coach attempts to reach user for screening call but cannot connect',
        'required_context': ['user', 'coach'],
        'optional_context': [],
        'context_builder': 'build_profile_recontact_context',
    },

    # =========================================================================
    # EVENTS & REGISTRATIONS CATEGORY (4 templates)
    # =========================================================================
    'event_registration_confirmation': {
        'name': 'Registration Confirmed',
        'category': 'events',
        'template': 'crush_lu/emails/event_registration_confirmation.html',
        'subject': 'Event Registration Confirmed',
        'description': 'Sent when user successfully registers for an event',
        'required_context': ['user', 'event', 'registration'],
        'optional_context': [],
        'context_builder': 'build_event_registration_context',
    },
    'event_reminder': {
        'name': 'Event Reminder',
        'category': 'events',
        'template': 'crush_lu/emails/event_reminder.html',
        'subject': 'Event Reminder',
        'description': 'Reminder email sent before scheduled events',
        'required_context': ['user', 'event', 'registration'],
        'optional_context': ['days_until_event'],
        'context_builder': 'build_event_reminder_context',
    },
    'event_waitlist': {
        'name': 'Added to Waitlist',
        'category': 'events',
        'template': 'crush_lu/emails/event_waitlist.html',
        'subject': 'Added to Waitlist',
        'description': 'Sent when user is added to event waitlist (event at capacity)',
        'required_context': ['user', 'event', 'registration'],
        'optional_context': [],
        'context_builder': 'build_event_waitlist_context',
    },
    'event_cancellation': {
        'name': 'Cancellation Confirmed',
        'category': 'events',
        'template': 'crush_lu/emails/event_cancellation.html',
        'subject': 'Event Cancellation Confirmed',
        'description': 'Sent when user cancels their event registration',
        'required_context': ['user', 'event'],
        'optional_context': [],
        'context_builder': 'build_event_cancellation_context',
    },

    # =========================================================================
    # CONNECTIONS & MESSAGES CATEGORY (3 templates)
    # =========================================================================
    'new_connection_request': {
        'name': 'Connection Request',
        'category': 'connections',
        'template': 'crush_lu/emails/new_connection_request.html',
        'subject': 'Someone wants to connect with you! 💕',
        'description': 'Sent when another user requests to connect after an event',
        'required_context': ['user', 'connection'],
        'optional_context': [],
        'context_builder': 'build_connection_request_context',
    },
    'connection_accepted': {
        'name': 'Connection Accepted',
        'category': 'connections',
        'template': 'crush_lu/emails/connection_accepted.html',
        'subject': 'Your connection request was accepted! 🎉',
        'description': 'Sent when your connection request is accepted',
        'required_context': ['user', 'connection'],
        'optional_context': [],
        'context_builder': 'build_connection_accepted_context',
    },
    'new_message': {
        'name': 'New Message',
        'category': 'connections',
        'template': 'crush_lu/emails/new_message.html',
        'subject': 'New message',
        'description': 'Sent when user receives a new message from a connection',
        'required_context': ['user', 'message'],
        'optional_context': [],
        'context_builder': 'build_new_message_context',
    },

    # =========================================================================
    # PRIVATE INVITATIONS CATEGORY (4 templates)
    # =========================================================================
    'existing_user_invitation': {
        'name': 'Invitation (Existing User)',
        'category': 'invitations',
        'template': 'crush_lu/emails/existing_user_invitation.html',
        'subject': "🎉 You're Invited!",
        'description': 'Sent to existing users when invited to a private event',
        'required_context': ['user', 'event'],
        'optional_context': [],
        'context_builder': 'build_existing_user_invitation_context',
    },
    'external_guest_invitation': {
        'name': 'Invitation (External Guest)',
        'category': 'invitations',
        'template': 'crush_lu/emails/external_guest_invitation.html',
        'subject': "💌 You're Invited to Crush.lu",
        'description': 'Sent to non-users when invited to create an account and join a private event',
        'required_context': ['invitation'],
        'optional_context': [],
        'context_builder': 'build_external_guest_invitation_context',
    },
    'invitation_approved': {
        'name': 'Invitation Approved',
        'category': 'invitations',
        'template': 'crush_lu/emails/invitation_approved.html',
        'subject': '✅ Your Invitation Has Been Approved!',
        'description': 'Sent when external guest invitation is approved by coach',
        'required_context': ['user', 'invitation'],
        'optional_context': [],
        'context_builder': 'build_invitation_approved_context',
    },
    'invitation_rejected': {
        'name': 'Invitation Rejected',
        'category': 'invitations',
        'template': 'crush_lu/emails/invitation_rejected.html',
        'subject': 'Update on Your Invitation',
        'description': 'Sent when external guest invitation is rejected by coach',
        'required_context': ['user', 'invitation'],
        'optional_context': ['feedback'],
        'context_builder': 'build_invitation_rejected_context',
    },

    # =========================================================================
    # JOURNEY & GIFTS CATEGORY (1 template)
    # =========================================================================
    'journey_gift_notification': {
        'name': 'Journey Gift Notification',
        'category': 'journey',
        'template': 'crush_lu/emails/journey_gift_notification.html',
        'subject': 'A Magical Gift Awaits',
        'description': 'Sent to gift recipient when someone creates a Wonderland journey for them',
        'required_context': ['gift'],
        'optional_context': [],
        'context_builder': 'build_journey_gift_context',
    },
}


def get_templates_by_category():
    """
    Group templates by category for display.

    Returns:
        dict: Category key -> list of template metadata dicts
    """
    result = {cat_key: [] for cat_key in EMAIL_CATEGORIES.keys()}

    for template_key, template_meta in EMAIL_TEMPLATE_METADATA.items():
        category = template_meta.get('category')
        if category in result:
            result[category].append({
                'key': template_key,
                **template_meta
            })

    return result


def get_template_by_key(key):
    """
    Get template metadata by its key.

    Args:
        key: Template identifier (e.g., 'welcome', 'profile_approved')

    Returns:
        dict or None: Template metadata if found
    """
    return EMAIL_TEMPLATE_METADATA.get(key)


def get_category_info(category_key):
    """
    Get category display information.

    Args:
        category_key: Category identifier (e.g., 'profile', 'events')

    Returns:
        dict or None: Category info if found
    """
    return EMAIL_CATEGORIES.get(category_key)
