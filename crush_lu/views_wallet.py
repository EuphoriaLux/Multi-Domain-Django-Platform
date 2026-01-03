from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.views.decorators.http import require_GET

from .models import CrushProfile
from .wallet import build_apple_pass, build_google_wallet_jwt


@login_required
@require_GET
def apple_wallet_pass(request):
    profile, _ = CrushProfile.objects.get_or_create(user=request.user)
    pkpass_data = build_apple_pass(profile)
    response = HttpResponse(pkpass_data, content_type="application/vnd.apple.pkpass")
    response["Content-Disposition"] = "attachment; filename=crushlu.pkpass"
    return response


@login_required
@require_GET
def google_wallet_jwt(request):
    profile, _ = CrushProfile.objects.get_or_create(user=request.user)
    jwt_token = build_google_wallet_jwt(profile)
    return JsonResponse({"jwt": jwt_token})
