# Bug Fix: Inactive Coaches Could Not Create Dating Profiles

## Problem

When a coach was deactivated via admin (`is_active=False`), they still couldn't create or edit dating profiles. The system was blocking them with the message:

```
Coaches cannot create dating profiles. You have a coach account.
```

## Root Cause

The views were checking if a `CrushCoach` record **exists**, but not checking if it's **active**:

```python
# OLD CODE - BUGGY
try:
    coach = CrushCoach.objects.get(user=request.user)  # ❌ Doesn't check is_active
    messages.error(request, 'Coaches cannot create dating profiles.')
    return redirect('crush_lu:coach_dashboard')
except CrushCoach.DoesNotExist:
    pass
```

This meant:
- Active coach (`is_active=True`) → Correctly blocked ✅
- Inactive coach (`is_active=False`) → Incorrectly blocked ❌

## Solution

Added `is_active=True` to the query in all affected views:

```python
# NEW CODE - FIXED
try:
    coach = CrushCoach.objects.get(user=request.user, is_active=True)  # ✅ Checks is_active
    messages.error(request, 'Coaches cannot create dating profiles. You have an active coach account.')
    return redirect('crush_lu:coach_dashboard')
except CrushCoach.DoesNotExist:
    # Either no coach record, or coach is inactive - allow profile creation
    pass
```

## Files Modified

| File | Lines | Change |
|------|-------|--------|
| [crush_lu/views.py](crush_lu/views.py#L103) | 103 | Added `is_active=True` to `create_profile` view |
| [crush_lu/views.py](crush_lu/views.py#L142) | 142 | Added `is_active=True` to `edit_profile` view |
| [crush_lu/views.py](crush_lu/views.py#L190) | 190 | Added `is_active=True` to `dashboard` view |

## Expected Behavior Now

### Active Coach (`is_active=True`)
- ❌ Cannot create dating profile
- ❌ Cannot edit dating profile
- ✅ Redirected to coach dashboard
- ✅ Sees coach navigation menu

### Inactive Coach (`is_active=False`)
- ✅ Can create dating profile
- ✅ Can edit dating profile (if exists)
- ✅ Sees dating user navigation menu
- ✅ Can attend events and make connections

### Regular User (No coach record)
- ✅ Can create dating profile
- ✅ Can edit dating profile
- ✅ Normal dating user experience

## Testing Steps

### Test 1: Deactivate Coach and Create Profile

1. Go to Admin → Crush Coaches
2. Find coach user (e.g., Thomas Weber)
3. Action: "Deactivate coach role (allows them to date)"
4. Logout and login as that user
5. Navigate to: Create Profile
6. **Expected:** Profile creation form appears ✅
7. Fill and submit profile
8. **Expected:** Profile submitted for review ✅

### Test 2: Reactivate Coach

1. Go to Admin → Crush Coaches
2. Find same coach user
3. Action: "Activate selected coaches"
4. Logout and login as that user
5. Try to access: Create Profile or Edit Profile
6. **Expected:** Redirected to coach dashboard with error ✅

### Test 3: Navigation Menu

**When Coach is Active:**
- **Expected:** Coach Dashboard, My Sessions, Events ✅
- **Not shown:** My Connections, Edit Profile ✅

**When Coach is Inactive:**
- **Expected:** Dashboard, Events, My Connections ✅
- **Shown in dropdown:** Edit Profile OR Create Profile ✅

## Related Documentation

- [ROLE_CONVERSION_GUIDE.md](ROLE_CONVERSION_GUIDE.md) - How to convert between roles
- [CRUSH_ARCHITECTURE.md](CRUSH_ARCHITECTURE.md) - User role architecture explanation
- [NAVIGATION_GUIDE.md](NAVIGATION_GUIDE.md) - Navigation menu system

## Additional Notes

This fix enables the **role switching** functionality documented in the Role Conversion Guide. Coaches can now:

1. Be deactivated by admin
2. Create/use dating profiles
3. Be reactivated as coaches later
4. Switch back to coaching role

The `CrushCoach` record is **preserved** when deactivated (just `is_active=False`), so:
- ✅ Coaching history is maintained
- ✅ Session notes are preserved
- ✅ Can be reactivated anytime
- ✅ No data loss

## Version History

- **2025-01-XX** - Initial bug fix for inactive coach blocking
