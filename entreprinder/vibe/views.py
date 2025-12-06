# entreprinder/vibe/views.py
"""
Vibe Coding Views - Pixel War and Games
"""

from django.shortcuts import render, get_object_or_404
from django.utils.translation import gettext as _
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from datetime import timedelta
import json

from .models import PixelCanvas, Pixel, PixelHistory, UserPixelCooldown, UserPixelStats


def index(request):
    """Renders the main page for the Vibe Coding section."""
    context = {'page_title': _('Vibe Coding Projects')}
    return render(request, 'vibe_coding/index.html', context)


@ensure_csrf_cookie
def pixel_war(request):
    """Main view for the Lux Pixel War game"""
    canvas, created = PixelCanvas.objects.get_or_create(
        name="Lux Pixel War",
        defaults={
            'width': 100,
            'height': 100,
            'anonymous_cooldown_seconds': 30,
            'registered_cooldown_seconds': 12,
            'registered_pixels_per_minute': 5,
            'anonymous_pixels_per_minute': 2
        }
    )

    user_stats = None
    if request.user.is_authenticated:
        user_stats, created_stats = UserPixelStats.objects.get_or_create(
            user=request.user,
            canvas=canvas
        )

    context = {
        'canvas': canvas,
        'page_title': _('Lux Pixel War'),
        'is_authenticated': request.user.is_authenticated,
        'user_stats': user_stats,
    }
    return render(request, 'vibe_coding/pixel_war.html', context)


@ensure_csrf_cookie
def pixel_war_optimized(request):
    """Optimized version of Pixel War with better performance"""
    canvas, created = PixelCanvas.objects.get_or_create(
        name="Lux Pixel War",
        defaults={
            'width': 100,
            'height': 100,
            'anonymous_cooldown_seconds': 30,
            'registered_cooldown_seconds': 12,
            'registered_pixels_per_minute': 5,
            'anonymous_pixels_per_minute': 2
        }
    )

    user_stats = None
    if request.user.is_authenticated:
        user_stats, created_stats = UserPixelStats.objects.get_or_create(
            user=request.user,
            canvas=canvas
        )

    context = {
        'canvas': canvas,
        'page_title': _('Lux Pixel War - Optimized'),
        'is_authenticated': request.user.is_authenticated,
        'user_stats': user_stats,
    }
    return render(request, 'vibe_coding/pixel_war_optimized.html', context)


@ensure_csrf_cookie
def pixel_war_pixi(request):
    """PixiJS WebGL version of Pixel War for maximum performance"""
    canvas, created = PixelCanvas.objects.get_or_create(
        name="Lux Pixel War",
        defaults={
            'width': 100,
            'height': 100,
            'anonymous_cooldown_seconds': 30,
            'registered_cooldown_seconds': 12,
            'registered_pixels_per_minute': 5,
            'anonymous_pixels_per_minute': 2
        }
    )

    user_stats = None
    if request.user.is_authenticated:
        user_stats, created_stats = UserPixelStats.objects.get_or_create(
            user=request.user,
            canvas=canvas
        )

    context = {
        'canvas': canvas,
        'page_title': _('Lux Pixel War - PixiJS'),
        'is_authenticated': request.user.is_authenticated,
        'user_stats': user_stats,
    }
    return render(request, 'vibe_coding/pixel_war_pixi.html', context)


@ensure_csrf_cookie
def pixel_war_ts(request):
    """TypeScript version of Pixel War with enhanced mobile navigation"""
    canvas, created = PixelCanvas.objects.get_or_create(
        name="Lux Pixel War",
        defaults={
            'width': 100,
            'height': 100,
            'anonymous_cooldown_seconds': 30,
            'registered_cooldown_seconds': 12,
            'registered_pixels_per_minute': 5,
            'anonymous_pixels_per_minute': 2
        }
    )

    user_stats = None
    if request.user.is_authenticated:
        user_stats, created_stats = UserPixelStats.objects.get_or_create(
            user=request.user,
            canvas=canvas
        )

    context = {
        'canvas': canvas,
        'page_title': _('Lux Pixel War - TypeScript'),
        'is_authenticated': request.user.is_authenticated,
        'user_stats': user_stats,
    }
    return render(request, 'vibe_coding/pixel_war_ts.html', context)


@ensure_csrf_cookie
def pixel_war_react(request):
    """React version of Pixel War with modern component-based architecture"""
    canvas, created = PixelCanvas.objects.get_or_create(
        name="Lux Pixel War",
        defaults={
            'width': 100,
            'height': 100,
            'anonymous_cooldown_seconds': 30,
            'registered_cooldown_seconds': 12,
            'registered_pixels_per_minute': 5,
            'anonymous_pixels_per_minute': 2
        }
    )

    user_stats = None
    if request.user.is_authenticated:
        user_stats, created_stats = UserPixelStats.objects.get_or_create(
            user=request.user,
            canvas=canvas
        )

    context = {
        'canvas': canvas,
        'page_title': _('Lux Pixel War - React'),
        'is_authenticated': request.user.is_authenticated,
        'user_stats': user_stats,
    }
    return render(request, 'vibe_coding/pixel_war_react.html', context)


@ensure_csrf_cookie
def pixel_war_demo(request):
    """Demo page showcasing both desktop and mobile React versions"""
    canvas, created = PixelCanvas.objects.get_or_create(
        name="Lux Pixel War",
        defaults={
            'width': 100,
            'height': 100,
            'anonymous_cooldown_seconds': 30,
            'registered_cooldown_seconds': 12,
            'registered_pixels_per_minute': 5,
            'anonymous_pixels_per_minute': 2
        }
    )

    user_stats = None
    if request.user.is_authenticated:
        user_stats, created_stats = UserPixelStats.objects.get_or_create(
            user=request.user,
            canvas=canvas
        )

    context = {
        'canvas': canvas,
        'page_title': _('Pixel Wars React Demo'),
        'is_authenticated': request.user.is_authenticated,
        'user_stats': user_stats,
    }
    return render(request, 'vibe_coding/pixel_war_demo.html', context)


def get_canvas_state(request, canvas_id=None):
    """Get the current state of the canvas"""
    if canvas_id:
        canvas = get_object_or_404(PixelCanvas, id=canvas_id)
    else:
        canvas = PixelCanvas.objects.first()

    if not canvas:
        return JsonResponse({'error': 'No canvas found'}, status=404)

    pixels = canvas.pixels.all()
    pixel_data = {}

    for pixel in pixels:
        key = f"{pixel.x},{pixel.y}"
        pixel_data[key] = {
            'color': pixel.color,
            'placed_by': pixel.placed_by.username if pixel.placed_by else 'Anonymous',
            'placed_at': pixel.placed_at.isoformat()
        }

    return JsonResponse({
        'success': True,
        'canvas': {
            'id': canvas.id,
            'name': canvas.name,
            'width': canvas.width,
            'height': canvas.height,
            'anonymous_cooldown': canvas.anonymous_cooldown_seconds,
            'registered_cooldown': canvas.registered_cooldown_seconds,
            'anonymous_pixels_per_minute': canvas.anonymous_pixels_per_minute,
            'registered_pixels_per_minute': canvas.registered_pixels_per_minute,
            'is_active': canvas.is_active
        },
        'pixels': pixel_data
    })


def place_pixel(request):
    """Place a pixel on the canvas with tiered cooldown system"""
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    try:
        data = json.loads(request.body)
        x = int(data.get('x'))
        y = int(data.get('y'))
        color = data.get('color', '#FFFFFF')
        canvas_id = data.get('canvas_id')

        if canvas_id:
            canvas = get_object_or_404(PixelCanvas, id=canvas_id)
        else:
            canvas = PixelCanvas.objects.first()

        if not canvas or not canvas.is_active:
            return JsonResponse({'error': 'Canvas is not active'}, status=400)

        if x < 0 or x >= canvas.width or y < 0 or y >= canvas.height:
            return JsonResponse({'error': 'Invalid coordinates'}, status=400)

        session_key = request.session.session_key
        if not session_key:
            request.session.create()
            session_key = request.session.session_key

        try:
            if request.user.is_authenticated:
                cooldown, created = UserPixelCooldown.objects.get_or_create(
                    user=request.user,
                    canvas=canvas,
                    defaults={'session_key': None, 'pixels_placed_last_minute': 0}
                )
                cooldown_seconds = canvas.registered_cooldown_seconds
                max_pixels_per_minute = canvas.registered_pixels_per_minute
            else:
                cooldown, created = UserPixelCooldown.objects.get_or_create(
                    user=None,
                    canvas=canvas,
                    session_key=session_key,
                    defaults={'pixels_placed_last_minute': 0}
                )
                cooldown_seconds = canvas.anonymous_cooldown_seconds
                max_pixels_per_minute = canvas.anonymous_pixels_per_minute
        except UserPixelCooldown.MultipleObjectsReturned:
            if request.user.is_authenticated:
                cooldowns = UserPixelCooldown.objects.filter(
                    user=request.user,
                    canvas=canvas
                ).order_by('-last_placed')
                cooldown = cooldowns.first()
                if cooldowns.count() > 1:
                    cooldowns.exclude(id=cooldown.id).delete()
                cooldown_seconds = canvas.registered_cooldown_seconds
                max_pixels_per_minute = canvas.registered_pixels_per_minute
            else:
                cooldowns = UserPixelCooldown.objects.filter(
                    user=None,
                    canvas=canvas,
                    session_key=session_key
                ).order_by('-last_placed')
                cooldown = cooldowns.first()
                if cooldowns.count() > 1:
                    cooldowns.exclude(id=cooldown.id).delete()
                cooldown_seconds = canvas.anonymous_cooldown_seconds
                max_pixels_per_minute = canvas.anonymous_pixels_per_minute
            created = False

        time_since_minute_reset = timezone.now() - cooldown.last_minute_reset
        if time_since_minute_reset.total_seconds() >= 60:
            cooldown.pixels_placed_last_minute = 0
            cooldown.last_minute_reset = timezone.now()
            cooldown.save()

        if cooldown.pixels_placed_last_minute >= max_pixels_per_minute:
            time_until_reset = 60 - time_since_minute_reset.total_seconds()
            return JsonResponse({
                'error': 'Pixel limit reached for this minute',
                'cooldown_remaining': int(time_until_reset),
                'limit_info': {
                    'max_per_minute': max_pixels_per_minute,
                    'placed_this_minute': cooldown.pixels_placed_last_minute,
                    'is_registered': request.user.is_authenticated
                }
            }, status=429)

        cooldown.last_placed = timezone.now()
        cooldown.pixels_placed_last_minute += 1
        cooldown.save()

        if request.user.is_authenticated:
            stats, stats_created = UserPixelStats.objects.get_or_create(
                user=request.user,
                canvas=canvas
            )
            stats.total_pixels_placed += 1
            stats.last_pixel_placed = timezone.now()
            stats.save()

        pixel, created = Pixel.objects.update_or_create(
            canvas=canvas,
            x=x,
            y=y,
            defaults={
                'color': color,
                'placed_by': request.user if request.user.is_authenticated else None
            }
        )

        PixelHistory.objects.create(
            canvas=canvas,
            x=x,
            y=y,
            color=color,
            placed_by=request.user if request.user.is_authenticated else None
        )

        return JsonResponse({
            'success': True,
            'pixel': {
                'x': pixel.x,
                'y': pixel.y,
                'color': pixel.color,
                'placed_by': pixel.placed_by.username if pixel.placed_by else 'Anonymous'
            },
            'cooldown_info': {
                'cooldown_seconds': 0,
                'pixels_remaining': max_pixels_per_minute - cooldown.pixels_placed_last_minute,
                'is_registered': request.user.is_authenticated
            }
        })

    except (ValueError, KeyError) as e:
        return JsonResponse({'error': str(e)}, status=400)
    except Exception as e:
        return JsonResponse({'error': 'Server error: ' + str(e)}, status=500)


def get_pixel_history(request):
    """Get the history of pixel placements"""
    canvas_id = request.GET.get('canvas_id')
    limit = int(request.GET.get('limit', 50))

    if canvas_id:
        canvas = get_object_or_404(PixelCanvas, id=canvas_id)
        history = PixelHistory.objects.filter(canvas=canvas)[:limit]
    else:
        history = PixelHistory.objects.all()[:limit]

    history_data = [{
        'x': h.x,
        'y': h.y,
        'color': h.color,
        'placed_by': h.placed_by.username if h.placed_by else 'Anonymous',
        'placed_at': h.placed_at.isoformat()
    } for h in history]

    return JsonResponse({
        'success': True,
        'history': history_data
    })


def road_trip_music_game(request):
    """Renders the Road Trip Discovery Game with Music Integration"""
    context = {
        'page_title': _('Road Trip Discovery with Music - 90 Minutes to Connection')
    }
    return render(request, 'vibe_coding/road_trip_music_game.html', context)
