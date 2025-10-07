# Crush.lu Navigation System Guide

## Overview

The navigation menu (`base.html`) now adapts to three different user roles, showing only relevant options for each type of user.

---

## Navigation Variations

### **1. Guest Users (Not Logged In)**

```
┌─────────────────────────────────────────────────────┐
│ 💕 Crush.lu  [About] [How It Works] [Events] [Login] [Join Now] │
└─────────────────────────────────────────────────────┘
```

**Menu Items:**
- **About** - Learn about Crush.lu
- **How It Works** - Understand the platform concept
- **Events** - Browse upcoming events (public view)
- **Login** - Access account
- **Join Now** (highlighted button) - Sign up

**Purpose:** Marketing and onboarding focus

---

### **2. Dating Users (Regular Members)**

```
┌──────────────────────────────────────────────────────────┐
│ 💕 Crush.lu  [🏠 Dashboard] [📅 Events] [👥 My Connections ①] [👤 Alice ▾] │
└──────────────────────────────────────────────────────────┘
```

**Menu Items:**
- **🏠 Dashboard** - Personal dashboard with profile status, events
- **📅 Events** - Browse and register for events
- **👥 My Connections** - View connections, requests
  - **Red Badge (①)** - Shows number of PENDING connection requests awaiting response
- **👤 User Dropdown:**
  - ✏️ Edit Profile
  - 🚪 Logout

**Purpose:** Dating and connection-focused

**Badge Logic:**
- Only shows when `pending_requests_count > 0`
- Updates in real-time via context processor
- Draws attention to requests needing action

---

### **3. Crush Coaches**

```
┌────────────────────────────────────────────────────────┐
│ 💕 Crush.lu  [⚡ Coach Dashboard] [📋 My Sessions] [📅 Events] [👤 Thomas ▾] │
└────────────────────────────────────────────────────────┘
```

**Menu Items:**
- **⚡ Coach Dashboard** - Review pending profile submissions
- **📋 My Sessions** - View coaching sessions and notes
- **📅 Events** - See event listings (coach perspective)
- **👤 Coach Dropdown:**
  - 🚪 Logout

**Purpose:** Profile review and facilitation focused

**No "My Connections":** Coaches don't date on the platform while active

---

## Technical Implementation

### **Role Detection Logic**

Located in [crush_lu/templates/crush_lu/base.html](crush_lu/templates/crush_lu/base.html#L92-L169):

```django
{% if user.is_authenticated %}
    {% if user.crushcoach and user.crushcoach.is_active %}
        {# COACH NAVIGATION #}
    {% else %}
        {# DATING USER NAVIGATION #}
    {% endif %}
{% else %}
    {# GUEST NAVIGATION #}
{% endif %}
```

**Detection Order:**
1. Check if user is authenticated
2. If yes, check if `user.crushcoach` exists and `is_active=True`
3. If coach is active → Show coach nav
4. Otherwise → Show dating user nav

---

### **Context Processor**

File: [crush_lu/context_processors.py](crush_lu/context_processors.py)

**Purpose:** Makes user-specific data available to ALL templates

**Variables Added:**
- `connection_count` - Total active connections
- `pending_requests_count` - Requests awaiting user's response (for badge)

**Registered in:** [azureproject/settings.py:105](azureproject/settings.py#L105)

```python
'context_processors': [
    # ... other processors
    'crush_lu.context_processors.crush_user_context',
]
```

**Query Logic:**
```python
pending_requests_count = EventConnection.objects.filter(
    recipient=request.user,
    status='pending'
).count()
```

---

## Visual Design

### **Icons Used** (Bootstrap Icons)

| Icon | Code | Purpose |
|------|------|---------|
| 🏠 | `bi-house-heart` | Dashboard (dating users) |
| ⚡ | `bi-speedometer2` | Dashboard (coaches) |
| 📅 | `bi-calendar-event` | Events |
| 📋 | `bi-calendar-check` | Sessions |
| 👥 | `bi-people` | Connections |
| 👤 | `bi-person-circle` | User profile |
| ✏️ | `bi-pencil` | Edit |
| 🚪 | `bi-box-arrow-right` | Logout |

### **Colors & Styling**

**Gradient Background:**
```css
background: linear-gradient(135deg, #9B59B6, #FF6B9D);
```

**Badge Style:**
```css
.badge {
    font-size: 0.7rem;
    padding: 0.25em 0.5em;
    background-color: #dc3545; /* Red */
    border-radius: 50px;
}
```

**Dropdown Menu:**
- Clean white background
- Soft shadow: `0 4px 15px rgba(0,0,0,0.15)`
- Hover effect with purple tint

---

## Mobile Responsiveness

### **Breakpoint: 991px**

**Above 991px (Desktop):**
- Horizontal menu
- Dropdowns open below
- All icons visible

**Below 991px (Mobile):**
- Hamburger menu (☰)
- Vertical stacked items
- Dropdowns expand inline
- Adjusted colors for better visibility on gradient background

**CSS:**
```css
@media (max-width: 991px) {
    .navbar-nav {
        padding: 1rem 0;
    }
    .dropdown-menu {
        background-color: rgba(255,255,255,0.1);
    }
    .dropdown-item {
        color: rgba(255,255,255,0.9) !important;
    }
}
```

---

## User Flow Examples

### **Example 1: New User Journey**

1. **Visits site** → Sees Guest nav with "Join Now" CTA
2. **Clicks "Join Now"** → Signs up, creates profile
3. **Logs in** → Nav switches to Dating User mode
4. **Profile pending approval** → Dashboard shows "Under Review"
5. **Profile approved** → Can now register for events
6. **Attends event** → "Make Connections" button appears
7. **Sends connection request** → Badge appears on recipient's nav (①)
8. **Recipient responds** → Badge disappears

### **Example 2: Coach Experience**

1. **Admin promotes user to coach** → User's dating profile deactivated
2. **Logs in** → Nav switches to Coach mode
3. **Sees "Coach Dashboard"** → Reviews pending profiles
4. **No "My Connections"** → Focused on coaching, not dating
5. **Admin deactivates coach role** → Nav switches back to Dating User mode

### **Example 3: Connection Request Flow**

**Alice's Perspective (Requester):**
1. Attends event
2. Sends connection request to Bob
3. Nav shows "My Connections" (no badge yet)

**Bob's Perspective (Recipient):**
1. Receives connection request
2. **Red badge (①) appears** on "My Connections"
3. Clicks → Sees Alice's request
4. Clicks "Accept" → Badge disappears
5. Connection moves to "Active Connections"

---

## Customization Options

### **Add More Nav Items**

To add a new menu item for dating users:

```django
<li class="nav-item">
    <a class="nav-link" href="{% url 'crush_lu:new_feature' %}">
        <i class="bi bi-star"></i> New Feature
    </a>
</li>
```

Insert at [base.html:191](crush_lu/templates/crush_lu/base.html#L191) (after "Events")

### **Change Badge Color**

Currently: Red (`bg-danger`)
Options: `bg-primary` (purple), `bg-warning` (yellow), `bg-success` (green)

```django
<span class="badge bg-warning rounded-pill">{{ pending_requests_count }}</span>
```

### **Add Notifications for Coaches**

Add to coach navigation:

```django
<li class="nav-item">
    <a class="nav-link" href="{% url 'crush_lu:coach_dashboard' %}">
        <i class="bi bi-speedometer2"></i> Coach Dashboard
        {% if pending_reviews_count > 0 %}
            <span class="badge bg-warning rounded-pill">{{ pending_reviews_count }}</span>
        {% endif %}
    </a>
</li>
```

Then add to context processor:

```python
if hasattr(request.user, 'crushcoach') and request.user.crushcoach.is_active:
    pending_reviews_count = ProfileSubmission.objects.filter(
        coach=request.user.crushcoach,
        status='pending'
    ).count()
    context['pending_reviews_count'] = pending_reviews_count
```

---

## Testing Checklist

When testing navigation:

### **As Guest:**
- [ ] See all public pages (About, How It Works, Events)
- [ ] "Join Now" button is highlighted and clickable
- [ ] Login redirects to appropriate dashboard after auth

### **As Dating User:**
- [ ] Dashboard link works
- [ ] Events link shows event list
- [ ] My Connections shows connections page
- [ ] Badge shows ONLY when pending requests exist
- [ ] Badge count is accurate
- [ ] Profile dropdown has "Edit Profile"
- [ ] Logout works

### **As Coach:**
- [ ] Coach Dashboard link works
- [ ] My Sessions link works
- [ ] Events link works
- [ ] NO "My Connections" in nav
- [ ] NO "Edit Profile" in dropdown
- [ ] Logout works

### **Mobile (<991px):**
- [ ] Hamburger menu appears
- [ ] All items stack vertically
- [ ] Dropdowns expand inline
- [ ] Text is readable on gradient background
- [ ] Touch targets are adequately sized

---

## Future Enhancements

### **Potential Additions:**

1. **Search Bar**
   - Add to nav for finding users/events
   - Conditional: only for approved users

2. **Notifications Dropdown**
   - Bell icon with badge
   - Shows recent activity (connection requests, coach messages, event reminders)

3. **Quick Actions**
   - Dropdown with frequently-used actions
   - "Request Connection", "Register for Event", etc.

4. **Admin Quick Access**
   - For staff users: direct link to Django Admin
   - Conditional: `{% if user.is_staff %}`

5. **Language Switcher**
   - Flags for FR/DE/EN
   - Respects i18n middleware

6. **Coach/User Toggle**
   - If dual roles allowed (see [ROLE_CONVERSION_GUIDE.md](ROLE_CONVERSION_GUIDE.md))
   - Switch between "Coach Mode" and "Dating Mode"

---

## Troubleshooting

### **Badge Not Showing:**
1. Check user is authenticated
2. Verify context processor is registered in settings
3. Check EventConnection objects exist with `status='pending'`
4. Inspect template variable: `{{ pending_requests_count }}`

### **Wrong Nav Showing:**
1. Check if `user.crushcoach` exists
2. Verify `crushcoach.is_active` is True/False
3. Clear browser cache
4. Check middleware order in settings

### **Icons Not Displaying:**
1. Verify Bootstrap Icons CDN is loaded
2. Check internet connection (CDN dependency)
3. Look for console errors in browser DevTools
4. Fallback: Use emoji instead of `<i>` tag

### **Dropdown Not Working:**
1. Verify Bootstrap JS is loaded at bottom of template
2. Check `data-bs-toggle="dropdown"` attribute
3. Ensure Bootstrap version is 5.3.0+
4. Test in different browsers

---

## Files Modified

| File | Changes |
|------|---------|
| [crush_lu/templates/crush_lu/base.html](crush_lu/templates/crush_lu/base.html) | Added role-specific navigation, icons, dropdowns, badges |
| [crush_lu/context_processors.py](crush_lu/context_processors.py) | Created context processor for connection counts |
| [azureproject/settings.py](azureproject/settings.py#L105) | Registered context processor |

---

## Summary

The new navigation system:

✅ **Adapts to user role** (Guest, Dating User, Coach)
✅ **Shows relevant features only** (no clutter)
✅ **Provides visual feedback** (badges for pending actions)
✅ **Mobile-first responsive** (works on all screen sizes)
✅ **Icon-driven** (modern, intuitive)
✅ **Dropdown menus** (clean, organized)
✅ **Context-aware** (knows connection counts in real-time)

This creates a personalized, role-appropriate experience for every user! 🎉
