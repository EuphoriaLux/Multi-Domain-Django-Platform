"""
Advent Calendar Views for Crush.lu

This module handles all views related to the personalized Advent Calendar experience.
Extends the Journey system to provide a 24-door December experience.
"""

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.db.models import Q
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
import json
import logging

from .decorators import crush_login_required
from .models import (
    SpecialUserExperience, JourneyConfiguration,
    AdventCalendar, AdventDoor, AdventDoorContent,
    AdventProgress, QRCodeToken
)

logger = logging.getLogger(__name__)


@crush_login_required
def advent_calendar_view(request):
    """
    Main Advent Calendar view - shows the 24-door calendar grid.
    Entry point for users with an advent calendar experience.
    """
    logger.info(f"Advent calendar view for user: {request.user.username}")

    try:
        # Find user's special experience by name matching
        special_experience = SpecialUserExperience.objects.filter(
            Q(first_name__iexact=request.user.first_name) &
            Q(last_name__iexact=request.user.last_name) &
            Q(is_active=True)
        ).first()

        if not special_experience:
            messages.warning(request, _('No special experience found for your account.'))
            return redirect('crush_lu:home')

        # Get the advent calendar journey
        journey = special_experience.advent_calendar_journey

        if not journey or not journey.is_active:
            messages.info(request, _('No Advent Calendar is currently available for you.'))
            return redirect('crush_lu:home')

        # Get the advent calendar configuration
        try:
            calendar = journey.advent_calendar
        except AdventCalendar.DoesNotExist:
            messages.warning(request, _('Your Advent Calendar is being prepared.'))
            return redirect('crush_lu:home')

        # Check if it's December
        if not calendar.is_december():
            context = {
                'calendar': calendar,
                'is_december': False,
                'message': 'The Advent Calendar will be available in December!'
            }
            return render(request, 'crush_lu/advent/calendar_locked.html', context)

        # Get or create user progress
        progress, created = AdventProgress.objects.get_or_create(
            user=request.user,
            calendar=calendar
        )

        if created:
            logger.info(f"Started advent calendar for {request.user.username}")

        # Get all doors with their status
        doors = calendar.doors.all().order_by('door_number')
        available_doors = calendar.get_available_doors()

        door_data = []
        for door in doors:
            is_available = door.door_number in available_doors
            is_opened = progress.is_door_opened(door.door_number)
            has_qr_scanned = progress.has_scanned_qr(door.door_number)

            # Check if door requires QR to open
            can_open = is_available
            if door.requires_qr_to_open() and not has_qr_scanned:
                can_open = False

            door_data.append({
                'door': door,
                'door_number': door.door_number,
                'is_available': is_available,
                'is_opened': is_opened,
                'can_open': can_open,
                'requires_qr': door.requires_qr_to_open(),
                'has_qr_bonus': door.has_qr_bonus(),
                'has_qr_scanned': has_qr_scanned,
                'teaser_text': door.teaser_text,
                'door_color': door.door_color,
                'door_icon': door.door_icon,
            })

        context = {
            'calendar': calendar,
            'progress': progress,
            'doors': door_data,
            'current_day': calendar.get_current_day(),
            'is_december': True,
            'completion_percentage': progress.completion_percentage,
            'doors_opened_count': len(progress.doors_opened or []),
            'special_experience': special_experience,
        }

        return render(request, 'crush_lu/advent/calendar.html', context)

    except Exception as e:
        logger.error(f"Error loading advent calendar: {e}", exc_info=True)
        messages.error(request, _('An error occurred loading your Advent Calendar.'))
        return redirect('crush_lu:home')


@crush_login_required
def advent_door_view(request, door_number):
    """
    View content behind a specific advent door.
    """
    logger.info(f"Advent door {door_number} view for user: {request.user.username}")

    try:
        # Validate door number
        if door_number < 1 or door_number > 24:
            messages.error(request, _('Invalid door number.'))
            return redirect('crush_lu:advent_calendar')

        # Get user's special experience
        special_experience = SpecialUserExperience.objects.filter(
            Q(first_name__iexact=request.user.first_name) &
            Q(last_name__iexact=request.user.last_name) &
            Q(is_active=True)
        ).first()

        if not special_experience:
            messages.warning(request, _('No special experience found.'))
            return redirect('crush_lu:home')

        # Get the advent calendar
        journey = special_experience.advent_calendar_journey
        if not journey:
            messages.warning(request, _('No Advent Calendar found.'))
            return redirect('crush_lu:home')

        calendar = journey.advent_calendar

        # Check if door is available
        if not calendar.is_door_available(door_number):
            current_day = calendar.get_current_day()
            if current_day is None:
                messages.info(request, _('The Advent Calendar is only available in December.'))
            elif door_number > current_day:
                messages.info(request, _('Door %(door)s will be available on December %(day)s.') % {'door': door_number, 'day': door_number})
            return redirect('crush_lu:advent_calendar')

        # Get the door
        door = get_object_or_404(AdventDoor, calendar=calendar, door_number=door_number)

        # Get user progress
        progress, _created = AdventProgress.objects.get_or_create(
            user=request.user,
            calendar=calendar
        )

        # Check QR requirements
        if door.requires_qr_to_open() and not progress.has_scanned_qr(door_number):
            messages.info(request, _('Scan the QR code on your physical gift to open this door!'))
            return redirect('crush_lu:advent_calendar')

        # Mark door as opened
        was_newly_opened = progress.open_door(door_number)
        if was_newly_opened:
            logger.info(f"{request.user.username} opened door {door_number}")

        # Get door content
        try:
            content = door.content
        except AdventDoorContent.DoesNotExist:
            content = None

        # Check if QR bonus is available
        show_bonus = door.has_qr_bonus() and progress.has_scanned_qr(door_number)

        context = {
            'calendar': calendar,
            'door': door,
            'door_number': door_number,
            'content': content,
            'progress': progress,
            'show_bonus': show_bonus,
            'has_qr_bonus': door.has_qr_bonus(),
            'was_newly_opened': was_newly_opened,
            'special_experience': special_experience,
        }

        # Render template based on content type
        template_map = {
            'challenge': 'crush_lu/advent/door_challenge.html',
            'poem': 'crush_lu/advent/door_poem.html',
            'photo': 'crush_lu/advent/door_photo.html',
            'video': 'crush_lu/advent/door_video.html',
            'audio': 'crush_lu/advent/door_audio.html',
            'gift_teaser': 'crush_lu/advent/door_gift.html',
            'memory': 'crush_lu/advent/door_memory.html',
            'quiz': 'crush_lu/advent/door_quiz.html',
            'countdown': 'crush_lu/advent/door_countdown.html',
        }

        template_name = template_map.get(door.content_type, 'crush_lu/advent/door_default.html')
        return render(request, template_name, context)

    except Exception as e:
        logger.error(f"Error opening door {door_number}: {e}", exc_info=True)
        messages.error(request, _('An error occurred opening this door.'))
        return redirect('crush_lu:advent_calendar')


@crush_login_required
@require_http_methods(["GET", "POST"])
def scan_qr_code(request, token):
    """
    Handle QR code scanning for physical gift unlocking.
    Can be accessed via GET (direct QR scan) or POST (form submission).
    """
    logger.info(f"QR scan attempt for token: {token} by user: {request.user.username}")

    try:
        # Find the QR token
        qr_token = QRCodeToken.objects.filter(token=token).first()

        if not qr_token:
            messages.error(request, _('Invalid QR code.'))
            return redirect('crush_lu:advent_calendar')

        # Verify the token belongs to this user
        if qr_token.user != request.user:
            messages.error(request, _('This QR code is not for you.'))
            return redirect('crush_lu:advent_calendar')

        # Check if token is valid (not used, not expired)
        if not qr_token.is_valid():
            if qr_token.is_used:
                messages.info(request, _('This QR code has already been used.'))
            else:
                messages.error(request, _('This QR code has expired.'))
            return redirect('crush_lu:advent_door', door_number=qr_token.door.door_number)

        # Get the door's calendar
        door = qr_token.door
        calendar = door.calendar

        # Get or create progress
        progress, _created = AdventProgress.objects.get_or_create(
            user=request.user,
            calendar=calendar
        )

        # Redeem the token
        if qr_token.redeem():
            # Record the QR scan in progress
            progress.record_qr_scan(door.door_number)

            logger.info(f"{request.user.username} scanned QR for door {door.door_number}")

            if door.requires_qr_to_open():
                messages.success(request, _('Door %(door)s unlocked! Open it to see your gift.') % {'door': door.door_number})
            elif door.has_qr_bonus():
                messages.success(request, _('Bonus content unlocked!'))

            return redirect('crush_lu:advent_door', door_number=door.door_number)
        else:
            messages.error(request, _('Could not process QR code. Please try again.'))
            return redirect('crush_lu:advent_calendar')

    except Exception as e:
        logger.error(f"Error scanning QR code: {e}", exc_info=True)
        messages.error(request, _('An error occurred. Please try again.'))
        return redirect('crush_lu:advent_calendar')


@crush_login_required
def advent_qr_scanner(request):
    """
    Display QR scanner page with camera access for mobile devices.
    """
    try:
        # Get user's special experience
        special_experience = SpecialUserExperience.objects.filter(
            Q(first_name__iexact=request.user.first_name) &
            Q(last_name__iexact=request.user.last_name) &
            Q(is_active=True)
        ).first()

        if not special_experience:
            return redirect('crush_lu:home')

        journey = special_experience.advent_calendar_journey
        if not journey:
            return redirect('crush_lu:home')

        calendar = journey.advent_calendar

        context = {
            'calendar': calendar,
            'special_experience': special_experience,
        }

        return render(request, 'crush_lu/advent/qr_scanner.html', context)

    except Exception as e:
        logger.error(f"Error loading QR scanner: {e}", exc_info=True)
        return redirect('crush_lu:advent_calendar')


# ============================================================================
# AJAX/API Endpoints
# ============================================================================

@crush_login_required
@require_http_methods(["GET"])
def get_advent_status(request):
    """
    API endpoint to get current advent calendar status.
    Returns JSON with progress and available doors.
    """
    try:
        special_experience = SpecialUserExperience.objects.filter(
            Q(first_name__iexact=request.user.first_name) &
            Q(last_name__iexact=request.user.last_name) &
            Q(is_active=True)
        ).first()

        if not special_experience:
            return JsonResponse({'success': False, 'error': 'No experience found'}, status=404)

        journey = special_experience.advent_calendar_journey
        if not journey:
            return JsonResponse({'success': False, 'error': 'No calendar found'}, status=404)

        calendar = journey.advent_calendar
        progress, _created = AdventProgress.objects.get_or_create(
            user=request.user,
            calendar=calendar
        )

        return JsonResponse({
            'success': True,
            'is_december': calendar.is_december(),
            'current_day': calendar.get_current_day(),
            'available_doors': calendar.get_available_doors(),
            'doors_opened': progress.doors_opened or [],
            'qr_scans': progress.qr_scans or [],
            'completion_percentage': progress.completion_percentage,
        })

    except Exception as e:
        logger.error(f"Error getting advent status: {e}", exc_info=True)
        return JsonResponse({'success': False, 'error': 'An error occurred while loading calendar status.'}, status=500)


@crush_login_required
@require_http_methods(["POST"])
def open_door_api(request):
    """
    API endpoint to open a door (marks it as opened).
    Expects JSON body with door_number.
    """
    try:
        data = json.loads(request.body)
        door_number = data.get('door_number')

        if not door_number or door_number < 1 or door_number > 24:
            return JsonResponse({'success': False, 'error': 'Invalid door number'}, status=400)

        special_experience = SpecialUserExperience.objects.filter(
            Q(first_name__iexact=request.user.first_name) &
            Q(last_name__iexact=request.user.last_name) &
            Q(is_active=True)
        ).first()

        if not special_experience:
            return JsonResponse({'success': False, 'error': 'No experience found'}, status=404)

        journey = special_experience.advent_calendar_journey
        if not journey:
            return JsonResponse({'success': False, 'error': 'No calendar found'}, status=404)

        calendar = journey.advent_calendar

        # Check availability
        if not calendar.is_door_available(door_number):
            return JsonResponse({
                'success': False,
                'error': 'Door not available yet',
                'available_on': f'December {door_number}'
            }, status=403)

        # Get door and check QR requirements
        door = get_object_or_404(AdventDoor, calendar=calendar, door_number=door_number)

        progress, _created = AdventProgress.objects.get_or_create(
            user=request.user,
            calendar=calendar
        )

        if door.requires_qr_to_open() and not progress.has_scanned_qr(door_number):
            return JsonResponse({
                'success': False,
                'error': 'QR scan required',
                'requires_qr': True
            }, status=403)

        # Open the door
        was_newly_opened = progress.open_door(door_number)

        return JsonResponse({
            'success': True,
            'was_newly_opened': was_newly_opened,
            'doors_opened': progress.doors_opened,
            'completion_percentage': progress.completion_percentage,
        })

    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)
    except Exception as e:
        logger.error(f"Error opening door via API: {e}", exc_info=True)
        return JsonResponse({'success': False, 'error': 'An error occurred while opening this door.'}, status=500)
