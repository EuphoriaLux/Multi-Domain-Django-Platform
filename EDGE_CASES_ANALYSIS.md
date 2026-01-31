# Edge Cases Analysis - Crush.lu Codebase

This document identifies edge cases and potential improvements in the crush.lu platform logic.

---

## 1. Event System Edge Cases

### 1.1 Event Registration Race Conditions

**File:** `crush_lu/models/events.py:135-142`

**Issue:** The `is_registration_open` property checks capacity but doesn't use database-level locking.

```python
@property
def is_registration_open(self):
    return (
        self.is_published
        and not self.is_cancelled
        and now < self.registration_deadline
        and self.get_confirmed_count() < self.max_participants  # Race condition here
    )
```

**Edge Case:** Two users registering simultaneously when only 1 spot remains could both see the event as "open" and both complete registration, exceeding `max_participants`.

**Recommendation:** Use `select_for_update()` or database-level constraints when creating registrations to prevent over-booking.

---

### 1.2 Voting System - Type Mismatch in API

**File:** `crush_lu/api_views.py:146`

**Issue:** The `submit_vote_api` function queries `EventActivityOption` but the model uses `GlobalActivityOption` for votes.

```python
selected_option = EventActivityOption.objects.get(id=option_id, event=event)
```

However, `EventActivityVote` stores a foreign key to `GlobalActivityOption`:

```python
# models/events.py:449
selected_option = models.ForeignKey(GlobalActivityOption, on_delete=models.CASCADE)
```

**Edge Case:** Submitting a vote with an `EventActivityOption` ID would fail silently or cause data inconsistency since the vote model expects a `GlobalActivityOption`.

**Recommendation:** Verify the correct model is being used in the voting API, or add explicit validation.

---

### 1.3 Vote Count Not Atomic

**File:** `crush_lu/api_views.py:159-184`

**Issue:** Vote count updates are not atomic operations:

```python
old_option.vote_count -= 1
old_option.save()
# ...
selected_option.vote_count += 1
selected_option.save()
```

**Edge Case:** If multiple users vote concurrently, vote counts could become inaccurate due to lost updates (read-modify-write race condition).

**Recommendation:** Use `F()` expressions for atomic updates:
```python
EventActivityOption.objects.filter(pk=old_option.pk).update(vote_count=F('vote_count') - 1)
```

---

### 1.4 Voting Session Winner Tie Handling

**File:** `crush_lu/models/events.py:577-603`

**Issue:** The `calculate_winner()` method uses `.order_by('-vote_count')` to determine the winner, but doesn't handle ties:

```python
if presentation_votes:
    winner_id = presentation_votes[0]['selected_option']  # Just takes first
```

**Edge Case:** If two options have the same vote count, the winner is arbitrary (based on database ordering).

**Recommendation:** Add explicit tie-breaking logic (e.g., random selection, or flagging as tie for coach review).

---

### 1.5 Missing Voting Session Attribute

**File:** `crush_lu/api_views.py:260`

**Issue:** The code references `voting_session.winning_option` but this attribute doesn't exist:

```python
'winner_id': voting_session.winning_option.id if voting_session.winning_option else None,
```

The model has `winning_presentation_style` and `winning_speed_dating_twist`, but no `winning_option`.

**Edge Case:** This will raise an `AttributeError` when trying to get voting results.

**Recommendation:** Fix to use the correct attribute names or add a `winning_option` property.

---

## 2. Journey System Edge Cases

### 2.1 Multiple Journey Progress Records

**File:** `crush_lu/api_journey.py:70-78`

**Issue:** The code uses `.filter().first()` to get journey progress:

```python
journey_progress = JourneyProgress.objects.filter(
    user=request.user
).select_related('journey').first()
```

**Edge Case:** If a user has multiple journeys (e.g., from multiple gifts), this only returns the first one (by ID), which may not be the intended active journey.

**Recommendation:** Add explicit selection logic (e.g., most recent, or specific journey_id parameter).

---

### 2.2 Puzzle Piece Hardcoded to 16

**File:** `crush_lu/api_journey.py:572`

**Issue:** The completion check hardcodes 16 pieces:

```python
if len(reward_progress.unlocked_pieces) == 16:
    reward_progress.is_completed = True
```

But `JourneyReward.puzzle_pieces` can be configured differently:

```python
puzzle_pieces = models.IntegerField(default=16, help_text=_("Number of jigsaw pieces (4x4=16, 5x4=20, 6x5=30)"))
```

**Edge Case:** A 20-piece or 30-piece puzzle would never be marked as "completed" because it checks for exactly 16 pieces.

**Recommendation:** Use the actual reward's `puzzle_pieces` value:
```python
if len(reward_progress.unlocked_pieces) == reward.puzzle_pieces:
```

---

### 2.3 Duplicate Unlocked Pieces

**File:** `crush_lu/api_journey.py:568`

**Issue:** The code appends pieces to a list without checking if already in list before spending points:

```python
reward_progress.unlocked_pieces.append(piece_index)
```

The check exists earlier (line 547), but if the check and append aren't atomic, a race condition could cause duplicate entries and incorrect completion calculation.

**Edge Case:** Rapid double-clicks could spend points twice for the same piece if the database isn't updated between requests.

**Recommendation:** Use `select_for_update()` when modifying reward progress, or check-and-append atomically.

---

### 2.4 Challenge Validation Not Bound to User's Journey

**File:** `crush_lu/api_journey.py:47-53`

**Issue:** When submitting a challenge, only the challenge existence is verified, not whether it belongs to the user's journey:

```python
challenge = JourneyChallenge.objects.get(id=challenge_id)  # No user journey check
```

Contrast with `unlock_hint` which properly validates:

```python
challenge = JourneyChallenge.objects.get(
    id=challenge_id,
    chapter__journey=journey_progress.journey  # SECURITY: Must be user's journey
)
```

**Edge Case:** A user could potentially submit answers to challenges in other users' journeys.

**Recommendation:** Add journey ownership validation in `submit_challenge`.

---

### 2.5 Hints Stored in Both Database and Session

**File:** `crush_lu/api_journey.py:166-167`

**Issue:** Hints are tracked in the database (ChallengeAttempt.hints_used) but also cleared from session:

```python
if f'hints_used_{challenge_id}' in request.session:
    del request.session[f'hints_used_{challenge_id}']
```

**Edge Case:** Mixed state between session and database could cause hints to be "forgotten" or inconsistent behavior across different browsers/devices.

**Recommendation:** Remove session-based hint tracking entirely since database is the source of truth.

---

## 3. Connection System Edge Cases

### 3.1 Self-Connection Not Prevented

**File:** `crush_lu/models/connections.py:64-68`

**Issue:** The model doesn't prevent a user from creating a connection request to themselves:

```python
requester = models.ForeignKey(User, on_delete=models.CASCADE, related_name='connection_requests_sent')
recipient = models.ForeignKey(User, on_delete=models.CASCADE, related_name='connection_requests_received')
```

**Edge Case:** A user could potentially create a connection request to themselves via API manipulation.

**Recommendation:** Add a model constraint or validation:
```python
class Meta:
    constraints = [
        models.CheckConstraint(
            check=~models.Q(requester=models.F('recipient')),
            name='no_self_connection'
        )
    ]
```

---

### 3.2 Coach Assignment Fallback

**File:** `crush_lu/models/connections.py:173-174`

**Issue:** If no approved submission coach is found, it falls back to "any active coach":

```python
self.assigned_coach = CrushCoach.objects.filter(is_active=True).first()
```

**Edge Case:** If there are no active coaches, `assigned_coach` will be `None`, which could cause issues in coach facilitation workflows.

**Recommendation:** Add handling for no available coaches (queue for later assignment, or alert administrators).

---

## 4. Profile Management Edge Cases

### 4.1 Phone Number Change Without Re-verification

**File:** `crush_lu/views_profile.py:115-120`

**Issue:** When phone number changes, verification is reset but the profile can still proceed:

```python
if profile.phone_number and new_phone_number != profile.phone_number:
    profile.phone_verified = False
    # ... but save continues
```

Step 2 requires verification (`crush_lu/views_profile.py:159-165`), but Step 1 doesn't block saving with a changed number.

**Edge Case:** A user could change their phone number in Step 1, complete Step 1, then be blocked at Step 2, losing their work.

**Recommendation:** Either enforce verification before Step 1 completion, or warn the user their progress will be blocked.

---

### 4.2 Photo Upload Creates Profile Implicitly

**File:** `crush_lu/views_profile.py:458`

**Issue:** Photo upload creates a profile if it doesn't exist:

```python
profile, created = CrushProfile.objects.get_or_create(user=request.user)
```

**Edge Case:** A user could upload photos before completing Step 1, bypassing the intended flow and potentially bypassing coach checks for active coaches.

**Recommendation:** Require profile to exist before allowing photo uploads, or create profile with specific initial state.

---

### 4.3 Profile Submission Resubmission Not Handled

**File:** `crush_lu/views_profile.py:260`

**Issue:** `get_or_create` for ProfileSubmission doesn't update if submission already exists:

```python
submission, created = ProfileSubmission.objects.get_or_create(
    profile=profile,
    defaults={'status': 'pending'}
)
```

**Edge Case:** If a user edits their profile after initial submission and resubmits, the existing submission keeps its old status (could be 'approved' or 'rejected').

**Recommendation:** Either create a new submission for re-reviews, or reset status on resubmission.

---

## 5. Referral System Edge Cases

### 5.1 Self-Referral Not Prevented

**File:** `crush_lu/referrals.py:81-120`

**Issue:** No check prevents a user from using their own referral code:

```python
def apply_referral_to_user(request, user):
    code = request.session.pop('referral_code', None)
    # ... applies referral without checking if referrer == user
```

**Edge Case:** A user could sign up using their own referral code to earn points.

**Recommendation:** Add validation:
```python
if referral.referrer.user == user:
    logger.warning("Self-referral attempted by user %s", user.id)
    return None
```

---

### 5.2 Double Reward Application

**File:** `crush_lu/referrals.py:273-274`

**Issue:** The profile approval bonus check relies on arithmetic comparison:

```python
if attribution.reward_points >= signup_points + bonus_points:
    return None  # Bonus already applied
```

**Edge Case:** If `REFERRAL_POINTS_PER_SIGNUP` or `REFERRAL_POINTS_PER_PROFILE_APPROVED` settings change, the comparison could give incorrect results for historical attributions.

**Recommendation:** Add a separate flag for bonus_applied or track reward types in a separate model.

---

### 5.3 Tier Calculation Order Dependency

**File:** `crush_lu/referrals.py:132`

**Issue:** Tier calculation sorts by threshold in reverse, but relies on dictionary order:

```python
for tier, threshold in sorted(thresholds.items(), key=lambda x: x[1], reverse=True):
```

**Edge Case:** If two tiers have the same threshold value, the selected tier is arbitrary.

**Recommendation:** Define tier order explicitly or use ordered thresholds.

---

### 5.4 Points Redemption Without Reward Tracking

**File:** `crush_lu/api_referral.py:192-200`

**Issue:** Points redemption only logs the action, doesn't create a persistent record:

```python
# Note: In a full implementation, you'd create a PointsRedemption model
# to track all redemptions. For now, we'll log it.
logger.info(...)
```

**Edge Case:** Users cannot see their redemption history, and disputes cannot be resolved without log access.

**Recommendation:** Create a `PointsRedemption` model to track all redemptions.

---

## 6. Gift and Advent Calendar Edge Cases

### 6.1 Gift Claim Maximum Attempts

**File:** `crush_lu/models/journey_gift.py:343-345`

**Issue:** `is_claimable` checks `claim_attempts < MAX_CLAIM_ATTEMPTS`:

```python
self.claim_attempts < self.MAX_CLAIM_ATTEMPTS
```

But `MAX_CLAIM_ATTEMPTS = 3`, so attempts 0, 1, 2 are allowed. After 3 attempts, it's locked.

**Edge Case:** A transient error on the third attempt permanently locks the gift, even if the error is recoverable.

**Recommendation:** Consider a time-based reset of attempts, or allow more attempts for transient errors.

---

### 6.2 Gift Claimed by Non-Intended Recipient

**File:** `crush_lu/models/journey_gift.py:398-549`

**Issue:** The `claim()` method doesn't verify the claiming user matches `recipient_email`:

```python
def claim(self, user):
    if not self.is_claimable:
        raise ValueError("This gift cannot be claimed")
    # ... no check that user.email == self.recipient_email
```

**Edge Case:** Anyone with access to the gift code/QR can claim the gift, even if `recipient_email` was specified.

**Recommendation:** Either enforce email matching or document that `recipient_email` is purely informational.

---

### 6.3 Advent Calendar Timezone Edge Cases

**File:** `crush_lu/models/advent.py:97-124`

**Issue:** The `is_door_available()` method handles timezones but doesn't account for DST transitions:

```python
tz = pytz.timezone(self.timezone_name)
now = timezone.now().astimezone(tz)
```

**Edge Case:** During DST transitions, a door might be available/unavailable for an extra hour, or the same door might be accessible twice.

**Recommendation:** Use consistent UTC-based date boundaries, or explicitly handle DST transitions.

---

### 6.4 Advent Door Accumulation Logic Bug

**File:** `crush_lu/models/advent.py:121-124`

**Issue:** The accumulation check has redundant logic:

```python
if self.allow_catch_up:
    return door_number <= current_date.day and door_number <= 24
else:
    return door_number == current_date.day and door_number <= 24
```

**Edge Case:** On December 25-31, `current_date.day` > 24, so:
- With `allow_catch_up=True`: All doors 1-24 are available (correct)
- With `allow_catch_up=False`: Door 25-31 would be checked, which don't exist (returns False, but logic is unclear)

**Recommendation:** Add explicit handling for December 25-31.

---

### 6.5 QR Token Reuse After Expiration

**File:** `crush_lu/models/advent.py:639-644`

**Issue:** `redeem()` checks validity but doesn't prevent race conditions:

```python
def redeem(self):
    if self.is_valid():
        self.is_used = True
        self.used_at = timezone.now()
        self.save()
```

**Edge Case:** Two simultaneous redemption attempts could both see `is_valid() = True` and both succeed.

**Recommendation:** Use `select_for_update()` or add a unique constraint on (token, is_used=False).

---

## 7. Event Voting System Edge Cases

### 7.1 Voting Category Constraint Missing

**File:** `crush_lu/models/events.py:452-455`

**Issue:** The `unique_together` constraint allows only one vote per option per user:

```python
unique_together = ('event', 'user', 'selected_option')
```

But the comment says users should vote once per category:
```python
# Each user can vote once PER CATEGORY (presentation_style AND speed_dating_twist)
```

**Edge Case:** A user could vote for multiple options in the same category.

**Recommendation:** Change constraint to `('event', 'user', 'selected_option__activity_type')` or add application-level validation.

---

## 8. Security Considerations

### 8.1 Profile Photo Slot Validation

**File:** `crush_lu/views_profile.py:447-453`

**Issue:** Photo slot validation returns an error template instead of rejecting:

```python
if slot not in [1, 2, 3]:
    return render(request, 'crush_lu/partials/photo_card.html', {
        'error': 'Invalid photo slot',
    })
```

**Edge Case:** An attacker could pass arbitrary slot values and receive error messages that might leak information.

**Recommendation:** Return HTTP 400 for invalid slots instead of rendering a template.

---

### 8.2 Invitation Code Brute Force

**File:** `crush_lu/models/events.py:278`

**Issue:** Invitation codes are UUIDs (secure), but there's no rate limiting on invitation landing page.

**Edge Case:** An attacker could brute-force invitation codes to access private events.

**Recommendation:** Add rate limiting to invitation landing pages.

---

## 9. Data Integrity Issues

### 9.1 Orphaned Journey Progress

**Issue:** If a journey is deleted but progress records remain, users might see inconsistent state.

**Recommendation:** Ensure CASCADE deletes work correctly, or add cleanup jobs.

---

### 9.2 Stale Vote Counts

**Issue:** `EventActivityOption.vote_count` is denormalized and could drift from actual vote count.

**Recommendation:** Add periodic reconciliation job or remove denormalized field in favor of aggregated queries.

---

## Summary

| Category | Critical | High | Medium |
|----------|----------|------|--------|
| Event System | 1 | 3 | 1 |
| Journey System | 1 | 3 | 1 |
| Connection System | 0 | 1 | 1 |
| Profile Management | 0 | 2 | 1 |
| Referral System | 1 | 2 | 1 |
| Gift/Advent | 0 | 2 | 3 |
| Security | 0 | 2 | 0 |
| **Total** | **3** | **15** | **8** |

### Priority Recommendations

**Critical (Fix Immediately):**
1. Event registration race condition
2. Self-referral prevention
3. Challenge validation for journey ownership

**High Priority:**
1. Voting API model mismatch
2. Atomic vote count updates
3. Puzzle completion hardcoded value
4. Points redemption tracking
5. Gift claim recipient validation

---

*Generated on: 2026-01-31*
*Analyzed codebase: crush_lu*
