from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from .models import ReferralCode, ReferralAttribution
from .oauth_statekit import get_client_ip


def ensure_session_key(request):
    if not request.session.session_key:
        request.session.save()
    return request.session.session_key


def build_referral_url(code, request=None, base_url=None):
    path = reverse('crush_lu:referral_redirect', kwargs={'code': code})
    if request is not None:
        return request.build_absolute_uri(path)
    base = base_url or getattr(settings, 'CRUSH_BASE_URL', None) or "https://crush.lu"
    return f"{base.rstrip('/')}{path}"


def capture_referral(request, code, source="link"):
    if not code:
        return None

    referral = ReferralCode.objects.filter(code__iexact=code, is_active=True).select_related('referrer').first()
    if not referral:
        return None

    request.session['referral_code'] = referral.code
    request.session['referral_source'] = source
    session_key = ensure_session_key(request)

    ReferralAttribution.objects.get_or_create(
        referral_code=referral,
        referrer=referral.referrer,
        referred_user=None,
        session_key=session_key,
        defaults={
            'ip_address': get_client_ip(request) or "",
            'user_agent': (request.META.get('HTTP_USER_AGENT') or "")[:1000],
            'landing_path': request.get_full_path(),
        }
    )
    return referral


def capture_referral_from_request(request):
    code = request.GET.get('ref')
    if code:
        return capture_referral(request, code, source="query")
    return None


def apply_referral_to_user(request, user):
    code = request.session.pop('referral_code', None)
    if not code:
        return None

    referral = ReferralCode.objects.filter(code__iexact=code, is_active=True).select_related('referrer').first()
    if not referral:
        return None

    session_key = ensure_session_key(request)
    attribution = ReferralAttribution.objects.filter(
        referral_code=referral,
        referrer=referral.referrer,
        referred_user=None,
        session_key=session_key
    ).order_by('-created_at').first()

    if attribution:
        attribution.mark_converted(user)
    else:
        ReferralAttribution.objects.create(
            referral_code=referral,
            referrer=referral.referrer,
            referred_user=user,
            status=ReferralAttribution.Status.CONVERTED,
            session_key=session_key,
            ip_address=get_client_ip(request) or "",
            user_agent=(request.META.get('HTTP_USER_AGENT') or "")[:1000],
            landing_path=request.get_full_path(),
            converted_at=timezone.now(),
        )

    referral.last_used_at = timezone.now()
    referral.save(update_fields=['last_used_at'])
    return referral
