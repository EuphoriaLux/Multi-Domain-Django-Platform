# Crush.lu Quick Setup Guide

## Prerequisites Check

Before setting up Crush.lu, ensure you have the required packages installed:

```bash
pip install django-crispy-forms
pip install crispy-bootstrap5
```

Or install all requirements at once:
```bash
pip install -r requirements.txt
```

## Quick Setup (Windows)

Simply run the setup script:
```bash
setup_crush.bat
```

## Manual Setup

If you prefer to run commands manually:

### 1. Create migrations
```bash
python manage.py makemigrations crush_lu
```

### 2. Apply migrations
```bash
python manage.py migrate
```

### 3. Create sample coaches
```bash
python manage.py create_crush_coaches
```

### 4. Create sample events
```bash
python manage.py create_sample_events
```

### 5. Start the server
```bash
python manage.py runserver
```

### 6. Access Crush.lu
Open your browser and navigate to:
```
http://localhost:8000/crush/
```

## Default Coach Accounts

After running `create_crush_coaches`, you can log in as a coach:

| Username | Email | Password | Specialization |
|----------|-------|----------|----------------|
| coach.marie | marie@crush.lu | crushcoach2025 | Young professionals, 25-35 |
| coach.thomas | thomas@crush.lu | crushcoach2025 | Students, 18-25 |
| coach.sophie | sophie@crush.lu | crushcoach2025 | 35+, Professionals |

## Testing the Platform

### Test Flow 1: Complete User Journey
1. Visit `http://localhost:8000/crush/`
2. Click "Join Now" or go to `/crush/signup/`
3. Create a new account
4. Complete your profile (add bio, interests, photos)
5. Submit profile for review
6. Log out and log in as a coach (e.g., coach.marie)
7. Go to `/crush/coach/dashboard/`
8. Review and approve the profile
9. Log out and log back in as your user
10. Browse events at `/crush/events/`
11. Register for an event

### Test Flow 2: Browse as Visitor
1. Visit `http://localhost:8000/crush/`
2. Explore the landing page
3. Click "How It Works" to see the process
4. Click "About" to learn about Crush.lu
5. View upcoming events (without registering)

### Test Flow 3: Coach Workflow
1. Log in as a coach
2. View pending profile submissions
3. Click on a submission to review
4. Choose to approve, reject, or request revisions
5. Add feedback notes
6. Submit review decision
7. View recent reviews in dashboard

## Available URLs

### Public Pages
- `/crush/` - Landing page
- `/crush/about/` - About page
- `/crush/how-it-works/` - How it works
- `/crush/events/` - Event list
- `/crush/events/<id>/` - Event detail
- `/crush/signup/` - User registration

### User Pages (login required)
- `/crush/dashboard/` - User dashboard
- `/crush/create-profile/` - Create profile
- `/crush/profile/edit/` - Edit profile
- `/crush/events/<id>/register/` - Register for event
- `/crush/events/<id>/cancel/` - Cancel registration

### Coach Pages (coach account required)
- `/crush/coach/dashboard/` - Coach dashboard
- `/crush/coach/review/<id>/` - Review profile
- `/crush/coach/sessions/` - View sessions

### Admin
- `/admin/` - Django admin interface

## Sample Events Created

After running `create_sample_events`, you'll have 6 events:

1. **Speed Dating: Young Professionals** - Urban Bar, Luxembourg City (€15)
2. **Casual Friday Mixer** - Café des Artistes (Free)
3. **Wine & Dine Speed Dating** - Vinothèque, Remich (€25)
4. **Outdoor Adventure Meetup** - Mullerthal Trail (€10)
5. **90s Throwback Speed Dating** - Retro Club, Esch (€12)
6. **Brunch & Mingle** - Sunny Side Café (€18)

All events are scheduled for dates starting 1 week from today.

## Features to Test

### Privacy Controls
- [ ] Show/hide full name option
- [ ] Show exact age vs age range
- [ ] Photo blur option

### Profile Approval Workflow
- [ ] Profile submission creates ProfileSubmission
- [ ] Coach auto-assignment works
- [ ] Approval updates profile status
- [ ] Rejected profiles get feedback
- [ ] Revision requests work

### Event Management
- [ ] Can't register without approved profile
- [ ] Event capacity limits work
- [ ] Waitlist activates when full
- [ ] Registration deadline enforced
- [ ] Can cancel registration

### Dashboard Features
- [ ] Profile status displayed correctly
- [ ] Upcoming events shown
- [ ] Past events shown
- [ ] Event registration status badges

## Troubleshooting

### Issue: ModuleNotFoundError: No module named 'crispy_forms'
**Solution**: Install missing packages:
```bash
pip install django-crispy-forms crispy-bootstrap5
```

### Issue: No events showing on homepage
**Solution**: Run the command to create sample events:
```bash
python manage.py create_sample_events
```

### Issue: Can't access coach dashboard
**Solution**: Make sure you're logged in as a user with a CrushCoach profile. Use one of the default coaches created by `create_crush_coaches`.

### Issue: Can't register for events
**Solution**: Your profile needs to be approved first. Log in as a coach and approve your profile from the coach dashboard.

### Issue: Photos not uploading
**Solution**: Make sure the `media` directory exists and has write permissions. Check `MEDIA_ROOT` in settings.py.

## Next Steps

Once you've tested the basic functionality:

1. **Customize the design** - Edit templates in `crush_lu/templates/crush_lu/`
2. **Add email notifications** - Configure EMAIL settings in settings.py
3. **Set up payment processing** - Integrate Stripe for event fees
4. **Deploy to production** - Update domain routing for crush.lu
5. **Add more event types** - Create custom event templates
6. **Implement photo blur** - Add JavaScript for privacy feature
7. **Create mobile views** - Optimize for mobile devices

## Production Deployment Notes

When deploying to production with the crush.lu domain:

1. Update `ALLOWED_HOSTS` to include production domain
2. Set `DEBUG = False` in production settings
3. Configure proper EMAIL backend for notifications
4. Set up SSL certificate for HTTPS
5. Configure Azure Blob Storage for media files
6. Update domain routing middleware to recognize crush.lu
7. Run migrations on production database
8. Create real coach accounts (don't use default passwords!)

## Support

For more detailed documentation, see:
- [README.md](crush_lu/README.md) - Crush.lu specific docs
- [CLAUDE.md](CLAUDE.md) - Full project documentation

Enjoy building connections with Crush.lu! 💕
