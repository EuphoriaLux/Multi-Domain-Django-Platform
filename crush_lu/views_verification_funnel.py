"""Member funnel and typed coach actions for January video verification."""

from django import forms
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_GET, require_POST
from django.views.decorators.cache import never_cache

from crush_lu.decorators import coach_required, crush_login_required, ratelimit
from crush_lu.services.real_person_check import RealPersonCheckError
from crush_lu.services.verification_funnel import (
    cancel_verification,
    claim_video_request,
    funnel_visible,
    member_funnel,
    request_verification,
)


class AppointmentForm(forms.Form):
    start_at = forms.DateTimeField(
        label=_("Agreed appointment time"),
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
        help_text=_("Enter the time agreed with the member, in Luxembourg time."),
    )
    minutes = forms.IntegerField(
        label=_("Duration in minutes"), min_value=10, max_value=60, initial=15
    )


@crush_login_required
@never_cache
@require_GET
def connect_verification(request):
    if not funnel_visible(request.user):
        return redirect("crush_lu:dashboard")
    from crush_lu.models import CrushProfile, ProfileSubmission
    from crush_lu.views import _verify_pending_luxid_member

    profile = CrushProfile.objects.filter(user=request.user).first()
    submission = ProfileSubmission.latest_for_profile(profile) if profile else None
    if profile and _verify_pending_luxid_member(request, profile, submission):
        return redirect("crush_lu:connect_verification")
    return _member_page(request)


def _member_page(request, *, status=200, error=""):
    funnel = member_funnel(request.user)
    if funnel["state"] == "luxid":
        from crush_lu.luxid import get_luxid_connect_url

        url = get_luxid_connect_url(request)
        funnel.update(action_url=url or "", action_label=_("Link LuxID") if url else "")
    return render(
        request,
        "crush_lu/crush_connect/verification.html",
        {"funnel": funnel, "funnel_error": error},
        status=status,
    )


@crush_login_required
@require_POST
@ratelimit(key="user", rate="10/h", method="POST")
def connect_verification_request(request):
    if not funnel_visible(request.user):
        raise Http404
    try:
        request_verification(request.user)
    except RealPersonCheckError:
        return _member_page(
            request,
            status=409,
            error=_(
                "Your request could not be saved. Check your current next step below."
            ),
        )
    return redirect("crush_lu:connect_verification")


@crush_login_required
@require_POST
def connect_verification_cancel(request):
    # Cancellation stays available during a configuration pause.
    cancel_verification(request.user)
    return redirect("crush_lu:connect_verification")


@coach_required
@require_POST
def coach_video_claim(request, check_id):
    try:
        check = claim_video_request(request.coach, check_id)
    except RealPersonCheckError as exc:
        if exc.status == 404:
            raise Http404 from None
        from django.contrib import messages
        from crush_lu.views_coach_real_person import _error_message

        messages.error(request, _error_message(exc))
        return redirect("crush_lu:coach_verification_channel")
    return redirect("crush_lu:coach_real_person_check", check_id=check.pk)


@coach_required
@require_POST
def coach_video_appointment(request, check_id):
    from crush_lu.services.verification_appointments import (
        arrange_appointment,
        cancel_appointment,
    )
    from crush_lu.views_coach_real_person import (
        _owned_check,
        _render_check_page,
        _error_message,
    )

    check = _owned_check(request, check_id)
    if check.purpose != "connect_week":
        raise Http404
    form = AppointmentForm(request.POST)
    try:
        if request.POST.get("action") == "cancel":
            cancel_appointment(request.coach, check_id)
        elif form.is_valid():
            arrange_appointment(request.coach, check_id, **form.cleaned_data)
        else:
            return _render_check_page(request, check, status=400, appointment_form=form)
    except RealPersonCheckError as exc:
        return _render_check_page(
            request,
            check,
            status=exc.status,
            error_messages=[_error_message(exc)],
            appointment_form=form,
        )
    return redirect("crush_lu:coach_real_person_check", check_id=check.pk)
