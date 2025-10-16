# Journey Testing Guide - Maxi Fries

## Journey Created Successfully ✓

The "Wonderland of You" journey has been created for Maxi Fries with the following configuration:

- **First Name**: Maxi
- **Last Name**: Fries
- **Date First Met**: October 9, 2025
- **Location**: e-lake
- **Journey Name**: The Wonderland of You
- **Total Chapters**: 6
- **Total Challenges**: 17
- **Status**: Active

## Chapter Breakdown

### Chapter 1: Down the Rabbit Hole
- **Theme**: wonderland_night (Dark blue gradient with floating elements)
- **Challenges**: 2
- **Rewards**: 1 (Welcome poem)

### Chapter 2: The Garden of Rare Flowers
- **Theme**: enchanted_garden (Mystical garden atmosphere)
- **Challenges**: 4
- **Rewards**: 1 (Love letter)

### Chapter 3: The Gallery of Moments
- **Theme**: art_gallery (Elegant museum setting)
- **Challenges**: 2
- **Rewards**: 1 (Memory poem)

### Chapter 4: The Carnival of Courage
- **Theme**: carnival (Vibrant, playful atmosphere)
- **Challenges**: 4
- **Rewards**: 1 (Courage poem)

### Chapter 5: The Starlit Observatory
- **Theme**: starlit_sky (Cosmic night sky)
- **Challenges**: 2
- **Rewards**: 1 (Future poem)

### Chapter 6: The Door to Tomorrow
- **Theme**: magical_door (Mysterious finale)
- **Challenges**: 3
- **Rewards**: Final reveal with response buttons

## How to Test

### 1. Create or Use Test User

You mentioned you have a test user. Make sure the user has:
- **First Name**: Maxi
- **Last Name**: Fries

The name match is case-insensitive, so "Maxi Fries", "maxi fries", or "MAXI FRIES" will all work.

### 2. Login Flow

When the user logs in:

1. **Signal Detection**: The `user_logged_in` signal will detect the matching name
2. **Session Activation**: Sets `special_experience_active` in session
3. **Auto-Redirect**: Login redirects to `special_welcome` page
4. **Journey Check**: `special_welcome` view detects journey and redirects to `journey_map`
5. **Journey Starts**: User sees the visual journey map

### 3. Testing URL Paths

You can access the journey directly with these URLs (after login):

```
/journey/                                      # Main journey map
/journey/chapter/1/                           # Chapter 1
/journey/chapter/1/challenge/1/               # First challenge
/journey/reward/1/                            # View reward
/journey/certificate/                         # Final certificate (after completion)
```

### 4. Expected Behavior

**On Login**:
- User logs in with credentials
- System detects "Maxi Fries" matches special experience
- Redirects to journey map (bypassing profile creation)
- Shows visual chapter pathway with Chapter 1 unlocked

**Chapter Progression**:
- Only Chapter 1 is unlocked initially
- Complete all challenges in a chapter to unlock the next
- Each correct answer awards points
- Hints available but cost points
- Rewards unlock after chapter completion

**Journey Completion**:
- After completing all 6 chapters
- Final reveal message displays
- Response buttons: "Yes, I'd love to!" and "I need time to think"
- Confetti animation on completion
- Printable certificate available

### 5. Admin Access

View and manage the journey in Django admin:

```
/admin/crush_lu/journeyconfiguration/
/admin/crush_lu/journeychapter/
/admin/crush_lu/journeychallenge/
/admin/crush_lu/journeyprogress/
```

### 6. Testing Checklist

- [ ] Login with Maxi Fries account
- [ ] Verify redirect to journey map (not profile creation)
- [ ] See Chapter 1 unlocked, others locked
- [ ] Click Chapter 1 and see challenges
- [ ] Answer a challenge correctly
- [ ] Test hint system (unlock hint, points deducted)
- [ ] Complete all challenges in Chapter 1
- [ ] View reward after chapter completion
- [ ] Verify Chapter 2 unlocks
- [ ] Test auto-save functionality (wait 30 seconds)
- [ ] Test progress persistence (logout and login again)
- [ ] Complete all chapters
- [ ] View final certificate

## Troubleshooting

### User redirected to profile creation instead of journey

**Fixed!** The views have been updated to redirect special users to the home page instead of forcing profile creation. Special users can access the journey without completing a Crush.lu profile.

### Journey not showing

Check in admin:
1. SpecialUserExperience for "Maxi Fries" is active
2. JourneyConfiguration is active
3. User's first_name and last_name exactly match

### Chapters not unlocking

- Verify all challenges in previous chapter are completed
- Check ChapterProgress in admin
- Ensure chapter has `requires_previous_completion=True` (except Chapter 1)

### Auto-save not working

- Check browser console for JavaScript errors
- Verify API endpoints are accessible
- Check that CSRF token is present

## Key Features Implemented

1. **No Profile Required**: Special users bypass Crush.lu profile creation
2. **Auto-Detection**: Login automatically detects matching names
3. **Sequential Unlocking**: Chapters unlock as previous ones complete
4. **Point System**: Points awarded for challenges, deducted for hints
5. **Auto-Save**: Progress saved every 30 seconds
6. **Session Persistence**: Hint usage tracked in session
7. **Responsive Design**: Mobile-friendly interface
8. **Theme Variations**: Each chapter has unique visual theme
9. **Reward System**: Poems and messages unlock after chapters
10. **Final Reveal**: Romantic message with response buttons

## Next Steps

1. **Test with your existing test user** that has first_name="Maxi" and last_name="Fries"
2. **Verify the complete flow** from login to journey completion
3. **Customize content** via Django admin if needed (edit questions, hints, rewards)
4. **Add more challenge types** if desired (5 templates created, 11 more types available)
5. **Monitor logs** for any issues during testing

## Support

All journey code is in:
- `crush_lu/models.py` (lines 638-1043) - Database models
- `crush_lu/views_journey.py` - Journey views
- `crush_lu/api_journey.py` - API endpoints
- `crush_lu/templates/crush_lu/journey/` - Templates
- `crush_lu/admin.py` (lines 714-1125) - Admin interfaces

For detailed implementation guide, see `JOURNEY_SYSTEM_IMPLEMENTATION.md`.
For quick setup, see `JOURNEY_QUICK_START.md`.
