# Crush.lu: Coach vs User Accounts

## Account Types

Crush.lu has **two distinct account types** that cannot be mixed:

### 1. 🎯 **Coach Accounts**
- Purpose: Review and approve user profiles
- Access: Coach dashboard, profile review tools
- Cannot: Create a dating profile or register for events
- Created by: Admin or management command

### 2. 💕 **User Accounts** (Dating Profiles)
- Purpose: Create dating profile, attend events
- Access: User dashboard, event registration
- Cannot: Review profiles or access coach features
- Created by: Self-signup through the platform

## How It Works

### User Journey (Regular Users)
1. Sign up at `/crush/signup/`
2. Create dating profile
3. Profile reviewed by coach
4. Once approved, can register for events
5. Attend meetups and make connections

### Coach Journey (Coaches)
1. Account created by admin via management command
2. Log in at `/crush/login/`
3. Redirected to coach dashboard automatically
4. Review pending profiles
5. Approve, reject, or request revisions

## Technical Implementation

### Database Structure
- `User` model (Django auth) - base user for login
- `CrushProfile` - OneToOne with User (for dating users)
- `CrushCoach` - OneToOne with User (for coaches)

### Separation Logic
When a user logs in and tries to access features:

```python
# In views.py
if CrushCoach.objects.filter(user=request.user).exists():
    # This is a coach - redirect to coach dashboard
    return redirect('crush_lu:coach_dashboard')
else:
    # This is a regular user - show dating features
    # ...
```

### Checks in Place

**create_profile view:**
```python
# Prevents coaches from creating dating profiles
if CrushCoach.objects.get(user=request.user):
    messages.error('Coaches cannot create dating profiles')
    return redirect('crush_lu:coach_dashboard')
```

**dashboard view:**
```python
# Redirects coaches to their own dashboard
if CrushCoach.objects.get(user=request.user):
    return redirect('crush_lu:coach_dashboard')
```

## Creating Coach Accounts

### Via Management Command (Recommended)
```bash
python manage.py create_crush_coaches
```

This creates 3 sample coaches:
- **coach.marie** - Specializes in young professionals (25-35)
- **coach.thomas** - Specializes in students (18-25)
- **coach.sophie** - Specializes in 35+ professionals

Default password: `crushcoach2025`

### Via Django Admin
1. Go to `/admin/`
2. Create a new User
3. Create a CrushCoach object linked to that user
4. Set specializations and max_active_reviews

### Manually in Shell
```python
from django.contrib.auth.models import User
from crush_lu.models import CrushCoach

# Create user
user = User.objects.create_user(
    username='coach.alex',
    email='alex@crush.lu',
    password='securepassword',
    first_name='Alex',
    last_name='Johnson'
)

# Create coach profile
coach = CrushCoach.objects.create(
    user=user,
    bio='Experienced relationship coach',
    specializations='LGBTQ+, All ages',
    is_active=True,
    max_active_reviews=15
)
```

## What Coaches Can Do

### Dashboard (`/crush/coach/dashboard/`)
- View pending profile submissions
- See review workload (X out of Y max reviews)
- Access recent review history

### Review Profiles (`/crush/coach/review/<id>/`)
- View full profile details
- See photos, bio, interests
- Make decision:
  - **Approve** - Profile goes live
  - **Reject** - User notified with feedback
  - **Revision** - Request changes from user
- Add internal notes (not visible to user)
- Add feedback (visible to user if rejected/revision)

### Coach Sessions (`/crush/coach/sessions/`)
- Track coaching interactions
- Schedule follow-up sessions
- Document session notes

## What Coaches CANNOT Do

❌ Create a dating profile
❌ Register for events
❌ See other user profiles (except for review)
❌ Match with users

## What Users CANNOT Do

❌ Access coach dashboard
❌ Review other profiles
❌ Approve/reject profiles
❌ See coach-only features

## Best Practices

### For Coaches
1. **Review Promptly**: Users are waiting for approval
2. **Be Constructive**: If requesting revisions, provide clear feedback
3. **Document Sessions**: Track all coaching interactions
4. **Maintain Boundaries**: Keep professional separation from users

### For Platform Admins
1. **Vet Coaches Carefully**: They represent your brand
2. **Set Appropriate Limits**: Adjust `max_active_reviews` per coach capacity
3. **Monitor Workload**: Ensure coaches aren't overloaded
4. **Provide Guidelines**: Create review standards for consistency

## Security & Privacy

### Coach Access
- Coaches only see profiles assigned to them for review
- Internal notes are never visible to users
- Coaches cannot access user event registrations
- Coaches cannot message users directly (system maintains separation)

### User Privacy
- Users don't see which coach reviewed them (optional to show)
- Profile visibility controlled by approval status
- Rejected profiles are not public
- Coaches see profiles only during review process

## Troubleshooting

### Issue: Coach accidentally created as regular user
**Solution:**
```python
from crush_lu.models import CrushProfile, CrushCoach

# Delete dating profile
CrushProfile.objects.filter(user__username='coach.name').delete()

# Create coach profile
user = User.objects.get(username='coach.name')
CrushCoach.objects.create(user=user, ...)
```

### Issue: User trying to access coach dashboard
**Result:** Redirected to create profile or user dashboard automatically

### Issue: Coach trying to sign up through normal flow
**Result:** Can create account, but prevented from creating dating profile

## Future Enhancements

Ideas for improving the coach system:

1. **Coach Specialization Matching**: Auto-assign based on user age/interests
2. **Coach Ratings**: Let users rate their coach experience
3. **Video Call Integration**: Schedule 1-on-1 coaching sessions
4. **Messaging System**: Allow coach-user communication within platform
5. **Coach Training Module**: Onboarding for new coaches
6. **Performance Metrics**: Track approval rates, response times
7. **Multi-Coach Review**: Require 2+ approvals for sensitive cases

---

**Remember:** The separation between coaches and users is fundamental to maintaining platform integrity and user trust. Coaches are mentors and gatekeepers, not participants.
