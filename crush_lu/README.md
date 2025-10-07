# Crush.lu - Privacy-First Dating Platform

## Local Development Access

### URLs
- **Local Development**: `http://localhost:8000/crush/`
- **Production**: `http://crush.lu/` (when deployed)

### Key Pages
- Landing page: `/crush/`
- Sign up: `/crush/signup/`
- Events: `/crush/events/`
- Dashboard: `/crush/dashboard/` (requires login)
- Coach Dashboard: `/crush/coach/dashboard/` (requires coach account)

## Setup Instructions

### 1. Run Migrations
```bash
python manage.py makemigrations crush_lu
python manage.py migrate
```

### 2. Create Sample Data
```bash
# Create 3 sample coach profiles
python manage.py create_crush_coaches

# Create 6 sample events
python manage.py create_sample_events
```

### 3. Create Admin User (if needed)
```bash
python manage.py createsuperuser
```

### 4. Start Development Server
```bash
python manage.py runserver
```

### 5. Access the Platform
Open your browser and go to: `http://localhost:8000/crush/`

## Test Accounts

### Coach Accounts
After running `create_crush_coaches`, you'll have these coaches:
- Username: `coach.marie` | Email: `marie@crush.lu` | Password: `crushcoach2025`
- Username: `coach.thomas` | Email: `thomas@crush.lu` | Password: `crushcoach2025`
- Username: `coach.sophie` | Email: `sophie@crush.lu` | Password: `crushcoach2025`

## Testing the Full User Journey

### As a New User:
1. Go to `/crush/signup/`
2. Create an account
3. Complete your profile at `/crush/create-profile/`
4. Submit for review

### As a Coach:
1. Log out of your user account
2. Log in as a coach (use credentials above)
3. Go to `/crush/coach/dashboard/`
4. Review and approve the pending profile
5. Log out

### As an Approved User:
1. Log back in as your user
2. Go to `/crush/dashboard/` - see approved status
3. Browse events at `/crush/events/`
4. Register for an event

## Admin Access

Django Admin: `http://localhost:8000/admin/`

You can manage all Crush.lu data:
- Crush Coaches
- Crush Profiles
- Profile Submissions
- Meetup Events
- Event Registrations
- Coach Sessions

## Features

### User Features
- ✅ Privacy-focused profile creation
- ✅ Photo upload (up to 3 photos)
- ✅ Privacy controls (name display, age display, photo blur)
- ✅ Event browsing and registration
- ✅ Dashboard with profile status and events
- ✅ Waitlist support for full events

### Coach Features
- ✅ Profile review dashboard
- ✅ Approve/reject/request revision workflow
- ✅ Provide feedback to users
- ✅ Workload management (max active reviews)
- ✅ Session tracking

### Event Management
- ✅ Multiple event types (speed dating, mixer, activity, themed)
- ✅ Capacity management
- ✅ Age restrictions
- ✅ Registration deadlines
- ✅ Payment tracking
- ✅ Dietary restrictions and special requests

## Privacy & Safety

- All profiles reviewed by coaches before going live
- Users control what information they share
- 18+ age verification
- Event-based connections (no endless swiping)
- Designed for Luxembourg's small community

## Architecture

### Models
- `CrushProfile` - User profiles with privacy settings
- `CrushCoach` - Coach profiles for onboarding
- `ProfileSubmission` - Review workflow tracking
- `MeetupEvent` - Speed dating and social events
- `EventRegistration` - Event RSVPs and attendance
- `CoachSession` - Coach-user interactions

### Views
- Public: home, about, how_it_works, event_list, event_detail
- Authenticated: dashboard, create_profile, edit_profile, event_register, event_cancel
- Coach: coach_dashboard, coach_review_profile, coach_sessions

## Troubleshooting

### Issue: "No module named 'crush_lu'"
**Solution**: Make sure `crush_lu` is in `INSTALLED_APPS` in settings.py

### Issue: "Reverse for 'crush_lu:home' not found"
**Solution**: The namespace is registered. Use `{% url 'crush_lu:home' %}` in templates

### Issue: Can't access coach dashboard
**Solution**: You need a `CrushCoach` object linked to your user. Run `create_crush_coaches` or create one in admin.

### Issue: Can't register for events
**Solution**: Your profile must be approved first. Log in as a coach and approve your profile.

## Next Steps

1. **Customize Design**: Edit templates in `crush_lu/templates/crush_lu/`
2. **Add Email Notifications**: Implement email alerts for profile approvals and event reminders
3. **Payment Integration**: Add Stripe/PayPal for event fees
4. **Enhanced Privacy**: Implement photo blurring functionality
5. **Mobile App**: Consider React Native wrapper for mobile experience

## Support

For issues or questions, refer to the main [CLAUDE.md](../CLAUDE.md) documentation.
