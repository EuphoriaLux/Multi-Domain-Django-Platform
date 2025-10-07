# Crush.lu Role Conversion Guide

## Overview

I've added **Admin Actions** to easily convert between Crush Profiles (dating users) and Crush Coaches.

---

## Visual Flow Diagram

```
┌──────────────┐
│  Django User │  ← Everyone starts here (signup)
└──────┬───────┘
       │
       ├─────────────────────────────────────────────┐
       │                                             │
       ↓                                             ↓
┌──────────────────┐                      ┌──────────────────┐
│  CrushProfile    │ ←──── Convert ────→ │   CrushCoach     │
│  (Dating User)   │                      │   (Coach)        │
└──────────────────┘                      └──────────────────┘
│                                                           │
│ • Attends events                          • Reviews profiles │
│ • Makes connections                       • Facilitates intros │
│ • Gets matched                            • Provides guidance │
└──────────────────────────────────────────────────────────┘
```

---

## How to Convert Roles in Django Admin

### **Option 1: Promote Dating User → Coach**

1. Go to **Django Admin** → **Crush Profiles**
2. Select the profile(s) you want to promote
3. In the "Action" dropdown, select: **"Promote selected profiles to Crush Coach role"**
4. Click **"Go"**

**What happens:**
- ✅ Creates a `CrushCoach` record for that user
- ✅ Transfers their bio to the coach profile
- ✅ Deactivates their dating profile (they won't appear in events)
- ✅ Sets `max_active_reviews=10`

**Result:**
- User can now access Coach Dashboard
- User can review profile submissions
- User CANNOT create/use dating profile (dating profile is deactivated)

---

### **Option 2: Demote Coach → Allow Dating**

1. Go to **Django Admin** → **Crush Coaches**
2. Select the coach(es) you want to convert
3. In the "Action" dropdown, select: **"Deactivate coach role (allows them to date)"**
4. Click **"Go"**

**What happens:**
- ✅ Deactivates the `CrushCoach` record (`is_active=False`)
- ✅ User can now create a dating profile

**Result:**
- User can no longer review profiles
- User can create/reactivate their dating profile
- Coach record is preserved (can be reactivated later)

---

## Admin List View Columns

### **CrushCoach Admin**
| Column | Description |
|--------|-------------|
| User | Username/email |
| Specializations | Coach expertise |
| Is Active | ✅/❌ Can review profiles |
| Max Active Reviews | Review capacity |
| Created At | When became coach |
| **Has Dating Profile** | ✅ if user also has CrushProfile |

### **CrushProfile Admin**
| Column | Description |
|--------|-------------|
| User | Username/email |
| Age | Calculated from DOB |
| Gender | M/F/NB/O/P |
| Location | City in Luxembourg |
| Is Approved | ✅ Coach approved |
| Is Active | ✅ Can attend events |
| Created At | Profile creation date |
| **Is Coach** | ✅ if user is also a coach |

---

## Current Business Rules

### **Mutual Exclusivity (Current Default)**

A user can have **BOTH** `CrushProfile` AND `CrushCoach` records, but:

1. **If coach is active** (`is_active=True`):
   - ❌ Cannot create new dating profile ([views.py:100-106](views.py#L100-106))
   - ❌ Redirected to coach dashboard ([views.py:172-177](views.py#L172-177))

2. **If dating profile is active**:
   - ✅ Can attend events
   - ✅ Can make connections
   - ❌ Cannot access coach dashboard

3. **To switch roles**:
   - Deactivate one role to activate the other
   - Admin uses the actions above

---

## Allowing Dual Roles (Optional)

If you want users to be BOTH coach AND dating user simultaneously:

### **Changes Needed:**

1. **Remove mutual exclusivity check** in [crush_lu/views.py:100-106](crush_lu/views.py#L100-106):
   ```python
   # Comment out or remove this:
   try:
       coach = CrushCoach.objects.get(user=request.user)
       messages.error(request, 'Coaches cannot create dating profiles.')
       return redirect('crush_lu:coach_dashboard')
   except CrushCoach.DoesNotExist:
       pass
   ```

2. **Update dashboard logic** in [crush_lu/views.py:172-177](crush_lu/views.py#L172-177):
   ```python
   # Instead of redirecting, show both sections:
   is_coach = hasattr(request.user, 'crushcoach')
   context = {
       'profile': profile,
       'is_coach': is_coach,
       # ... other context
   }
   ```

3. **Update admin action** in [crush_lu/admin.py:101-104](crush_lu/admin.py#L101-104):
   ```python
   # Comment out this line to keep dating profile active:
   # profile.is_active = False
   # profile.save()
   ```

4. **Add role-switching UI** (optional):
   - Add toggle in navigation: "Switch to Coach Mode" / "Switch to Dating Mode"
   - Store preference in session
   - Show appropriate dashboard based on mode

---

## Use Cases & Examples

### **Example 1: Trusted User Becomes Coach**

**Scenario:** Alice has been using Crush.lu for 6 months. She's great with people and you want her to help review new profiles.

**Steps:**
1. Go to Admin → Crush Profiles
2. Find Alice's profile
3. Select it → Action: "Promote selected profiles to Crush Coach role"
4. Alice's dating profile is deactivated
5. Alice gets email: "You've been selected as a Crush Coach!"
6. Next time Alice logs in, she sees the Coach Dashboard

### **Example 2: Coach Wants to Date**

**Scenario:** Bob is a coach but now wants to use the platform to find his own connections.

**Steps:**
1. Go to Admin → Crush Coaches
2. Find Bob's coach record
3. Select it → Action: "Deactivate coach role (allows them to date)"
4. Bob's coach role is deactivated
5. Bob can now create/activate his dating profile
6. Bob attends events as a regular user

### **Example 3: Temporary Coach Break**

**Scenario:** Coach Emma is going on vacation for 2 months.

**Steps:**
1. Go to Admin → Crush Coaches
2. Find Emma
3. Select → Action: "Deactivate selected coaches"
4. Emma won't get new profile reviews assigned
5. After vacation: Select → Action: "Activate selected coaches"

---

## Database Structure

```
User Table (Django built-in)
├── id: 1
├── username: "alice"
├── email: "alice@example.com"
└── first_name: "Alice"

CrushProfile Table
├── id: 1
├── user_id: 1  ← Foreign key to User
├── date_of_birth: 1995-03-15
├── bio: "Love hiking!"
├── is_active: False  ← Deactivated when promoted to coach
└── is_approved: True

CrushCoach Table
├── id: 1
├── user_id: 1  ← Foreign key to User (same user!)
├── bio: "Love hiking!"  ← Copied from profile
├── is_active: True
└── max_active_reviews: 10
```

**Key Point:** Same `user_id` in both tables! That's why we can convert between roles.

---

## Important Notes

### **Data Preservation**
- ✅ Converting roles does NOT delete data
- ✅ Original records are kept (just deactivated)
- ✅ Can be reversed/reactivated anytime
- ✅ Photos, bio, interests preserved

### **Active Connections**
- If you deactivate a dating profile with active connections:
  - ⚠️ User can still access their existing connections
  - ⚠️ User just can't make NEW connections
  - ⚠️ Test this thoroughly!

### **Profile Submissions**
- If you deactivate a coach who is reviewing profiles:
  - ⚠️ Their pending reviews remain assigned to them
  - 💡 Reassign reviews to another coach manually
  - 💡 Or wait for them to finish before deactivating

---

## Quick Reference Commands

### **In Django Admin:**

**Promote user to coach:**
```
Crush Profiles → Select user → "Promote to Crush Coach role"
```

**Allow coach to date:**
```
Crush Coaches → Select coach → "Deactivate coach role (allows them to date)"
```

**Bulk approve profiles:**
```
Crush Profiles → Select profiles → "Approve selected profiles"
```

**Temporary deactivate coaches:**
```
Crush Coaches → Select coaches → "Deactivate selected coaches"
```

---

## FAQ

**Q: Can a user be BOTH coach and dating user at the same time?**
A: Yes technically (both records can exist), but only ONE can be `is_active=True` at a time with current code. To allow dual roles, see "Allowing Dual Roles" section above.

**Q: What happens to a coach's profile reviews if I deactivate them?**
A: Reviews remain assigned. Manually reassign or finish before deactivating.

**Q: Can I reverse a conversion?**
A: Yes! Just use the opposite action. Data is preserved.

**Q: Do I need to run migrations?**
A: No, these admin actions use existing database structure.

**Q: Can users convert themselves?**
A: No, this is admin-only. You could build a "Coach Application" form if you want self-service.

---

## Testing Checklist

Before promoting/demoting in production:

- [ ] Test on development database first
- [ ] Check user has no active profile reviews
- [ ] Check user has no pending connections
- [ ] Verify user can access appropriate dashboard after conversion
- [ ] Test reversing the conversion
- [ ] Notify user of the change

---

## Next Steps

Would you like me to:

1. ✅ **Keep current system** (mutual exclusivity with admin conversion)
2. Add user-facing "Coach Application" form
3. Allow dual roles with role-switching UI
4. Add automated role assignment based on reputation
5. Something else?

Let me know!
