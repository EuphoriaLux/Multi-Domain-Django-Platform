"""
Crush.lu Admin Package

This package contains the admin configuration for the Crush.lu Coach Panel,
split into logical modules for better organization and maintainability.

Modules:
- filters.py: Custom admin filters (ReviewTimeFilter, etc.)
- site.py: CrushLuAdminSite custom admin site class
- special.py: SpecialUserExperienceAdmin
- profiles.py: CrushProfile, CrushCoach, ProfileSubmission admins
- events.py: MeetupEvent, EventRegistration, EventInvitation admins
- connections.py: EventConnection, ConnectionMessage admins
- activities.py: Activity voting and presentation admins
- journey.py: Journey system admins
- advent.py: Advent calendar system admins
- notifications.py: Push notification admins
- preferences.py: User activity and email preference admins
- users.py: User admin classes
- referrals.py: Referral code admins
- wallet.py: Wallet pass management (Apple & Google)
- pwa_devices.py: PWA device installation tracking (admin-only)
- oauth.py: OAuth state debugging (for Android PWA issues)
- passkit.py: Apple PassKit device registration tracking
"""

from django.contrib import admin

# Import the custom admin site
from .site import crush_admin_site

# Import all admin classes for re-export
from .filters import (
    ReviewTimeFilter,
    CoachAssignmentFilter,
    SubmissionWorkflowFilter,
    DaysSinceSignupFilter,
    DaysPendingApprovalFilter,
    ProfileCompletenessFilter,
    EventParticipationFilter,
)

from .special import SpecialUserExperienceAdmin

from .profiles import (
    CrushCoachAdmin,
    ProfileSubmissionProfileInline,
    CrushProfileAdmin,
    ProfileSubmissionAdmin,
    CoachSessionAdmin,
    ApprovedProfile,
    ApprovedProfileAdmin,
    AwaitingReviewProfile,
    AwaitingReviewProfileAdmin,
    IncompleteProfile,
    IncompleteProfileAdmin,
    CompletedSubmission,
    CompletedSubmissionAdmin,
    InProcessSubmission,
    InProcessSubmissionAdmin,
    PendingReviewProfile,
    PendingReviewProfileAdmin,
    RevisionNeededProfile,
    RevisionNeededProfileAdmin,
    RecontactCoachProfile,
    RecontactCoachProfileAdmin,
    RejectedProfile,
    RejectedProfileAdmin,
)

from .events import (
    EventRegistrationInline,
    EventInvitationInline,
    EventVotingSessionInline,
    PresentationQueueInline,
    SpeedDatingPairInline,
    MeetupEventAdmin,
    EventRegistrationAdmin,
    EventInvitationAdmin,
)

from .connections import (
    EventConnectionAdmin,
    ConnectionMessageAdmin,
)

from .activities import (
    EventActivityOptionInline,
    GlobalActivityOptionAdmin,
    EventActivityOptionAdmin,
    EventActivityVoteAdmin,
    EventVotingSessionAdmin,
    PresentationQueueAdmin,
    PresentationRatingAdmin,
    SpeedDatingPairAdmin,
)

from .journey import (
    JourneyChapterInline,
    JourneyChallengeInline,
    JourneyRewardInline,
    ChapterProgressInline,
    JourneyConfigurationAdmin,
    JourneyChapterAdmin,
    JourneyChallengeAdmin,
    JourneyRewardAdmin,
    JourneyProgressAdmin,
    ChapterProgressAdmin,
    ChallengeAttemptAdmin,
    RewardProgressAdmin,
    JourneyGiftAdmin,
)

from .advent import (
    AdventDoorContentInline,
    AdventDoorInline,
    QRCodeTokenInline,
    AdventCalendarAdmin,
    AdventDoorAdmin,
    AdventDoorContentAdmin,
    AdventProgressAdmin,
    QRCodeTokenAdmin,
)

from .notifications import (
    PushSubscriptionAdmin,
    CoachPushSubscriptionAdmin,
)

from .preferences import (
    UserActivityAdmin,
    EmailPreferenceAdmin,
    ProfileReminderAdmin,
)

from .newsletter import (
    NewsletterAdmin,
    NewsletterRecipientAdmin,
)

from .users import (
    CrushProfileUserInline,
    CrushCoachUserInline,
    CrushUserAdmin,
)

from .crush_spark import CrushSparkAdmin

from .referrals import (
    ReferralCodeAdmin,
    ReferralAttributionAdmin,
)

from .wallet import (
    WalletPassFilter,
    WalletPassAdmin,
    WalletPassProxy,
)

from .pwa_devices import PWADeviceInstallationAdmin

from .oauth import OAuthStateAdmin

from .passkit import PasskitDeviceRegistrationAdmin

from .site_config import CrushSiteConfigAdmin

# Import all models for registration
from crush_lu.models import (
    SpecialUserExperience,
    CrushCoach,
    CrushProfile,
    CrushSpark,
    ProfileSubmission,
    CoachSession,
    MeetupEvent,
    EventRegistration,
    EventInvitation,
    EventConnection,
    ConnectionMessage,
    GlobalActivityOption,
    EventActivityOption,
    EventActivityVote,
    EventVotingSession,
    PresentationQueue,
    PresentationRating,
    SpeedDatingPair,
    JourneyConfiguration,
    JourneyChapter,
    JourneyChallenge,
    JourneyReward,
    JourneyProgress,
    ChapterProgress,
    ChallengeAttempt,
    RewardProgress,
    JourneyGift,
    AdventCalendar,
    AdventDoor,
    AdventDoorContent,
    AdventProgress,
    QRCodeToken,
    PushSubscription,
    CoachPushSubscription,
    UserActivity,
    EmailPreference,
    ProfileReminder,
    ReferralCode,
    ReferralAttribution,
    PWADeviceInstallation,
    OAuthState,
    PasskitDeviceRegistration,
    CrushSiteConfig,
    Newsletter,
    NewsletterRecipient,
)


# ============================================================================
# REGISTER ALL MODELS WITH CUSTOM ADMIN SITE
# ============================================================================

# Special User Experience
crush_admin_site.register(SpecialUserExperience, SpecialUserExperienceAdmin)

# Profile System
crush_admin_site.register(CrushCoach, CrushCoachAdmin)
crush_admin_site.register(CrushProfile, CrushProfileAdmin)
crush_admin_site.register(ApprovedProfile, ApprovedProfileAdmin)
crush_admin_site.register(AwaitingReviewProfile, AwaitingReviewProfileAdmin)
crush_admin_site.register(IncompleteProfile, IncompleteProfileAdmin)
crush_admin_site.register(PendingReviewProfile, PendingReviewProfileAdmin)
crush_admin_site.register(RevisionNeededProfile, RevisionNeededProfileAdmin)
crush_admin_site.register(RecontactCoachProfile, RecontactCoachProfileAdmin)
crush_admin_site.register(RejectedProfile, RejectedProfileAdmin)
crush_admin_site.register(ProfileSubmission, ProfileSubmissionAdmin)
crush_admin_site.register(CompletedSubmission, CompletedSubmissionAdmin)
crush_admin_site.register(InProcessSubmission, InProcessSubmissionAdmin)
crush_admin_site.register(CoachSession, CoachSessionAdmin)

# Event System
crush_admin_site.register(MeetupEvent, MeetupEventAdmin)
crush_admin_site.register(EventRegistration, EventRegistrationAdmin)
crush_admin_site.register(EventInvitation, EventInvitationAdmin)

# Crush Spark System
crush_admin_site.register(CrushSpark, CrushSparkAdmin)

# Connection System
crush_admin_site.register(EventConnection, EventConnectionAdmin)
crush_admin_site.register(ConnectionMessage, ConnectionMessageAdmin)

# Activity Voting System
crush_admin_site.register(GlobalActivityOption, GlobalActivityOptionAdmin)
crush_admin_site.register(EventActivityOption, EventActivityOptionAdmin)
crush_admin_site.register(EventActivityVote, EventActivityVoteAdmin)
crush_admin_site.register(EventVotingSession, EventVotingSessionAdmin)
crush_admin_site.register(PresentationQueue, PresentationQueueAdmin)
crush_admin_site.register(PresentationRating, PresentationRatingAdmin)
crush_admin_site.register(SpeedDatingPair, SpeedDatingPairAdmin)

# Journey System
crush_admin_site.register(JourneyConfiguration, JourneyConfigurationAdmin)
crush_admin_site.register(JourneyChapter, JourneyChapterAdmin)
crush_admin_site.register(JourneyChallenge, JourneyChallengeAdmin)
crush_admin_site.register(JourneyReward, JourneyRewardAdmin)
crush_admin_site.register(JourneyProgress, JourneyProgressAdmin)
crush_admin_site.register(ChapterProgress, ChapterProgressAdmin)
crush_admin_site.register(ChallengeAttempt, ChallengeAttemptAdmin)
crush_admin_site.register(RewardProgress, RewardProgressAdmin)
crush_admin_site.register(JourneyGift, JourneyGiftAdmin)

# Advent Calendar System
crush_admin_site.register(AdventCalendar, AdventCalendarAdmin)
crush_admin_site.register(AdventDoor, AdventDoorAdmin)
crush_admin_site.register(AdventDoorContent, AdventDoorContentAdmin)
crush_admin_site.register(AdventProgress, AdventProgressAdmin)
crush_admin_site.register(QRCodeToken, QRCodeTokenAdmin)

# Push Notifications (registered with @admin.register decorator in notifications.py,
# but we need to register them here since we're not using the decorator pattern)
crush_admin_site.register(PushSubscription, PushSubscriptionAdmin)
crush_admin_site.register(CoachPushSubscription, CoachPushSubscriptionAdmin)

# User Activity and Preferences
crush_admin_site.register(UserActivity, UserActivityAdmin)
crush_admin_site.register(EmailPreference, EmailPreferenceAdmin)
crush_admin_site.register(ProfileReminder, ProfileReminderAdmin)

# Referral System
crush_admin_site.register(ReferralCode, ReferralCodeAdmin)
crush_admin_site.register(ReferralAttribution, ReferralAttributionAdmin)

# Wallet Pass Management
crush_admin_site.register(WalletPassProxy, WalletPassAdmin)

# PWA Device Installation Tracking (Admin-only analytics)
crush_admin_site.register(PWADeviceInstallation, PWADeviceInstallationAdmin)

# OAuth State (Debugging cross-browser auth issues)
crush_admin_site.register(OAuthState, OAuthStateAdmin)

# PassKit Device Registration (Apple Wallet device tracking)
crush_admin_site.register(PasskitDeviceRegistration, PasskitDeviceRegistrationAdmin)

# Newsletter System
crush_admin_site.register(Newsletter, NewsletterAdmin)
crush_admin_site.register(NewsletterRecipient, NewsletterRecipientAdmin)

# Site Configuration (singleton)
crush_admin_site.register(CrushSiteConfig, CrushSiteConfigAdmin)

# Register User model with crush_admin_site for proper navigation
# This allows coaches to navigate to User records while staying within /crush-admin/
from django.contrib.auth.models import User
crush_admin_site.register(User, CrushUserAdmin)


# ============================================================================
# EXPORTS
# ============================================================================

__all__ = [
    # Admin site
    'crush_admin_site',

    # Filters
    'ReviewTimeFilter',
    'CoachAssignmentFilter',
    'SubmissionWorkflowFilter',
    'DaysSinceSignupFilter',
    'DaysPendingApprovalFilter',
    'ProfileCompletenessFilter',
    'EventParticipationFilter',

    # Special
    'SpecialUserExperienceAdmin',

    # Profiles
    'CrushCoachAdmin',
    'ProfileSubmissionProfileInline',
    'CrushProfileAdmin',
    'ProfileSubmissionAdmin',
    'CoachSessionAdmin',
    'ApprovedProfile',
    'ApprovedProfileAdmin',
    'AwaitingReviewProfile',
    'AwaitingReviewProfileAdmin',
    'IncompleteProfile',
    'IncompleteProfileAdmin',
    'CompletedSubmission',
    'CompletedSubmissionAdmin',
    'InProcessSubmission',
    'InProcessSubmissionAdmin',
    'PendingReviewProfile',
    'PendingReviewProfileAdmin',
    'RevisionNeededProfile',
    'RevisionNeededProfileAdmin',
    'RecontactCoachProfile',
    'RecontactCoachProfileAdmin',
    'RejectedProfile',
    'RejectedProfileAdmin',

    # Events
    'EventRegistrationInline',
    'EventInvitationInline',
    'EventVotingSessionInline',
    'PresentationQueueInline',
    'SpeedDatingPairInline',
    'MeetupEventAdmin',
    'EventRegistrationAdmin',
    'EventInvitationAdmin',

    # Crush Spark
    'CrushSparkAdmin',

    # Connections
    'EventConnectionAdmin',
    'ConnectionMessageAdmin',

    # Activities
    'EventActivityOptionInline',
    'GlobalActivityOptionAdmin',
    'EventActivityOptionAdmin',
    'EventActivityVoteAdmin',
    'EventVotingSessionAdmin',
    'PresentationQueueAdmin',
    'PresentationRatingAdmin',
    'SpeedDatingPairAdmin',

    # Journey
    'JourneyChapterInline',
    'JourneyChallengeInline',
    'JourneyRewardInline',
    'ChapterProgressInline',
    'JourneyConfigurationAdmin',
    'JourneyChapterAdmin',
    'JourneyChallengeAdmin',
    'JourneyRewardAdmin',
    'JourneyProgressAdmin',
    'ChapterProgressAdmin',
    'ChallengeAttemptAdmin',
    'RewardProgressAdmin',
    'JourneyGiftAdmin',

    # Advent
    'AdventDoorContentInline',
    'AdventDoorInline',
    'QRCodeTokenInline',
    'AdventCalendarAdmin',
    'AdventDoorAdmin',
    'AdventDoorContentAdmin',
    'AdventProgressAdmin',
    'QRCodeTokenAdmin',

    # Notifications
    'PushSubscriptionAdmin',
    'CoachPushSubscriptionAdmin',

    # Preferences
    'UserActivityAdmin',
    'EmailPreferenceAdmin',
    'ProfileReminderAdmin',

    # Referrals
    'ReferralCodeAdmin',
    'ReferralAttributionAdmin',

    # Users
    'CrushProfileUserInline',
    'CrushCoachUserInline',
    'CrushUserAdmin',

    # Wallet
    'WalletPassFilter',
    'WalletPassAdmin',
    'WalletPassProxy',

    # PWA Devices
    'PWADeviceInstallationAdmin',

    # OAuth State (debugging)
    'OAuthStateAdmin',

    # PassKit Device Registration
    'PasskitDeviceRegistrationAdmin',

    # Newsletter
    'NewsletterAdmin',
    'NewsletterRecipientAdmin',

    # Site Configuration
    'CrushSiteConfigAdmin',
]
