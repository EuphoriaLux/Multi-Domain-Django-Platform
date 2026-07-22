"""
Custom admin site configuration for Crush.lu Coach Panel.

Provides a customized admin interface with:
- Coach-only access control
- Grouped model organization
- Custom dashboard integration
"""

from django.contrib import admin
from django.core.exceptions import ObjectDoesNotExist
from django.urls import reverse


class CrushLuAdminSite(admin.AdminSite):
    site_header = '💕 Crush.lu Coach Panel'
    site_title = 'Crush.lu Coach Panel'
    index_title = 'Welcome to Crush.lu Coach Management'

    # Use custom index template with Quick Links and sidebar
    index_template = 'admin/crush_lu/index.html'

    def has_permission(self, request):
        """
        Custom permission check: Only Crush coaches can access this admin panel.
        Superusers can always access.

        Note: We override the default is_staff check to allow coaches access.
        """
        # Superusers always have access
        if request.user.is_superuser:
            return True

        # Check if user is authenticated
        if not request.user.is_authenticated:
            return False

        # Check if user is an active Crush coach
        try:
            coach = request.user.crushcoach
            # Grant access to active coaches even if they're not staff
            if coach.is_active:
                return True
        except (AttributeError, ObjectDoesNotExist):
            pass

        # Fallback to default staff check
        return request.user.is_active and request.user.is_staff

    def has_module_perms(self, request, app_label):
        """
        Allow coaches to see all Crush.lu models.
        """
        if not self.has_permission(request):
            return False

        # Coaches can see crush_lu app
        if app_label == 'crush_lu':
            return True

        # Superusers can see everything
        if request.user.is_superuser:
            return True

        # Default Django permission check for other apps
        return super().has_module_perms(request, app_label)

    def index(self, request, extra_context=None):
        """
        Override index to add custom dashboard link and analytics.
        """
        from django.db.models import Count
        from django.utils import timezone
        from datetime import timedelta
        from ..models import (
            CrushProfile, ProfileSubmission, MeetupEvent,
            EventConnection
        )

        extra_context = extra_context or {}
        extra_context['show_dashboard_link'] = True
        extra_context['dashboard_url'] = reverse('crush_admin_dashboard')

        # Add coach information to context
        try:
            coach = request.user.crushcoach
            extra_context['is_coach'] = True
            extra_context['coach_name'] = request.user.get_full_name() or request.user.username
        except (AttributeError, ObjectDoesNotExist):
            extra_context['is_coach'] = False

        # Quick stats for index page
        extra_context['total_profiles'] = CrushProfile.objects.count()
        extra_context['approved_profiles'] = CrushProfile.objects.filter(verification_status="verified").count()
        extra_context['mutual_connections'] = EventConnection.objects.filter(status='mutual').count()

        # Upcoming events count
        now = timezone.now()
        current_events = [
            event
            for event in MeetupEvent.objects.filter(
                date_time__gte=MeetupEvent.live_lookback_cutoff(now),
                is_published=True,
                is_cancelled=False,
            ).annotate(
                registration_count=Count('eventregistration')
            ).order_by('date_time')
            if event.end_time >= now
        ]
        extra_context['upcoming_events'] = len(current_events)

        # Pending actions for Action Center
        cutoff_24h = now - timedelta(hours=24)
        pending_reviews = ProfileSubmission.objects.filter(status='pending').count()
        urgent_reviews = ProfileSubmission.objects.filter(
            status='pending',
            submitted_at__lt=cutoff_24h
        ).count()
        awaiting_call = ProfileSubmission.objects.filter(
            status='pending',
            coach__isnull=False,
            review_call_completed=False
        ).count()
        ready_to_approve = ProfileSubmission.objects.filter(
            status='pending',
            coach__isnull=False,
            review_call_completed=True
        ).count()
        unassigned = ProfileSubmission.objects.filter(
            status='pending',
            coach__isnull=True
        ).count()

        extra_context['pending_actions'] = {
            'total_pending': pending_reviews,
            'urgent_reviews': urgent_reviews,
            'awaiting_call': awaiting_call,
            'ready_to_approve': ready_to_approve,
            'unassigned': unassigned,
        }

        # Recent submissions for Today's Focus
        extra_context['recent_submissions'] = ProfileSubmission.objects.filter(
            status='pending'
        ).select_related('profile__user', 'coach__user').order_by('-submitted_at')[:5]

        # Upcoming events list for Today's Focus
        extra_context['upcoming_events_list'] = current_events[:5]

        return super().index(request, extra_context)

    def get_app_list(self, request, app_label=None):
        """
        Override to customize the admin index page grouping.
        Groups models into logical categories for better organization.
        """
        app_list = super().get_app_list(request, app_label)

        # The grouping below splits the single crush_lu app into many sidebar
        # "apps". That only makes sense for the dashboard index (app_label is
        # None). The per-app index page (Django passes app_label='crush_lu')
        # and its app_index.html template expect the standard single app —
        # they read app_list.0.models for ALL crush_lu models — so return the
        # default untouched there, otherwise only the first group's models show.
        if app_label is not None:
            return app_list

        # Custom ordering and grouping for admin sidebar
        # Each group uses sequential ordering (1, 2, 3...) for internal sorting
        # Icons provide visual identification without cluttering with numbers
        custom_order = {
            # ═══════════════════════════════════════════════════════════════════
            # GROUP: Crush Connect (the LuxID-gated matchmaking product)
            # ═══════════════════════════════════════════════════════════════════
            'crushconnectmembership': {'order': 1, 'icon': '💞', 'group': 'Crush Connect'},  # onboarding
            'connectdailydrop': {'order': 2, 'icon': '🎁', 'group': 'Crush Connect'},
            'curiosityspark': {'order': 3, 'icon': '✨', 'group': 'Crush Connect'},
            'connectcoachpick': {'order': 4, 'icon': '🎯', 'group': 'Crush Connect'},
            'crushconnectwaitlist': {'order': 5, 'icon': '📋', 'group': 'Crush Connect'},
            'interest': {'order': 6, 'icon': '🏷️', 'group': 'Crush Connect'},
            'sparkprompt': {'order': 7, 'icon': '💬', 'group': 'Crush Connect'},
            'connectquestionweek': {'order': 8, 'icon': '📆', 'group': 'Crush Connect'},  # weekly Q&A rotation
            'connectquestion': {'order': 9, 'icon': '❓', 'group': 'Crush Connect'},
            'connectquestionanswer': {'order': 10, 'icon': '💭', 'group': 'Crush Connect'},
            'confirmedencounter': {'order': 11, 'icon': '🤝', 'group': 'Crush Connect'},
            'confirmedencounterremovalrequest': {'order': 12, 'icon': '🚫', 'group': 'Crush Connect'},

            # ═══════════════════════════════════════════════════════════════════
            # GROUP 1: Users & Profiles (Core user management)
            # ═══════════════════════════════════════════════════════════════════
            'user': {'order': 0, 'icon': '🔑', 'group': 'Users & Profiles'},  # Django User accounts
            'crushprofile': {'order': 1, 'icon': '👤', 'group': 'Users & Profiles'},
            'profilesubmission': {'order': 2, 'icon': '📄', 'group': 'Users & Profiles'},
            'crushcoach': {'order': 3, 'icon': '🎓', 'group': 'Users & Profiles'},
            'coachsession': {'order': 4, 'icon': '💬', 'group': 'Users & Profiles'},
            'screeningslot': {'order': 5, 'icon': '📅', 'group': 'Users & Profiles'},
            'premiummembership': {'order': 6, 'icon': '💎', 'group': 'Users & Profiles'},
            'callattempt': {'order': 7, 'icon': '☎️', 'group': 'Users & Profiles'},

            # ═══════════════════════════════════════════════════════════════════
            # GROUP: Profile Segments (saved-filter proxy views of profiles)
            # These proxy admins stay registered (index Action Center deep-links
            # target them); they just live in their own collapsed "More"-tier
            # group instead of padding Users & Profiles with 9 extra rows.
            # See docs/crush-admin-redesign.md.
            # ═══════════════════════════════════════════════════════════════════
            'approvedprofile': {'order': 1, 'icon': '✅', 'group': 'Profile Segments'},
            'awaitingreviewprofile': {'order': 2, 'icon': '📋', 'group': 'Profile Segments'},
            'pendingreviewprofile': {'order': 3, 'icon': '⏳', 'group': 'Profile Segments'},
            'revisionneededprofile': {'order': 4, 'icon': '✏️', 'group': 'Profile Segments'},
            'recontactcoachprofile': {'order': 5, 'icon': '📞', 'group': 'Profile Segments'},
            'rejectedprofile': {'order': 6, 'icon': '❌', 'group': 'Profile Segments'},
            'incompleteprofile': {'order': 7, 'icon': '📝', 'group': 'Profile Segments'},
            'inprocesssubmission': {'order': 8, 'icon': '🔄', 'group': 'Profile Segments'},
            'completedsubmission': {'order': 9, 'icon': '✔️', 'group': 'Profile Segments'},

            # ═══════════════════════════════════════════════════════════════════
            # GROUP 2: Events & Meetups (Event management)
            # ═══════════════════════════════════════════════════════════════════
            'meetupevent': {'order': 1, 'icon': '🎉', 'group': 'Events & Meetups'},
            'eventregistration': {'order': 2, 'icon': '✅', 'group': 'Events & Meetups'},
            'eventinvitation': {'order': 3, 'icon': '💌', 'group': 'Events & Meetups'},
            'presentationqueue': {'order': 5, 'icon': '📋', 'group': 'Events & Meetups'},
            'presentationrating': {'order': 6, 'icon': '⭐', 'group': 'Events & Meetups'},
            'eventpoll': {'order': 7, 'icon': '🗳️', 'group': 'Events & Meetups'},
            'eventpollvote': {'order': 8, 'icon': '📊', 'group': 'Events & Meetups'},
            'eventfeedback': {'order': 9, 'icon': '📝', 'group': 'Events & Meetups'},
            'eventlobbyparticipation': {'order': 10, 'icon': '🚪', 'group': 'Events & Meetups'},  # live event lobby
            'eventmeetsignal': {'order': 11, 'icon': '👋', 'group': 'Events & Meetups'},
            'eventmeetingconfirmation': {'order': 12, 'icon': '✅', 'group': 'Events & Meetups'},

            # ═══════════════════════════════════════════════════════════════════
            # GROUP: Quiz Night (Live quiz event management)
            # ═══════════════════════════════════════════════════════════════════
            'quizevent': {'order': 1, 'icon': '🧠', 'group': 'Quiz Night'},
            'quizround': {'order': 2, 'icon': '🔄', 'group': 'Quiz Night'},
            'quizquestion': {'order': 3, 'icon': '❓', 'group': 'Quiz Night'},
            'quiztable': {'order': 4, 'icon': '🪑', 'group': 'Quiz Night'},
            'tableroundscore': {'order': 5, 'icon': '📊', 'group': 'Quiz Night'},
            'individualscore': {'order': 6, 'icon': '🏅', 'group': 'Quiz Night'},

            # ═══════════════════════════════════════════════════════════════════
            # GROUP 3: Activity Voting (Event activity polls)
            # ═══════════════════════════════════════════════════════════════════
            'globalactivityoption': {'order': 1, 'icon': '🌐', 'group': 'Activity Voting'},
            'eventactivityoption': {'order': 2, 'icon': '🎯', 'group': 'Activity Voting'},
            'eventactivityvote': {'order': 3, 'icon': '🗳️', 'group': 'Activity Voting'},
            'eventvotingsession': {'order': 4, 'icon': '⏱️', 'group': 'Activity Voting'},

            # ═══════════════════════════════════════════════════════════════════
            # GROUP 4: Connections & Messages (Post-event interactions)
            # ═══════════════════════════════════════════════════════════════════
            'crushspark': {'order': 1, 'icon': '✨', 'group': 'Connections'},
            'eventconnection': {'order': 2, 'icon': '🔗', 'group': 'Connections'},
            'connectionmessage': {'order': 3, 'icon': '💬', 'group': 'Connections'},

            # ═══════════════════════════════════════════════════════════════════
            # GROUP 5: Special Journey System (VIP personalized experiences)
            # ═══════════════════════════════════════════════════════════════════
            'special_user_experience': {'order': 1, 'icon': '✨', 'group': 'Special Journey'},
            'journeygift': {'order': 2, 'icon': '🎁', 'group': 'Special Journey'},  # Gifts sent via QR
            'journeyconfiguration': {'order': 3, 'icon': '🗺️', 'group': 'Special Journey'},
            'journeychapter': {'order': 4, 'icon': '📖', 'group': 'Special Journey'},
            'journeychallenge': {'order': 5, 'icon': '🎯', 'group': 'Special Journey'},
            'journeyreward': {'order': 6, 'icon': '🏆', 'group': 'Special Journey'},
            'journeyprogress': {'order': 7, 'icon': '📊', 'group': 'Special Journey'},
            'chapterprogress': {'order': 8, 'icon': '📈', 'group': 'Special Journey'},
            'challengeattempt': {'order': 9, 'icon': '🎮', 'group': 'Special Journey'},
            'rewardprogress': {'order': 10, 'icon': '✅', 'group': 'Special Journey'},

            # ═══════════════════════════════════════════════════════════════════
            # GROUP 6: Advent Calendar (Seasonal feature)
            # ═══════════════════════════════════════════════════════════════════
            'adventcalendar': {'order': 1, 'icon': '🎄', 'group': 'Advent Calendar'},
            'adventdoor': {'order': 2, 'icon': '🚪', 'group': 'Advent Calendar'},
            'adventdoorcontent': {'order': 3, 'icon': '📦', 'group': 'Advent Calendar'},
            'adventprogress': {'order': 4, 'icon': '📊', 'group': 'Advent Calendar'},
            'qrcodetoken': {'order': 5, 'icon': '📱', 'group': 'Advent Calendar'},

            # ═══════════════════════════════════════════════════════════════════
            # GROUP 7: Notifications & Settings (User preferences)
            # ═══════════════════════════════════════════════════════════════════
            'notification': {'order': 0, 'icon': '🔔', 'group': 'Notifications'},  # in-app bell
            'pushsubscription': {'order': 1, 'icon': '🔔', 'group': 'Notifications'},
            'coachpushsubscription': {'order': 2, 'icon': '📣', 'group': 'Notifications'},
            'iosappdevice': {'order': 3, 'icon': '📱', 'group': 'Notifications'},
            'androidappdevice': {'order': 3, 'icon': '🤖', 'group': 'Notifications'},
            'iosnativeauthcode': {'order': 4, 'icon': '🔐', 'group': 'Notifications'},
            'newsletter': {'order': 5, 'icon': '📰', 'group': 'Notifications'},
            'newsletterrecipient': {'order': 6, 'icon': '📨', 'group': 'Notifications'},
            'campaign': {'order': 7, 'icon': '📢', 'group': 'Notifications'},
            'campaignrecipient': {'order': 8, 'icon': '📮', 'group': 'Notifications'},
            'emailpreference': {'order': 9, 'icon': '📧', 'group': 'Notifications'},
            'useractivity': {'order': 10, 'icon': '📊', 'group': 'Notifications'},
            'profilereminder': {'order': 11, 'icon': '📬', 'group': 'Notifications'},

            # ═══════════════════════════════════════════════════════════════════
            # GROUP 8: Wallet & Passes (Apple/Google Wallet integration)
            # ═══════════════════════════════════════════════════════════════════
            'walletpassproxy': {'order': 1, 'icon': '💳', 'group': 'Wallet & Passes'},
            'passkitdeviceregistration': {'order': 2, 'icon': '📲', 'group': 'Wallet & Passes'},

            # ═══════════════════════════════════════════════════════════════════
            # GROUP 9: Growth & Referrals (Marketing and acquisition)
            # ═══════════════════════════════════════════════════════════════════
            'referralcode': {'order': 1, 'icon': '🎟️', 'group': 'Growth & Referrals'},
            'referralattribution': {'order': 2, 'icon': '🔗', 'group': 'Growth & Referrals'},

            # ═══════════════════════════════════════════════════════════════════
            # GROUP: Crush Cache (location-based scavenger hunt feature)
            # ═══════════════════════════════════════════════════════════════════
            'cachehunt': {'order': 1, 'icon': '🧭', 'group': 'Crush Cache'},
            'cachestation': {'order': 2, 'icon': '📍', 'group': 'Crush Cache'},
            'cachechallenge': {'order': 3, 'icon': '🎯', 'group': 'Crush Cache'},
            'cacheteam': {'order': 4, 'icon': '👥', 'group': 'Crush Cache'},
            'cacheteamprogress': {'order': 5, 'icon': '📈', 'group': 'Crush Cache'},
            'cachestationattempt': {'order': 6, 'icon': '🗒️', 'group': 'Crush Cache'},
            'cachechallengeattempt': {'order': 7, 'icon': '📝', 'group': 'Crush Cache'},

            # ═══════════════════════════════════════════════════════════════════
            # GROUP: Trust & Safety (moderation — reports & blocks)
            # ═══════════════════════════════════════════════════════════════════
            'userreport': {'order': 1, 'icon': '🚩', 'group': 'Trust & Safety'},
            'userblock': {'order': 2, 'icon': '🛑', 'group': 'Trust & Safety'},

            # ═══════════════════════════════════════════════════════════════════
            # GROUP 11: Site Settings (Global configuration)
            # ═══════════════════════════════════════════════════════════════════
            'crushsiteconfig': {'order': 1, 'icon': '⚙️', 'group': 'Site Settings'},

            # ───────────────────────────────────────────────────────────────────
            # SUPERUSER-ONLY GROUPS (dev / analytics / audit — hidden from coaches)
            # See SUPERUSER_ONLY_GROUPS below.
            # ───────────────────────────────────────────────────────────────────
            # Matching engine (trait-based scoring behind Crush Connect Drops)
            'trait': {'order': 1, 'icon': '🧬', 'group': 'Matching'},
            'matchscore': {'order': 2, 'icon': '💯', 'group': 'Matching'},

            # Analytics internals
            'weeklymetricssnapshot': {'order': 1, 'icon': '📊', 'group': 'Analytics'},

            # Changelog / patch notes
            'patchrelease': {'order': 1, 'icon': '🚀', 'group': 'Changelog'},
            'patchnote': {'order': 2, 'icon': '🗒️', 'group': 'Changelog'},

            # Technical & Debug (Developer tools)
            'pwadeviceinstallation': {'order': 1, 'icon': '📱', 'group': 'Technical & Debug'},
            'oauthstate': {'order': 2, 'icon': '🔐', 'group': 'Technical & Debug'},
            'userdataconsent': {'order': 3, 'icon': '🔏', 'group': 'Technical & Debug'},  # GDPR audit
        }

        # Groups that only superusers may see in the menu. Coaches keep the
        # operational groups (incl. Crush Connect); these stay hidden from them.
        SUPERUSER_ONLY_GROUPS = {
            'Matching', 'Analytics', 'Changelog', 'Technical & Debug', 'Other',
        }
        is_superuser = request.user.is_superuser

        # Sidebar tiers (rendered by the nav_sidebar.html override):
        #   pinned    -> always expanded (the daily coach workflow)
        #   more      -> collapsed under a "More" section
        #   superuser -> collapsed under a "Developer & Analytics" section
        # Each fake-app below gets a 'tier' key so the template can group them.
        # See docs/crush-admin-redesign.md.
        PINNED_GROUPS = {
            'Crush Connect', 'Users & Profiles', 'Events & Meetups',
            'Connections', 'Notifications',
        }

        def tier_for(group_key):
            if group_key in SUPERUSER_ONLY_GROUPS:
                return 'superuser'
            if group_key in PINNED_GROUPS:
                return 'pinned'
            return 'more'

        # Create grouped app list - transform single crush_lu app into multiple sections
        new_app_list = []

        # First, collect User model from auth app to merge into Users & Profiles group
        auth_user_model = None
        for app in app_list:
            if app['app_label'] == 'auth':
                for model in app['models']:
                    if model['object_name'].lower() == 'user':
                        auth_user_model = model
                        break
                break

        for app in app_list:
            if app['app_label'] == 'crush_lu':
                # Group models by category
                groups = {}

                # Add User model from auth app to the groups if found
                if auth_user_model:
                    config = custom_order.get('user')
                    if config:
                        auth_user_model['_order'] = config['order']
                        group_name = config['group']
                        icon = config['icon']
                        if not auth_user_model['name'].startswith(icon):
                            auth_user_model['name'] = f"{icon} {auth_user_model['name']}"
                        if group_name not in groups:
                            groups[group_name] = []
                        groups[group_name].append(auth_user_model)

                for model in app['models']:
                    model_name = model['object_name'].lower()

                    # Handle the special case where object_name doesn't match the key
                    # Map known variations
                    model_key = model_name
                    if model_key == 'specialuserexperience':
                        model_key = 'special_user_experience'

                    if model_key in custom_order:
                        config = custom_order[model_key]
                        model['_order'] = config['order']
                        group_name = config['group']

                        # Add icon to model name only if it doesn't already have one
                        icon = config['icon']
                        if not model['name'].startswith(icon):
                            # Remove any existing numbering (e.g., "2. Journey Configurations" -> "Journey Configurations")
                            clean_name = model['name']
                            if '. ' in clean_name and clean_name.split('. ')[0].isdigit():
                                clean_name = '. '.join(clean_name.split('. ')[1:])

                            # Add icon only (no number prefix for cleaner look)
                            model['name'] = f"{icon} {clean_name}"

                        # Add to appropriate group
                        if group_name not in groups:
                            groups[group_name] = []
                        groups[group_name].append(model)
                    else:
                        # Safety net: a registered model that isn't categorised
                        # above must never silently vanish from the menu. Drop it
                        # into the superuser-only "Other" catch-all so it stays
                        # reachable until someone gives it a proper group.
                        model['_order'] = 999
                        if not model['name'].startswith('📦'):
                            model['name'] = f"📦 {model['name']}"
                        groups.setdefault('Other', []).append(model)

                # Create separate "app" entry for each group
                # Order determines sidebar display order - organized by frequency of use
                group_order = [
                    # === DAILY USE (Coach Core Workflow) ===
                    ('💞 Crush Connect', 'Crush Connect'),             # Memberships, Drops, Sparks, Coach Picks
                    ('👥 Users & Profiles', 'Users & Profiles'),       # Profile reviews, coach assignments
                    ('🎉 Events & Meetups', 'Events & Meetups'),       # Event management, registrations
                    ('💕 Connections', 'Connections'),                 # Post-event connections, messages
                    ('🔔 Notifications', 'Notifications'),             # Push notifications, email prefs

                    # === WEEKLY / OCCASIONAL (collapsed under "More") ===
                    ('🛡️ Trust & Safety', 'Trust & Safety'),           # Reports & blocks (moderation)
                    ('🗂️ Profile Segments', 'Profile Segments'),       # Saved-filter proxy views of profiles
                    ('✨ Special Journey', 'Special Journey'),         # VIP journey creation & monitoring
                    ('📈 Growth & Referrals', 'Growth & Referrals'),   # Referral tracking, marketing

                    # === EVENT-SPECIFIC (During Events Only) ===
                    ('🧠 Quiz Night', 'Quiz Night'),                   # Live quiz management
                    ('🗳️ Activity Voting', 'Activity Voting'),         # Live event voting sessions
                    ('🧭 Crush Cache', 'Crush Cache'),                 # Location scavenger hunt

                    # === SEASONAL / OCCASIONAL ===
                    ('🎄 Advent Calendar', 'Advent Calendar'),         # December only
                    ('💳 Wallet & Passes', 'Wallet & Passes'),         # Apple/Google Wallet

                    ('⚙️ Site Settings', 'Site Settings'),               # WhatsApp, site config

                    # === SUPERUSER-ONLY (dev / analytics / audit) ===
                    ('🧬 Matching', 'Matching'),                       # Traits, match scores
                    ('📊 Analytics', 'Analytics'),                     # Weekly KPI snapshots
                    ('🗒️ Changelog', 'Changelog'),                     # Patch releases / notes
                    ('🔧 Technical & Debug', 'Technical & Debug'),     # PWA, OAuth, GDPR consent
                    ('📦 Other', 'Other'),                             # Auto-catch for uncategorised models
                ]

                for display_name, group_key in group_order:
                    # Hide dev/analytics/audit groups from non-superuser coaches.
                    if group_key in SUPERUSER_ONLY_GROUPS and not is_superuser:
                        continue
                    if group_key in groups:
                        # Sort models within each group
                        groups[group_key].sort(key=lambda x: x.get('_order', 999))

                        # Create a fake "app" for this group
                        new_app_list.append({
                            'name': display_name,
                            'app_label': f'crush_lu_{group_key.lower().replace(" ", "_").replace("&", "and")}',
                            'app_url': '#',
                            'has_module_perms': True,
                            'models': groups[group_key],
                            'tier': tier_for(group_key),
                        })
            elif app['app_label'] == 'auth':
                # Skip auth app - User model is merged into Users & Profiles group
                continue
            else:
                # Keep other apps as-is
                new_app_list.append(app)

        return new_app_list


# Use custom admin site
crush_admin_site = CrushLuAdminSite(name='crush_admin')
