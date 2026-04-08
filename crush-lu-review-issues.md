# Crush.lu App Code Review - Issues

The following issues were identified during a thorough code review of the Crush.lu Django app.
Each issue includes priority, category, affected files, and suggested fixes.

---

## Issue 1: Race condition in vote submission API

**Priority:** P1-High
**Labels:** `bug`, `security`
**File:** `crush_lu/api_views.py` lines 163-186

### Description

The `submit_vote_api` view has a race condition that can lead to duplicate votes and corrupted vote counts:

1. No `transaction.atomic()` wrapping the vote creation/update
2. `voting_session.total_votes += 1; voting_session.save()` uses a non-atomic increment (should use `F('total_votes') + 1`)
3. Two concurrent requests could both see `existing_vote=None` and create duplicate votes
4. The check-then-create pattern is not protected by `select_for_update()`

```python
# Current (broken):
existing_vote = EventActivityVote.objects.filter(event=event, user=request.user).first()
if existing_vote:
    existing_vote.selected_option = selected_option
    existing_vote.save()
else:
    EventActivityVote.objects.create(event=event, user=request.user, selected_option=selected_option)
    voting_session.total_votes += 1  # NOT atomic!
    voting_session.save()
```

### Suggested Fix

Wrap in `transaction.atomic()`, use `F('total_votes') + 1` for the counter, and use `update_or_create` or add a unique constraint on `(event, user)`.

**Reference:** The event check-in view (`views_checkin.py:136`) already does this correctly with `transaction.atomic()` + `select_for_update()`.

---

## Issue 2: Excessive `@csrf_exempt` on authenticated API endpoints

**Priority:** P1-High
**Labels:** `security`
**Files:**
- `crush_lu/api_push.py` - 10 endpoints with `@csrf_exempt`
- `crush_lu/api_coach_push.py` - 5 endpoints with `@csrf_exempt`
- `crush_lu/views_account.py:543` - `api_update_email_preference`

### Description

`@login_required` does NOT protect against CSRF attacks. A malicious website can forge POST requests that include the user's session cookie, allowing an attacker to:
- Subscribe/unsubscribe push notifications on behalf of a user
- Modify email preferences without the user's knowledge
- Manipulate coach push subscription settings

The comment in `api_push.py:68` says *"Push subscriptions use their own authentication via login_required"* but this is incorrect - `login_required` only checks if a session cookie is present, not that the request originated from the legitimate site.

### Suggested Fix

Remove `@csrf_exempt` and include the CSRF token in JavaScript `fetch()` calls. The HTMX CSRF setup in `base.html` already handles this for HTMX requests - extend the same pattern to vanilla `fetch()` calls. Alternatively, use DRF's `SessionAuthentication` which enforces CSRF automatically.

---

## Issue 3: Photo upload fallback bypasses validation

**Priority:** P1-High
**Labels:** `security`, `bug`
**File:** `crush_lu/views_profile.py` lines 844-846

### Description

If the form field validation path is not reached (e.g., the field name doesn't match), the code falls through to a "fallback" that saves the uploaded file with zero validation:

```python
# Fallback - save without validation (shouldn't happen)
setattr(profile, photo_field_name, photo_file)
profile.save(update_fields=[photo_field_name])
```

The comment itself acknowledges this "shouldn't happen," yet the code allows arbitrary file uploads if this path is triggered. This could allow malicious files to be stored in the Azure Blob container.

### Suggested Fix

Replace the fallback with an error response:

```python
# If validation path was not reached, return error
logger.error(f"Photo field {photo_field_name} not found in form")
return render(request, 'crush_lu/partials/photo_card.html', {
    'slot': slot,
    'photo': None,
    'is_main': slot == 1,
    'error': 'Upload failed. Please try again.',
})
```

---

## Issue 4: Misleading vote status check logic

**Priority:** P2-Medium
**Labels:** `bug`
**File:** `crush_lu/api_views.py` lines 100-115

### Description

Two sequential status checks create contradictory logic and a misleading error message:

```python
if user_registration.status not in ['confirmed', 'attended']:
    return JsonResponse({'error': 'Only confirmed attendees can vote'}, status=403)
if user_registration.status == 'confirmed':
    return JsonResponse({'error': 'You must check in at the event before you can vote'}, status=403)
```

- The first check allows both `confirmed` and `attended`
- The second check immediately rejects `confirmed`
- Net effect: only `attended` can vote, but the first error message says "confirmed attendees can vote"
- The first check is redundant

### Suggested Fix

Simplify to a single, clear check:

```python
if user_registration.status != 'attended':
    return JsonResponse({
        'success': False,
        'error': 'You must check in at the event before you can vote'
    }, status=403)
```

---

## Issue 5: Consider mandatory email verification for Crush.lu

**Priority:** P2-Medium
**Labels:** `security`, `enhancement`
**File:** `azureproject/settings.py` line 399

### Description

`ACCOUNT_EMAIL_VERIFICATION = "optional"` allows users to create accounts and interact on the platform without ever verifying their email address. For a dating platform, this lowers the barrier for:
- Bot accounts
- Fake/throwaway accounts
- Users with typos in their email (who can never receive notifications)

### Suggested Fix

Consider `ACCOUNT_EMAIL_VERIFICATION = "mandatory"` for the Crush.lu domain. This could be implemented via a custom allauth adapter that checks the domain and applies different verification requirements.

---

## Issue 6: N+1 query pattern in voting results API

**Priority:** P2-Medium
**Labels:** `performance`
**File:** `crush_lu/api_views.py` lines 244-267

### Description

The `voting_results_api` loops through all `GlobalActivityOption` objects and executes a separate `COUNT` query for each one:

```python
for option in GlobalActivityOption.objects.filter(is_active=True).order_by('activity_type', 'sort_order'):
    vote_count = EventActivityVote.objects.filter(event=event, selected_option=option).count()
```

With N activity options, this produces N+1 database queries. During live event voting with many concurrent users, this can cause unnecessary load.

### Suggested Fix

Use Django's annotation to do it in a single query:

```python
from django.db.models import Count, Q

options = GlobalActivityOption.objects.filter(
    is_active=True
).annotate(
    vote_count=Count(
        'eventactivityvote',
        filter=Q(eventactivityvote__event=event)
    )
).order_by('activity_type', 'sort_order')
```

---

## Issue 7: `SOCIALACCOUNT_LOGIN_ON_GET = True` enables login CSRF

**Priority:** P2-Medium
**Labels:** `security`
**File:** `azureproject/settings.py` lines 299 and 406 (duplicated)

### Description

Two problems:

1. **Login CSRF vulnerability:** `SOCIALACCOUNT_LOGIN_ON_GET = True` allows initiating OAuth flows via GET request. An attacker can force-login a victim to the attacker's OAuth account by embedding `<img src="https://crush.lu/accounts/google/login/">` on a malicious page. The django-allauth documentation explicitly warns about this setting.

2. **Duplicate setting:** The setting is defined twice (lines 299 and 406), which is confusing and could lead to maintenance errors.

### Suggested Fix

- Set `SOCIALACCOUNT_LOGIN_ON_GET = False` (users will see a POST confirmation page before OAuth redirect)
- Remove the duplicate definition at line 299

---

## Issue 8: Missing server-side MIME type validation on photo uploads

**Priority:** P3-Low
**Labels:** `security`, `enhancement`
**Files:** `crush_lu/views_profile.py`, `crush_lu/forms.py`

### Description

The `python-magic` library is included in `requirements.txt` but is not used for upload validation. Currently:
- File extension is the only initial check
- A crafted file with a `.jpg` extension could bypass initial checks
- Pillow's `Image.open()` would likely fail during processing, but error handling may not consistently reject the file

### Suggested Fix

Add MIME type validation using `python-magic` before processing:

```python
import magic

def validate_image_mime(file):
    mime = magic.from_buffer(file.read(2048), mime=True)
    file.seek(0)
    if mime not in ('image/jpeg', 'image/png', 'image/webp', 'image/gif'):
        raise ValidationError('Unsupported file type')
```

---

## Issue 9: Overly broad exception handling in critical paths

**Priority:** P3-Low
**Labels:** `code-quality`
**Files:**
- `crush_lu/signals.py` - Multiple signal handlers
- `crush_lu/middleware.py` line 124
- `crush_lu/views_profile.py` line 855

### Description

Many code paths use bare `except Exception` that silently swallow all errors with just a log warning:

```python
except Exception as e:
    logger.warning(f"Error updating user activity: {e}")
```

While this prevents crashes, it can silently hide:
- Data corruption issues
- Permission errors
- Logic bugs
- Security-related failures

In production, these logged warnings may go unnoticed while the underlying issue causes data inconsistency.

### Suggested Fix

Narrow exception types where possible:
- `IOError`/`OSError` for file operations
- `DatabaseError` for DB operations
- `(ConnectionError, TimeoutError)` for network calls

For critical paths, consider re-raising unexpected exceptions after logging, or at minimum logging at ERROR level instead of WARNING.

---

## Issue 10: `admin_views.py` is 80KB monolith - needs refactoring

**Priority:** P3-Low
**Labels:** `maintenance`, `code-quality`
**File:** `crush_lu/admin_views.py` (79,709 bytes)

### Description

This single file is massive at ~80KB while the regular views have already been properly split into modular files (`views_account.py`, `views_events.py`, `views_coach.py`, `views_profile.py`, etc.). The admin views haven't received the same treatment.

This makes the file:
- Difficult to navigate and review
- Hard to test individual sections
- Prone to merge conflicts when multiple developers work on admin features

### Suggested Fix

Split into modules following the same pattern as the regular views:
- `admin_views_profiles.py` - Profile management admin views
- `admin_views_events.py` - Event management admin views
- `admin_views_analytics.py` - Analytics/dashboard views
- `admin_views_newsletter.py` - Newsletter admin views
- etc.

Keep `admin_views.py` as a re-export file (like `models/__init__.py`) for backwards compatibility.
