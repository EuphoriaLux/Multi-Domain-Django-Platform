"""
Profile Reminder Admin Panel for Crush.lu Coach Panel.

Provides a GUI interface for sending profile completion reminder emails
without using the command line.
"""

from django.contrib.auth.decorators import login_required, user_passes_test
from django.shortcuts import render
from django.utils import timezone

from crush_lu.email_helpers import get_users_needing_reminder, send_profile_incomplete_reminder
from crush_lu.models import ProfileReminder


@login_required
@user_passes_test(lambda u: u.is_superuser)
def profile_reminders_panel(request):
    """
    Admin panel for sending profile completion reminders.

    GET: Shows form with preview of eligible users per reminder type.
    POST: Executes reminder sending with selected options.
    """
    results = None
    error = None

    # Get preview counts for each reminder type
    preview = {}
    for reminder_type in ['24h', '72h', '7d']:
        users = get_users_needing_reminder(reminder_type)
        preview[reminder_type] = {
            'users': list(users.select_related('crushprofile')[:10]),
            'count': users.count(),
        }

    total_eligible = sum(p['count'] for p in preview.values())

    # Handle form submission
    if request.method == 'POST':
        reminder_type = request.POST.get('reminder_type', 'all')
        try:
            limit = int(request.POST.get('limit', 100))
            if limit < 1:
                limit = 1
            elif limit > 500:
                limit = 500
        except (ValueError, TypeError):
            limit = 100
        dry_run = request.POST.get('dry_run') == 'on'

        # Execute reminders
        results = execute_reminders(reminder_type, limit, dry_run)

        # Refresh preview after sending
        for rtype in ['24h', '72h', '7d']:
            users = get_users_needing_reminder(rtype)
            preview[rtype] = {
                'users': list(users.select_related('crushprofile')[:10]),
                'count': users.count(),
            }
        total_eligible = sum(p['count'] for p in preview.values())

    # Get recent reminders for history section
    recent_reminders = (
        ProfileReminder.objects
        .select_related('user', 'user__crushprofile')
        .order_by('-sent_at')[:20]
    )

    context = {
        'title': 'Profile Reminder Panel',
        'preview': preview,
        'total_eligible': total_eligible,
        'results': results,
        'error': error,
        'recent_reminders': recent_reminders,
    }

    return render(request, 'admin/crush_lu/profile_reminders_panel.html', context)


def execute_reminders(reminder_type, limit, dry_run):
    """
    Execute profile reminder sending.

    Args:
        reminder_type: One of '24h', '72h', '7d', or 'all'
        limit: Maximum number of emails to send
        dry_run: If True, don't actually send emails

    Returns:
        dict with execution results
    """
    # Determine which reminder types to process
    if reminder_type == 'all':
        reminder_types = ['24h', '72h', '7d']
    else:
        reminder_types = [reminder_type]

    results = {
        'reminder_type': reminder_type,
        'limit': limit,
        'dry_run': dry_run,
        'sent': 0,
        'skipped': 0,
        'failed': 0,
        'details': [],
        'executed_at': timezone.now(),
    }

    emails_remaining = limit

    for rtype in reminder_types:
        if emails_remaining <= 0:
            break

        users = get_users_needing_reminder(rtype)
        users_to_process = list(users[:emails_remaining])

        for user in users_to_process:
            # Get profile info for logging
            try:
                profile = user.crushprofile
                status = profile.completion_status
            except Exception:
                status = 'unknown'

            detail = {
                'user_email': user.email,
                'user_name': f"{user.first_name} {user.last_name}".strip() or user.email,
                'reminder_type': rtype,
                'status': status,
                'result': None,
            }

            if dry_run:
                detail['result'] = 'would_send'
                results['sent'] += 1
                emails_remaining -= 1
            else:
                try:
                    success = send_profile_incomplete_reminder(
                        user=user,
                        reminder_type=rtype,
                        request=None
                    )

                    if success:
                        detail['result'] = 'sent'
                        results['sent'] += 1
                        emails_remaining -= 1
                    else:
                        detail['result'] = 'skipped'
                        results['skipped'] += 1
                except Exception as e:
                    detail['result'] = 'failed'
                    detail['error'] = str(e)
                    results['failed'] += 1

            results['details'].append(detail)

    return results
