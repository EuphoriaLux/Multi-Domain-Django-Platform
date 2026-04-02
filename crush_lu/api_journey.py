"""
Journey System API Endpoints

Handles AJAX requests for challenge submissions, hints, progress tracking, etc.
"""

from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.utils import timezone
from django.utils.translation import gettext as _
from .decorators import crush_login_required
from .models import (
    JourneyChallenge, JourneyProgress, ChapterProgress, ChallengeAttempt,
    JourneyReward, RewardProgress
)
from .utils.journey_validation import (
    normalize_answer,
    validate_answer_format,
    sanitize_answer_for_storage,
    compare_answers
)
import json
import logging

logger = logging.getLogger(__name__)


@crush_login_required
@require_http_methods(["POST"])
def submit_challenge(request):
    """
    Handle challenge answer submission.
    Returns JSON with success status and points earned.
    """
    try:
        data = json.loads(request.body)
        challenge_id = data.get('challenge_id')
        user_answer = data.get('answer', '')

        if not challenge_id:
            return JsonResponse({
                'success': False,
                'message': _('Missing challenge ID')
            }, status=400)

        # Get the challenge
        try:
            challenge = JourneyChallenge.objects.get(id=challenge_id)
        except JourneyChallenge.DoesNotExist:
            return JsonResponse({
                'success': False,
                'message': _('Challenge not found')
            }, status=404)

        # Validate answer format
        is_valid, error_message = validate_answer_format(user_answer, challenge.challenge_type)
        if not is_valid:
            return JsonResponse({
                'success': False,
                'message': error_message
            }, status=400)

        # Normalize answer for comparison
        normalized_answer = normalize_answer(user_answer, challenge.challenge_type)

        # Sanitize answer for storage
        sanitized_answer = sanitize_answer_for_storage(user_answer, challenge.challenge_type)

        # Get user's chapter progress
        journey_progress = JourneyProgress.objects.filter(
            user=request.user
        ).select_related('journey').first()

        if not journey_progress:
            return JsonResponse({
                'success': False,
                'message': _('No active journey found')
            }, status=404)

        chapter_progress, created = ChapterProgress.objects.get_or_create(
            journey_progress=journey_progress,
            chapter=challenge.chapter
        )

        # Check if already completed
        existing_attempt = ChallengeAttempt.objects.filter(
            chapter_progress=chapter_progress,
            challenge=challenge,
            is_correct=True
        ).first()

        if existing_attempt:
            return JsonResponse({
                'success': True,
                'already_completed': True,
                'message': _('You already completed this challenge!'),
                'points_earned': existing_attempt.points_earned
            })

        # Special handling for Chapters 2, 4, 5 - they're questionnaires, not quizzes
        # All answers are accepted and saved for later analysis
        # Also accept all open_text/would_you_rather challenges regardless of chapter
        # OR if no correct_answer is set (blank = questionnaire mode)
        if (challenge.chapter.chapter_number in [2, 4, 5] or
            challenge.challenge_type in ['open_text', 'would_you_rather'] or
            not challenge.correct_answer.strip()):
            is_correct = True  # All answers accepted
            points_earned = challenge.points_awarded  # Full points awarded
            hints_used = []  # No hints in questionnaire mode
        else:
            # For word_scramble, use language-specific answer from options if available
            if challenge.challenge_type == 'word_scramble' and isinstance(challenge.options, dict) and challenge.options.get('answer'):
                correct = challenge.options['answer']
                alts = challenge.options.get('alternatives', [])
            else:
                correct = challenge.correct_answer
                alts = challenge.alternative_answers or []

            # Regular validation using utility functions
            is_correct = compare_answers(
                user_answer,
                correct,
                challenge.challenge_type
            )

            # Also check alternative answers if provided
            if not is_correct and alts:
                for alt_answer in alts:
                    if compare_answers(user_answer, alt_answer, challenge.challenge_type):
                        is_correct = True
                        break

            # Get hints used from database (tamper-proof)
            existing_attempt = ChallengeAttempt.objects.filter(
                chapter_progress=chapter_progress,
                challenge=challenge
            ).first()
            hints_used = existing_attempt.hints_used if existing_attempt else []

            # Calculate points (base points minus hint deductions)
            points_earned = 0
            if is_correct:
                points_earned = challenge.points_awarded
                for hint_num in hints_used:
                    if hint_num == 1:
                        points_earned -= challenge.hint_1_cost
                    elif hint_num == 2:
                        points_earned -= challenge.hint_2_cost
                    elif hint_num == 3:
                        points_earned -= challenge.hint_3_cost
                points_earned = max(0, points_earned)  # Don't go negative

        # Save attempt (use sanitized answer)
        attempt = ChallengeAttempt.objects.create(
            chapter_progress=chapter_progress,
            challenge=challenge,
            user_answer=sanitized_answer,
            is_correct=is_correct,
            hints_used=hints_used,
            points_earned=points_earned
        )

        # If correct, update progress
        if is_correct:
            # Update chapter progress points
            chapter_progress.points_earned += points_earned
            chapter_progress.save()

            # Update journey progress points
            journey_progress.total_points += points_earned
            journey_progress.save()

            # Clear hints from session
            if f'hints_used_{challenge_id}' in request.session:
                del request.session[f'hints_used_{challenge_id}']

            logger.info(
                f"✅ {request.user.username} solved challenge {challenge_id} "
                f"({challenge.get_challenge_type_display()}) - {points_earned} pts"
            )

            return JsonResponse({
                'success': True,
                'is_correct': True,
                'points_earned': points_earned,
                'total_points': journey_progress.total_points,
                'success_message': challenge.success_message,
                'message': _('Correct! Well done! 🎉')
            })
        else:
            return JsonResponse({
                'success': True,
                'is_correct': False,
                'message': _('Not quite right. Try again! 💪')
            })

    except Exception as e:
        logger.error(f"❌ Error submitting challenge: {e}", exc_info=True)
        return JsonResponse({
            'success': False,
            'message': _('An error occurred processing your answer')
        }, status=500)


@crush_login_required
@require_http_methods(["POST"])
def unlock_hint(request):
    """
    Unlock a hint for a challenge.

    Security: Stores hint usage in database (ChallengeAttempt.hints_used)
    instead of session to prevent user tampering.
    """
    try:
        data = json.loads(request.body)
        challenge_id = data.get('challenge_id')
        hint_number = data.get('hint_number')

        if not challenge_id or not hint_number:
            return JsonResponse({
                'success': False,
                'message': _('Missing challenge ID or hint number')
            }, status=400)

        # Get the challenge - must belong to user's journey
        journey_progress = JourneyProgress.objects.filter(user=request.user).first()
        if not journey_progress:
            return JsonResponse({
                'success': False,
                'message': _('No active journey found')
            }, status=404)

        try:
            challenge = JourneyChallenge.objects.get(
                id=challenge_id,
                chapter__journey=journey_progress.journey  # SECURITY: Must be user's journey
            )
        except JourneyChallenge.DoesNotExist:
            return JsonResponse({
                'success': False,
                'message': _('Challenge not found')
            }, status=404)

        # Get hint text and cost
        hint_text = None
        hint_cost = 0

        if hint_number == 1:
            hint_text = challenge.hint_1
            hint_cost = challenge.hint_1_cost
        elif hint_number == 2:
            hint_text = challenge.hint_2
            hint_cost = challenge.hint_2_cost
        elif hint_number == 3:
            hint_text = challenge.hint_3
            hint_cost = challenge.hint_3_cost
        else:
            return JsonResponse({
                'success': False,
                'message': _('Invalid hint number')
            }, status=400)

        if not hint_text:
            return JsonResponse({
                'success': False,
                'message': _('Hint not available')
            }, status=404)

        # Track hint usage in database (tamper-proof)
        # Get or create chapter progress
        chapter_progress, created = ChapterProgress.objects.get_or_create(
            journey_progress=journey_progress,
            chapter=challenge.chapter
        )

        # Get or create challenge attempt for this user/challenge
        # Use filter().first() to handle any existing duplicate records gracefully
        attempt = ChallengeAttempt.objects.filter(
            chapter_progress=chapter_progress,
            challenge=challenge
        ).first()

        if not attempt:
            attempt = ChallengeAttempt.objects.create(
                chapter_progress=chapter_progress,
                challenge=challenge,
                user_answer='',  # Empty until they submit
                hints_used=[]
            )

        # Add hint to the list if not already used
        hints_used = attempt.hints_used or []
        if hint_number not in hints_used:
            hints_used.append(hint_number)
            attempt.hints_used = hints_used
            attempt.save(update_fields=['hints_used'])

        return JsonResponse({
            'success': True,
            'hint_text': hint_text,
            'hint_cost': hint_cost,
            'total_hints_used': len(hints_used)
        })

    except Exception as e:
        logger.error(f"❌ Error unlocking hint: {e}", exc_info=True)
        return JsonResponse({
            'success': False,
            'message': _('An error occurred unlocking the hint')
        }, status=500)


@crush_login_required
@require_http_methods(["GET"])
def get_progress(request):
    """
    Get user's current journey progress.
    Used for progress bars, stats display, etc.
    """
    try:
        journey_progress = JourneyProgress.objects.filter(
            user=request.user
        ).select_related('journey').first()

        if not journey_progress:
            return JsonResponse({
                'success': False,
                'message': _('No active journey found')
            }, status=404)

        # Get completed chapters count
        completed_chapters = journey_progress.chapter_completions.filter(
            is_completed=True
        ).count()

        return JsonResponse({
            'success': True,
            'data': {
                'journey_name': journey_progress.journey.journey_name,
                'current_chapter': journey_progress.current_chapter,
                'total_chapters': journey_progress.journey.total_chapters,
                'completed_chapters': completed_chapters,
                'completion_percentage': journey_progress.completion_percentage,
                'total_points': journey_progress.total_points,
                'time_spent_seconds': journey_progress.total_time_seconds,
                'is_completed': journey_progress.is_completed,
            }
        })

    except Exception as e:
        logger.error(f"❌ Error getting progress: {e}", exc_info=True)
        return JsonResponse({
            'success': False,
            'message': _('An error occurred retrieving progress')
        }, status=500)


@crush_login_required
@require_http_methods(["POST"])
def save_state(request):
    """
    Save journey state (time spent, current position, etc.)
    Called periodically via JavaScript to track progress.
    """
    try:
        # Use request.POST for form data or json.loads for JSON
        if request.content_type == 'application/json':
            data = json.loads(request.body)
        else:
            data = request.POST.dict()

        # Ensure time_increment is an integer
        time_increment = int(data.get('time_increment', 0))  # Seconds since last save

        journey_progress = JourneyProgress.objects.filter(
            user=request.user
        ).first()

        if not journey_progress:
            return JsonResponse({
                'success': False,
                'message': _('No active journey found')
            }, status=404)

        # Update time spent
        if time_increment > 0:
            journey_progress.total_time_seconds += time_increment
            journey_progress.save(update_fields=['total_time_seconds', 'last_activity'])

            logger.debug(f"⏱️ Updated time for {request.user.username}: +{time_increment}s")

        return JsonResponse({
            'success': True,
            'total_time': journey_progress.total_time_seconds
        })

    except Exception as e:
        logger.error(f"❌ Error saving state: {e}", exc_info=True)
        return JsonResponse({
            'success': False,
            'message': _('An error occurred saving state')
        }, status=500)


@crush_login_required
@require_http_methods(["POST"])
def record_final_response(request):
    """
    Record user's response to the final chapter (Yes/Thinking).
    Sends email notification to journey creator.
    """
    try:
        data = json.loads(request.body)
        response_choice = data.get('response')  # 'yes' or 'thinking'

        if response_choice not in ['yes', 'thinking']:
            return JsonResponse({
                'success': False,
                'message': _('Invalid response choice')
            }, status=400)

        journey_progress = JourneyProgress.objects.filter(
            user=request.user
        ).select_related('journey__special_experience').first()

        if not journey_progress:
            return JsonResponse({
                'success': False,
                'message': _('No active journey found')
            }, status=404)

        # Update final response
        journey_progress.final_response = response_choice
        journey_progress.final_response_at = timezone.now()

        # Mark journey as completed if not already
        if not journey_progress.is_completed:
            journey_progress.is_completed = True
            journey_progress.completed_at = timezone.now()

        journey_progress.save()

        logger.info(
            f"💖 {request.user.username} responded '{response_choice}' to final chapter"
        )

        # Send email notification
        try:
            from django.core.mail import send_mail
            from django.conf import settings

            user_name = f"{request.user.first_name} {request.user.last_name}"
            response_text = "Yes, let's see where this goes 💫" if response_choice == 'yes' else "I need to think about this ✨"

            subject = f"🎉 {user_name} completed the journey!"
            message = f"""
            Great news! {user_name} just completed "The Wonderland of You" journey!

            Final Response: {response_text}
            Completed: {journey_progress.completed_at.strftime('%B %d, %Y at %I:%M %p')}
            Total Points Earned: {journey_progress.total_points}
            Time Spent: {journey_progress.total_time_seconds // 60} minutes

            Journey Details:
            - Journey Name: {journey_progress.journey.journey_name}
            - Chapters Completed: {journey_progress.journey.total_chapters}
            - Completion Percentage: {journey_progress.completion_percentage}%

            View full details in the admin panel:
            https://crush.lu/crush-admin/crush_lu/journeyprogress/{journey_progress.id}/change/

            ---
            This is an automated notification from Crush.lu Journey System
            """

            # Send to admin email (configure in settings)
            recipient_email = getattr(settings, 'JOURNEY_NOTIFICATION_EMAIL', settings.DEFAULT_FROM_EMAIL)

            send_mail(
                subject,
                message,
                settings.DEFAULT_FROM_EMAIL,
                [recipient_email],
                fail_silently=True  # Don't break the response if email fails
            )

            logger.info(f"📧 Sent journey completion email for {user_name}")
        except Exception as email_error:
            logger.error(f"❌ Failed to send email notification: {email_error}", exc_info=True)
            # Continue anyway - email failure shouldn't break the response

        return JsonResponse({
            'success': True,
            'message': _('Response recorded'),
            'response': response_choice,
            'completed': True
        })

    except Exception as e:
        logger.error(f"❌ Error recording final response: {e}", exc_info=True)
        return JsonResponse({
            'success': False,
            'message': _('An error occurred recording your response')
        }, status=500)


@crush_login_required
@require_http_methods(["POST"])
def unlock_puzzle_piece(request):
    """
    Unlock a jigsaw puzzle piece using points.
    Tracks progress persistently in RewardProgress model.
    """
    try:
        data = json.loads(request.body)
        reward_id = data.get('reward_id')
        piece_index = data.get('piece_index')

        if reward_id is None or piece_index is None:
            return JsonResponse({
                'success': False,
                'message': _('Missing reward ID or piece index')
            }, status=400)

        # Get user's journey progress
        journey_progress = JourneyProgress.objects.filter(
            user=request.user
        ).select_related('journey').first()

        if not journey_progress:
            return JsonResponse({
                'success': False,
                'message': _('No active journey found')
            }, status=404)

        # Get the reward
        try:
            reward = JourneyReward.objects.get(id=reward_id)
        except JourneyReward.DoesNotExist:
            return JsonResponse({
                'success': False,
                'message': _('Reward not found')
            }, status=404)

        # Get or create reward progress
        reward_progress, created = RewardProgress.objects.get_or_create(
            journey_progress=journey_progress,
            reward=reward
        )

        # Define points cost per piece (50 points per piece)
        PIECE_COST = 50

        # Check if already unlocked
        if piece_index in reward_progress.unlocked_pieces:
            return JsonResponse({
                'success': False,
                'message': _('This piece is already unlocked'),
                'already_unlocked': True
            })

        # Check if user has enough points
        if journey_progress.total_points < PIECE_COST:
            return JsonResponse({
                'success': False,
                'message': _('Not enough points! You need %(points)s points to unlock this piece.') % {'points': PIECE_COST},
                'insufficient_points': True,
                'points_needed': PIECE_COST,
                'current_points': journey_progress.total_points
            })

        # Deduct points and unlock piece
        journey_progress.total_points -= PIECE_COST
        journey_progress.save()

        reward_progress.unlocked_pieces.append(piece_index)
        reward_progress.points_spent += PIECE_COST

        # Check if completed (all 16 pieces)
        if len(reward_progress.unlocked_pieces) == 16:
            reward_progress.is_completed = True
            reward_progress.completed_at = timezone.now()

        reward_progress.save()

        logger.info(
            f"🧩 {request.user.username} unlocked piece {piece_index} for {PIECE_COST} points "
            f"({len(reward_progress.unlocked_pieces)}/16 complete)"
        )

        return JsonResponse({
            'success': True,
            'unlocked_pieces': reward_progress.unlocked_pieces,
            'points_remaining': journey_progress.total_points,
            'points_spent': PIECE_COST,
            'is_completed': reward_progress.is_completed,
            'total_unlocked': len(reward_progress.unlocked_pieces),
            'message': _('Piece unlocked! -%(points)s points') % {'points': PIECE_COST}
        })

    except Exception as e:
        logger.error(f"❌ Error unlocking puzzle piece: {e}", exc_info=True)
        return JsonResponse({
            'success': False,
            'message': _('An error occurred unlocking the piece')
        }, status=500)


@crush_login_required
@require_http_methods(["GET"])
def get_reward_progress(request, reward_id):
    """
    Get the user's progress for a specific reward (e.g., jigsaw puzzle).
    """
    try:
        # Get user's journey progress
        journey_progress = JourneyProgress.objects.filter(
            user=request.user
        ).first()

        if not journey_progress:
            return JsonResponse({
                'success': False,
                'message': _('No active journey found')
            }, status=404)

        # Get reward progress if exists
        try:
            reward_progress = RewardProgress.objects.get(
                journey_progress=journey_progress,
                reward_id=reward_id
            )
            unlocked_pieces = reward_progress.unlocked_pieces
            is_completed = reward_progress.is_completed
        except RewardProgress.DoesNotExist:
            unlocked_pieces = []
            is_completed = False

        return JsonResponse({
            'success': True,
            'unlocked_pieces': unlocked_pieces,
            'is_completed': is_completed,
            'total_unlocked': len(unlocked_pieces),
            'current_points': journey_progress.total_points
        })

    except Exception as e:
        logger.error(f"❌ Error getting reward progress: {e}", exc_info=True)
        return JsonResponse({
            'success': False,
            'message': _('An error occurred retrieving reward progress')
        }, status=500)
