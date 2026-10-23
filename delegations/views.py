"""
Views for Delegations.lu app.

Handles the multilingual SEO marketing frontpage, the interactive employment-issue
triage prototype, and the authenticated company delegation portal.
"""

import hashlib
import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.translation import get_language
from django.utils.translation import gettext as _

from .signals import ensure_delegation_profile_for_user

logger = logging.getLogger(__name__)

VALID_ISSUE_CATEGORIES = {
    "dismissal",
    "harassment",
    "salary",
    "contract",
    "sick_leave",
    "delegation",
}

VALID_URGENCY_LEVELS = {"urgent", "active", "preventive"}
VALID_COMPANY_SIZES = {"15_plus", "under_15", "unsure"}
VALID_DELEGATION_STATES = {"yes", "no", "unsure"}
VALID_PREFERRED_LANGUAGES = {"fr", "de", "en"}


def _get_or_create_profile(user):
    """
    Get existing DelegationProfile or create one from Microsoft SocialAccount.
    Returns (profile, created) tuple.
    """
    return ensure_delegation_profile_for_user(user)


def _build_intake_guidance(
    issue_category: str,
    urgency: str,
    company_size: str,
    has_delegation: str,
    contact_email: str,
) -> dict:
    """
    Build localized Luxembourg labour-law orientation guidance for the prototype
    employment-issue intake flow.
    """
    digest = (
        hashlib.sha256(
            f"{contact_email}:{issue_category}:{timezone.now().date().isoformat()}".encode()
        )
        .hexdigest()[:6]
        .upper()
    )
    reference_code = f"DLG-LU-{digest}"

    category_guidance = {
        "dismissal": {
            "label": _("Dismissal, Warning Letter & Notice Period"),
            "legal_reference": _(
                "Luxembourg Labour Code (Art. L.124-5 & L.124-11): After a dismissal with notice, you have 1 month from notification to request the precise reasons by registered letter (lettre recommandée)."
            ),
            "steps": [
                _(
                    "Send a registered letter (lettre recommandée avec accusé de réception) within 1 month to request the formal grounds for dismissal if you were dismissed with notice."
                ),
                _(
                    "Keep the original envelope, postal receipt, employment contract, and any warning letters or performance emails."
                ),
                _(
                    "If your company has 150+ employees (or a collective agreement), verify whether the mandatory pre-dismissal interview (entretien préalable) and right to assistance by a staff delegate were respected."
                ),
            ],
        },
        "harassment": {
            "label": _("Workplace Harassment, Mobbing & Burnout"),
            "legal_reference": _(
                "Luxembourg Law of 29 March 2023 (Art. L.246-1 to L.246-7): Employers must prevent and stop moral harassment immediately, and victims/witnesses are legally protected against retaliation."
            ),
            "steps": [
                _(
                    "Keep a dated factual log of incidents, messages, witnesses, and medical consultations without removing confidential employer data."
                ),
                _(
                    "Alert your Staff Delegation (Délégation du personnel) or Equality/Safety Delegate, who holds a statutory mandate to assist and accompany you."
                ),
                _(
                    "If internal measures fail, the Inspection du travail et des mines (ITM) can be seized to investigate and order the employer to cease harassment."
                ),
            ],
        },
        "salary": {
            "label": _("Unpaid Salary, Overtime, Indexation & Leave"),
            "legal_reference": _(
                "Luxembourg Labour Code (Art. L.221-1 & L.211-27): Wages, statutory indexation (index), and approved overtime compensation are strictly regulated; salary claims remain actionable for 3 years."
            ),
            "steps": [
                _(
                    "Collect your payslips, time-tracking records, shift schedules, and written requests for overtime or paid leave."
                ),
                _(
                    "Verify whether your sector is covered by a Luxembourg Collective Bargaining Agreement (Convention collective) with higher overtime or bonus rates."
                ),
                _(
                    "Request a written clarification from HR or escalate through your Staff Delegation or the ITM helpdesk."
                ),
            ],
        },
        "contract": {
            "label": _("Contract Modification, Demotion & Reassignment"),
            "legal_reference": _(
                "Luxembourg Labour Code (Art. L.121-7): Any substantial modification of an essential clause (salary, hours, role, workplace) to the employee's detriment must follow strict dismissal-notice formalities."
            ),
            "steps": [
                _(
                    "Do not sign an unfavorable contract addendum (avenant) under pressure; ask for reasonable time to review the document in writing."
                ),
                _(
                    "Check whether the change affects an essential element of your contract and whether the statutory notice period or pre-interview rules apply."
                ),
                _(
                    "If an immediate unfavorable change is imposed without following Art. L.121-7, consult a Staff Delegate or union/legal specialist promptly."
                ),
            ],
        },
        "sick_leave": {
            "label": _("Sick Leave, Maternity & Dismissal Protection"),
            "legal_reference": _(
                "Luxembourg Labour Code (Art. L.121-6): Employees are protected against dismissal for up to 26 consecutive weeks of incapacity, provided they notify the employer on day 1 and submit the medical certificate by day 3."
            ),
            "steps": [
                _(
                    "Always notify your employer (or their representative) on the very first day of absence — keep written proof (email, SMS, or witness)."
                ),
                _(
                    "Ensure your employer receives the medical certificate (certificat médical) no later than the 3rd day of incapacity and send the CNS copy."
                ),
                _(
                    "If you receive a dismissal letter after fulfilling both obligations, contest it rapidly with the support of your Staff Delegation or legal advisor."
                ),
            ],
        },
        "delegation": {
            "label": _("Staff Delegation Rights & Collective Representation"),
            "legal_reference": _(
                "Luxembourg Labour Code (Art. L.411-1 & L.414-1): Every company in Luxembourg employing at least 15 workers must establish a Staff Delegation (Délégation du personnel) to safeguard and defend employee interests."
            ),
            "steps": [
                _(
                    "You have the statutory right to contact your Staff Delegation confidentially during working hours to raise individual or collective concerns."
                ),
                _(
                    "If your company has 15+ employees in Luxembourg but no elected delegation, the employer and ITM can be called upon to organize social elections."
                ),
                _(
                    "Staff delegates can accompany you to formal HR meetings, mediate workplace disputes, and refer safety or legal breaches to the ITM."
                ),
            ],
        },
    }

    selected = category_guidance.get(issue_category, category_guidance["dismissal"])

    if urgency == "urgent":
        urgency_label = _(
            "High Priority — Time-sensitive statutory deadlines may apply"
        )
    elif urgency == "active":
        urgency_label = _(
            "Active Workplace Situation — Document facts before your next HR step"
        )
    else:
        urgency_label = _("Preventive Orientation — Understanding your rights early")

    if company_size == "15_plus":
        if has_delegation == "yes":
            delegation_note = _(
                "Your company has 15+ employees and an active Staff Delegation: under Luxembourg law, your delegates can confidentially advise you and accompany you in discussions with management."
            )
        elif has_delegation == "no":
            delegation_note = _(
                "In Luxembourg, companies with 15 or more employees are legally required to have a Staff Delegation (Art. L.411-1). Its absence is an important point to raise with the ITM or a trade union."
            )
        else:
            delegation_note = _(
                "Because your employer has 15+ staff in Luxembourg, a Staff Delegation is mandatory by law. We will help you identify your representation options safely."
            )
    elif company_size == "under_15":
        delegation_note = _(
            "In companies with fewer than 15 employees, there is no mandatory Staff Delegation, but you remain fully protected by the Luxembourg Labour Code and can rely on the ITM, trade unions, or legal counsel."
        )
    else:
        delegation_note = _(
            "Whether your company has a Staff Delegation (mandatory from 15 employees) or not, every employee in Luxembourg is protected by the Code du travail and the ITM."
        )

    return {
        "reference_code": reference_code,
        "category_label": selected["label"],
        "legal_reference": selected["legal_reference"],
        "steps": selected["steps"],
        "urgency_label": urgency_label,
        "delegation_note": delegation_note,
        "contact_email": contact_email,
    }


def home(request):
    """
    Marketing & SEO frontpage for delegations.lu (EN / FR / DE).

    - Redirects authenticated company portal users to their status destination
      on GET (unless ?preview=1 is passed).
    - Handles the confidential Employment-Issue Help Prototype intake form on POST.
    """
    if (
        request.method == "GET"
        and request.user.is_authenticated
        and request.GET.get("preview") != "1"
    ):
        profile, _created = _get_or_create_profile(request.user)
        if profile:
            if profile.is_approved:
                return redirect("delegations:dashboard")
            elif profile.manually_blocked or profile.status == "rejected":
                return redirect("delegations:access_denied")
            elif profile.status == "pending":
                return redirect("delegations:pending_approval")
            elif profile.status == "no_company":
                return redirect("delegations:no_company")

    active_lang = (get_language() or "en")[:2].lower()
    if active_lang not in VALID_PREFERRED_LANGUAGES:
        active_lang = "en"

    form_data = {
        "issue_category": request.GET.get("topic", ""),
        "urgency": "active",
        "company_size": "15_plus",
        "has_delegation": "unsure",
        "situation_summary": "",
        "contact_email": "",
        "preferred_language": active_lang,
        "consent": False,
    }
    if form_data["issue_category"] not in VALID_ISSUE_CATEGORIES:
        form_data["issue_category"] = ""

    form_errors = {}
    intake_result = None

    if request.method == "POST":
        issue_category = (request.POST.get("issue_category") or "").strip()
        urgency = (request.POST.get("urgency") or "").strip()
        company_size = (request.POST.get("company_size") or "").strip()
        has_delegation = (request.POST.get("has_delegation") or "unsure").strip()
        situation_summary = (request.POST.get("situation_summary") or "").strip()
        contact_email = (request.POST.get("contact_email") or "").strip()
        preferred_language = (
            request.POST.get("preferred_language") or active_lang
        ).strip()
        consent = (request.POST.get("consent") or "").lower() in {
            "on",
            "true",
            "1",
            "yes",
        }

        form_data = {
            "issue_category": issue_category,
            "urgency": urgency or "active",
            "company_size": company_size or "15_plus",
            "has_delegation": (
                has_delegation
                if has_delegation in VALID_DELEGATION_STATES
                else "unsure"
            ),
            "situation_summary": situation_summary,
            "contact_email": contact_email,
            "preferred_language": (
                preferred_language
                if preferred_language in VALID_PREFERRED_LANGUAGES
                else active_lang
            ),
            "consent": consent,
        }

        if issue_category not in VALID_ISSUE_CATEGORIES:
            form_errors["issue_category"] = _(
                "Please select the employment issue that best matches your situation."
            )

        if urgency not in VALID_URGENCY_LEVELS:
            form_errors["urgency"] = _("Please indicate how urgent your situation is.")

        if company_size not in VALID_COMPANY_SIZES:
            form_errors["company_size"] = _(
                "Please select your approximate company size in Luxembourg."
            )

        if len(situation_summary) < 15:
            form_errors["situation_summary"] = _(
                "Please share a short summary (at least 15 characters) so we can tailor your orientation."
            )

        if not contact_email:
            form_errors["contact_email"] = _(
                "Please enter a personal email address where we can send your orientation summary."
            )
        else:
            try:
                validate_email(contact_email)
            except ValidationError:
                form_errors["contact_email"] = _(
                    "Please enter a valid email address (for example: name@example.com)."
                )

        if not consent:
            form_errors["consent"] = _(
                "Please confirm that you understand this confidential prototype orientation."
            )

        if not form_errors:
            intake_result = _build_intake_guidance(
                issue_category=issue_category,
                urgency=urgency,
                company_size=company_size,
                has_delegation=form_data["has_delegation"],
                contact_email=contact_email,
            )
            logger.info(
                "Delegations.lu employment intake prototype submitted: ref=%s category=%s urgency=%s size=%s lang=%s",
                intake_result["reference_code"],
                issue_category,
                urgency,
                company_size,
                form_data["preferred_language"],
            )

    context = {
        "form_data": form_data,
        "form_errors": form_errors,
        "intake_result": intake_result,
        "active_lang": active_lang,
    }
    return render(request, "delegations/home.html", context)


@login_required
def dashboard(request):
    """
    Main dashboard for approved delegation users.
    Only accessible to users with approved status.
    """
    profile, created = _get_or_create_profile(request.user)

    if not profile:
        messages.warning(
            request, "Please sign in with Microsoft to access this platform."
        )
        return redirect("delegations:home")

    if created:
        messages.info(request, "Your profile has been created.")

    # Check access
    if not profile.is_approved:
        if profile.manually_blocked or profile.status == "rejected":
            return redirect("delegations:access_denied")
        elif profile.status == "pending":
            return redirect("delegations:pending_approval")
        elif profile.status == "no_company":
            return redirect("delegations:no_company")

    context = {
        "profile": profile,
        "company": profile.company,
    }
    return render(request, "delegations/dashboard.html", context)


@login_required
def profile_view(request):
    """
    User profile page showing Microsoft account details.
    """
    profile, _ = _get_or_create_profile(request.user)

    if not profile:
        messages.warning(request, "Your profile is being set up. Please wait.")
        return redirect("delegations:home")

    # Only approved users can view profile
    if not profile.is_approved:
        return redirect("delegations:dashboard")

    context = {
        "profile": profile,
        "user": request.user,
    }
    return render(request, "delegations/profile.html", context)


@login_required
def pending_approval(request):
    """
    Page shown to users waiting for admin approval.
    """
    profile, _ = _get_or_create_profile(request.user)

    if not profile:
        return redirect("delegations:home")

    # If already approved, redirect to dashboard
    if profile.is_approved:
        return redirect("delegations:dashboard")

    # If rejected or blocked, show access denied
    if profile.status == "rejected" or profile.manually_blocked:
        return redirect("delegations:access_denied")

    context = {
        "profile": profile,
    }
    return render(request, "delegations/pending_approval.html", context)


@login_required
def no_company(request):
    """
    Page shown to users whose email domain doesn't match any company.
    """
    profile, _ = _get_or_create_profile(request.user)

    if not profile:
        return redirect("delegations:home")

    # If approved, redirect to dashboard
    if profile.is_approved:
        return redirect("delegations:dashboard")

    if profile.manually_blocked or profile.status == "rejected":
        return redirect("delegations:access_denied")

    # If they have a company now, check other status
    if profile.company and profile.status == "pending":
        return redirect("delegations:pending_approval")

    context = {
        "profile": profile,
        "user_email": request.user.email,
    }
    return render(request, "delegations/no_company.html", context)


@login_required
def access_denied(request):
    """
    Page shown to rejected or manually blocked users (e.g., management roles).
    """
    profile, _ = _get_or_create_profile(request.user)

    if not profile:
        return redirect("delegations:home")

    # If approved, redirect to dashboard
    if profile.is_approved:
        return redirect("delegations:dashboard")

    reason = profile.rejection_reason
    if not reason and profile.manually_blocked:
        reason = "Your account has been blocked by an administrator."

    context = {
        "profile": profile,
        "reason": reason or "Your account does not have access to this platform.",
    }
    return render(request, "delegations/access_denied.html", context)
