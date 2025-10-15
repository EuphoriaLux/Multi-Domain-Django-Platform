# Special User Experience System - Crush.lu

## Overview

The Special User Experience system allows you to create personalized, romantic welcome experiences for specific users on Crush.lu. This is perfect for creating a unique journey for someone special! 💕

## Features Implemented

### 1. **Admin-Configurable Settings**
- Set target user by first name + last name
- Custom welcome title and message
- Custom theme color (hex code)
- Multiple animation styles:
  - 🌹 Floating Hearts
  - ⭐ Sparkling Stars
  - 🌸 Falling Rose Petals
  - 🎆 Fireworks
  - 🌌 Aurora Borealis

### 2. **VIP Features**
- Auto-approve profile (skip coach review)
- Skip event waitlists (always get confirmed spots)
- VIP badge display
- Tracking of how many times the experience was triggered

### 3. **Beautiful Animated Welcome Page**
- Stunning gradient background
- Smooth fade-in animations
- Fully responsive design
- Multiple animation effects overlaying the page
- Glassmorphism card design

## How to Set It Up

### Step 1: Access Django Admin
1. Go to your Crush.lu admin panel: `https://crush.lu/admin/` (or `http://localhost:8000/admin/` for dev)
2. Login with your admin credentials

### Step 2: Create a Special User Experience
1. Navigate to **"Special User Experiences"** in the admin sidebar
2. Click **"Add Special User Experience"**
3. Fill in the details:

#### User Matching Section:
- **First Name**: Enter her first name (e.g., "Marie")
- **Last Name**: Enter her last name (e.g., "Dupont")
- **Is Active**: ✅ Check this box to activate

#### Custom Welcome Experience:
- **Custom Welcome Title**: e.g., "Welcome to Your Special Journey, Marie"
- **Custom Welcome Message**: Write your personal message! e.g., "Something magical awaits you in this special space created just for you... 💕"
- **Custom Theme Color**: Choose a romantic color (e.g., `#FF1493` for deep pink, `#C850C0` for purple)
- **Animation Style**: Choose from:
  - **hearts** - Floating hearts animation
  - **roses** - Falling rose petals (very romantic!)
  - **stars** - Sparkling stars
  - **fireworks** - Celebration fireworks
  - **aurora** - Aurora borealis waves

#### VIP Features:
- **Auto Approve Profile**: ✅ Recommended - skip coach review
- **Skip Waitlist**: ✅ Recommended - always get event spots
- **VIP Badge**: ✅ Show special VIP badge on welcome screen

4. Click **"Save"**

### Step 3: Test the Experience
1. The user must register with the EXACT first name and last name you configured
2. When they log in, they will automatically be redirected to the special welcome page
3. The welcome page will show:
   - Custom title and message
   - Beautiful animations
   - VIP badge (if enabled)
   - "Begin Your Journey" button → takes them to dashboard

## How It Works Technically

### Detection Flow:
1. User logs in with username/email + password
2. Django `user_logged_in` signal fires
3. Signal handler checks if user's first_name + last_name matches any active `SpecialUserExperience`
4. If match found:
   - Session data is populated with special experience settings
   - Profile is auto-approved (if configured)
   - User is redirected to `/special-welcome/` page

### Session Data Stored:
```python
{
    'special_experience_active': True,
    'special_experience_id': <id>,
    'special_experience_data': {
        'welcome_title': "...",
        'welcome_message': "...",
        'theme_color': "#FF1493",
        'animation_style': "roses",
        'vip_badge': True,
        'custom_landing_url': ""
    }
}
```

### Files Created/Modified:
- ✅ `crush_lu/models.py` - Added `SpecialUserExperience` model
- ✅ `crush_lu/signals.py` - Added login detection signal
- ✅ `crush_lu/views.py` - Added `special_welcome` view and login redirect logic
- ✅ `crush_lu/urls.py` - Added `/special-welcome/` URL route
- ✅ `crush_lu/admin.py` - Registered admin interface with custom fieldsets
- ✅ `crush_lu/templates/crush_lu/special_welcome.html` - Beautiful animated welcome page
- ✅ `crush_lu/migrations/0010_specialuserexperience.py` - Database migration

## Customization Ideas

### Romantic Color Schemes:
- Deep Pink: `#FF1493`
- Purple Love: `#9B59B6`
- Rose Gold: `#B76E79`
- Sunset Orange: `#FF6B6B`
- Lavender: `#E6E6FA`

### Message Ideas:
- "Welcome to a space created just for you... ✨"
- "Something special happens when two hearts meet..."
- "Your journey to connection starts here 💕"
- "In a sea of profiles, yours shines the brightest ⭐"

### Animation Combinations:
- **Romantic Classic**: Rose petals + Deep Pink (`#FF1493`)
- **Magical Night**: Stars + Purple (`#9B59B6`)
- **Celebration**: Fireworks + Gold (`#FFD700`)
- **Dreamy**: Aurora + Lavender (`#E6E6FA`)

## Admin Tracking

The admin panel tracks:
- **Trigger Count**: How many times the experience was activated
- **Last Triggered At**: Last time the user logged in and saw it
- You can activate/deactivate experiences anytime

## Important Notes

⚠️ **Name Matching**:
- Matching is case-insensitive
- Must match BOTH first name AND last name exactly
- Example: "Marie" matches "marie" or "MARIE"

⚠️ **Privacy**:
- This is a personal feature - be respectful
- Only create experiences for people who would appreciate them
- The special experience is private and only visible to the matched user

⚠️ **One-Time Show**:
- The special welcome page shows once per login session
- After clicking "Begin Your Journey", they go to normal dashboard
- They can return to the special page via direct URL if still logged in

## Troubleshooting

**Special experience not triggering?**
1. Check that `is_active` is ✅ checked
2. Verify first name + last name match exactly (check User model in admin)
3. Make sure user is logging in (not just visiting while already logged in)
4. Check Django logs for signal messages

**Animations not working?**
1. Check browser console for JavaScript errors
2. Ensure template is using the correct animation_style value
3. Try a different animation style

**Profile not auto-approving?**
1. Verify `auto_approve_profile` is checked
2. Check that user has a `CrushProfile` created
3. Look for log messages in Django console

## Next Steps / Future Enhancements

Ideas for expanding this feature:
- [ ] Custom background image upload
- [ ] Personalized photo gallery
- [ ] Custom music/audio greeting
- [ ] Multiple welcome messages that rotate
- [ ] Timeline of special moments
- [ ] Custom event invitations
- [ ] Private messaging channel

---

**Created with love for Crush.lu** 💕
