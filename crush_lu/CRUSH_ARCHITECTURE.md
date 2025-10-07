# Crush.lu Architecture Explained

## User Hierarchy

Crush.lu has a **3-tier architecture** for users:

```
Django User (Base)
    ├── CrushProfile (Dating User)
    └── CrushCoach (Coach/Reviewer)
```

### 1. **Django User** (Foundation)
- Built-in Django authentication model
- Fields: `username`, `email`, `password`, `first_name`, `last_name`
- Created when someone signs up
- **Everyone starts as a Django User**

### 2. **CrushProfile** (Dating Profiles)
- **OneToOne** relationship with Django User
- Contains dating-specific information:
  - Date of birth, gender, location
  - Bio, interests, looking for
  - Photos (up to 3)
  - Privacy settings
  - Approval status
- **Purpose**: People looking to meet others
- **Can do**: Attend events, make connections, get matched

### 3. **CrushCoach** (Coaches)
- **OneToOne** relationship with Django User
- Contains coach-specific information:
  - Bio, specializations
  - Max active reviews
  - Active status
- **Purpose**: Review profiles, facilitate connections, guide users
- **Can do**: Review profile submissions, facilitate connections, provide guidance

---

## Current Separation Logic

### **Mutual Exclusivity Rule**
Right now, **one User CANNOT be both a CrushProfile AND a CrushCoach simultaneously**.

This is enforced in [crush_lu/views.py:100-106](crush_lu/views.py#L100-106):

```python
@crush_login_required
def create_profile(request):
    """Profile creation"""
    # Check if user is a coach
    try:
        coach = CrushCoach.objects.get(user=request.user)
        messages.error(request, 'Coaches cannot create dating profiles.')
        return redirect('crush_lu:coach_dashboard')
    except CrushCoach.DoesNotExist:
        pass
    # ... continue with profile creation
```

And in [crush_lu/views.py:172-177](crush_lu/views.py#L172-177):

```python
def dashboard(request):
    """User dashboard - redirects coaches to their dashboard"""
    try:
        coach = CrushCoach.objects.get(user=request.user)
        return redirect('crush_lu:coach_dashboard')
    except CrushCoach.DoesNotExist:
        pass
    # ... regular user dashboard
```

---

## Why Separate?

### **Pros of Current Design:**
1. **Clear Role Separation**: Coaches focus on helping, not dating
2. **Trust**: Users know coaches are impartial facilitators
3. **No Conflicts**: Coaches can't review their own connections
4. **Professional Boundaries**: Like therapists not dating clients
5. **Simpler UI**: Different dashboards for different roles

### **Cons of Current Design:**
1. **Inflexible**: What if a coach wants to date?
2. **Manual Work**: Admin must manually create CrushCoach records
3. **No Promotion Path**: Users can't "graduate" to become coaches
4. **No Demotion Path**: Coaches can't step down to date

---

## How Users Are Created Currently

### **Scenario 1: Regular Dating User**
1. User visits Crush.lu
2. Signs up → Creates **Django User**
3. Fills profile form → Creates **CrushProfile**
4. Profile submitted for coach review
5. ✅ Can attend events and make connections

### **Scenario 2: Crush Coach** (Admin-only currently)
1. Admin creates Django User in admin panel
2. Admin creates **CrushCoach** linked to that User
3. ✅ Coach can review profiles

**Problem**: There's no self-service way to become a coach or switch roles!

---

## Proposed Solution: Role Conversion System

I can add functionality to allow:

### **Option A: Admin-Only Conversion**
- Admin can convert CrushProfile → CrushCoach
- Admin can convert CrushCoach → CrushProfile
- Safe, controlled, no self-service abuse

### **Option B: User Self-Service Application**
- Users can apply to become coaches
- Admin reviews and approves
- Approved users get converted automatically

### **Option C: Dual Roles Allowed**
- Allow one User to have BOTH CrushProfile AND CrushCoach
- Add role-switching UI
- More complex to implement

---

## Recommended Approach: Admin-Only Conversion with Actions

### **What I'll Build:**

1. **Admin Actions in Django Admin**
   - Select a CrushProfile → Click "Convert to Coach"
   - Select a CrushCoach → Click "Convert to Dating Profile"

2. **Conversion Logic:**
   ```python
   def convert_profile_to_coach(profile):
       # Deactivate dating profile
       profile.is_active = False
       profile.save()

       # Create coach profile
       CrushCoach.objects.create(
           user=profile.user,
           bio=profile.bio,  # Transfer bio
           is_active=True
       )
   ```

3. **Safety Checks:**
   - Can't convert if User already has both roles
   - Preserve original data (soft delete, not hard delete)
   - Add notes about why conversion happened

### **Benefits:**
- ✅ Simple to implement
- ✅ Maintains trust (admin approval required)
- ✅ Flexible for your business needs
- ✅ Reversible if needed
- ✅ Data preservation

---

## Alternative: Allow Both Roles Simultaneously

If you want coaches to also be able to date:

### **Changes Needed:**
1. Remove mutual exclusivity checks in views
2. Add role-switching UI (toggle between "Dating Mode" and "Coach Mode")
3. Update dashboards to show both sections
4. Add privacy rule: Coaches can't review profiles of people they've dated
5. Add ethics guidelines

### **Trade-offs:**
- ✅ More flexible
- ❌ More complex UI
- ❌ Potential conflicts of interest
- ❌ May reduce user trust

---

## What Should We Build?

I recommend **Option A: Admin-Only Conversion** because:

1. **You're in control**: Only you decide who becomes a coach
2. **Maintains trust**: Users know coaches are vetted
3. **Simple**: No complex role-switching UI
4. **Reversible**: Can convert back if needed
5. **Quick to implement**: Just admin actions

### **Implementation:**
- Add custom admin actions to `CrushProfileAdmin` and `CrushCoachAdmin`
- Add conversion methods to models
- Add archive fields to preserve history
- Add confirmation step to prevent accidents

---

## Next Steps

Would you like me to:

1. ✅ **Build admin conversion actions** (recommended)
2. Build a user-facing coach application system
3. Allow dual roles with role-switching
4. Something else?

Let me know which approach fits your business model best!
