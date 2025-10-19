# Push Notifications Implementation Guide for Crush.lu

This guide provides a complete implementation of Web Push Notifications for the Crush.lu platform.

## Overview

We'll implement browser-based push notifications that allow users to receive:
- Event reminders
- New connection requests
- New messages
- Profile approval notifications
- Journey updates

## Implementation Steps

### 1. Install Dependencies

Add to `requirements.txt`:
```txt
# Push Notifications
pywebpush==1.14.1
django-webpush==0.3.5
```

Then run:
```bash
pip install pywebpush==1.14.1 django-webpush==0.3.5
```

### 2. Configure Django Settings

Add to `azureproject/settings.py`:

```python
INSTALLED_APPS = [
    # ... existing apps
    'webpush',
]

# Web Push Settings
WEBPUSH_SETTINGS = {
    "VAPID_PUBLIC_KEY": os.environ.get('VAPID_PUBLIC_KEY', ''),
    "VAPID_PRIVATE_KEY": os.environ.get('VAPID_PRIVATE_KEY', ''),
    "VAPID_ADMIN_EMAIL": "noreply@crush.lu"
}
```

### 3. Generate VAPID Keys

Run this command to generate keys:
```bash
python manage.py generate_vapid_keys
```

Save the output to your `.env` file:
```env
VAPID_PUBLIC_KEY=<your-public-key>
VAPID_PRIVATE_KEY=<your-private-key>
```

### 4. Create Notification Models

Add to `crush_lu/models.py`:

```python
class NotificationPreference(models.Model):
    """User notification preferences"""

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='notification_preferences')

    # Email Notifications
    email_event_reminders = models.BooleanField(default=True)
    email_new_messages = models.BooleanField(default=True)
    email_connection_requests = models.BooleanField(default=True)
    email_profile_updates = models.BooleanField(default=True)

    # Push Notifications
    push_enabled = models.BooleanField(default=False)
    push_event_reminders = models.BooleanField(default=True)
    push_new_messages = models.BooleanField(default=True)
    push_connection_requests = models.BooleanField(default=True)
    push_profile_updates = models.BooleanField(default=True)
    push_journey_updates = models.BooleanField(default=True)

    # Timing preferences
    quiet_hours_start = models.TimeField(null=True, blank=True, help_text="No notifications after this time")
    quiet_hours_end = models.TimeField(null=True, blank=True, help_text="No notifications before this time")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Notification Preferences for {self.user.username}"

    @property
    def is_quiet_hours(self):
        """Check if current time is within quiet hours"""
        if not self.quiet_hours_start or not self.quiet_hours_end:
            return False

        from datetime import datetime
        now = datetime.now().time()

        if self.quiet_hours_start < self.quiet_hours_end:
            return self.quiet_hours_start <= now <= self.quiet_hours_end
        else:  # Quiet hours span midnight
            return now >= self.quiet_hours_start or now <= self.quiet_hours_end


class PushSubscription(models.Model):
    """Store push notification subscriptions"""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='push_subscriptions')
    subscription_info = models.JSONField(help_text="Browser subscription object")
    device_name = models.CharField(max_length=200, blank=True, help_text="Browser/Device identifier")
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    last_used = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ['user', 'subscription_info']

    def __str__(self):
        return f"{self.user.username} - {self.device_name or 'Unknown Device'}"
```

### 5. Create Migration

```bash
python manage.py makemigrations crush_lu
python manage.py migrate
```

### 6. Add URL Patterns

Add to `crush_lu/urls.py`:

```python
from django.urls import path, include

urlpatterns = [
    # ... existing patterns

    # Push notifications
    path('webpush/', include('webpush.urls')),
    path('notifications/subscribe/', views.subscribe_push, name='subscribe_push'),
    path('notifications/preferences/', views.notification_preferences, name='notification_preferences'),
]
```

### 7. Create Views

Add to `crush_lu/views.py`:

```python
from django.http import JsonResponse
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_http_methods
from webpush import send_user_notification
import json

from .models import NotificationPreference, PushSubscription


@login_required
@require_http_methods(["POST"])
def subscribe_push(request):
    """Subscribe user to push notifications"""
    try:
        data = json.loads(request.body)
        subscription_info = data.get('subscription')
        device_name = data.get('device_name', '')

        # Create or update subscription
        subscription, created = PushSubscription.objects.update_or_create(
            user=request.user,
            subscription_info=subscription_info,
            defaults={'device_name': device_name, 'is_active': True}
        )

        # Enable push in preferences
        prefs, _ = NotificationPreference.objects.get_or_create(user=request.user)
        prefs.push_enabled = True
        prefs.save()

        return JsonResponse({
            'success': True,
            'message': 'Push notifications enabled!'
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=400)


@login_required
def notification_preferences(request):
    """View and update notification preferences"""
    prefs, created = NotificationPreference.objects.get_or_create(user=request.user)

    if request.method == 'POST':
        # Update preferences
        prefs.email_event_reminders = request.POST.get('email_event_reminders') == 'on'
        prefs.email_new_messages = request.POST.get('email_new_messages') == 'on'
        prefs.email_connection_requests = request.POST.get('email_connection_requests') == 'on'
        prefs.email_profile_updates = request.POST.get('email_profile_updates') == 'on'

        prefs.push_event_reminders = request.POST.get('push_event_reminders') == 'on'
        prefs.push_new_messages = request.POST.get('push_new_messages') == 'on'
        prefs.push_connection_requests = request.POST.get('push_connection_requests') == 'on'
        prefs.push_profile_updates = request.POST.get('push_profile_updates') == 'on'
        prefs.push_journey_updates = request.POST.get('push_journey_updates') == 'on'

        # Quiet hours
        quiet_start = request.POST.get('quiet_hours_start')
        quiet_end = request.POST.get('quiet_hours_end')
        prefs.quiet_hours_start = quiet_start if quiet_start else None
        prefs.quiet_hours_end = quiet_end if quiet_end else None

        prefs.save()

        messages.success(request, 'Notification preferences updated!')
        return redirect('crush_lu:notification_preferences')

    context = {
        'prefs': prefs,
        'push_subscriptions': request.user.push_subscriptions.filter(is_active=True),
    }
    return render(request, 'crush_lu/notification_preferences.html', context)


def send_notification(user, title, body, notification_type, url=None):
    """
    Send push notification to user

    Args:
        user: User object
        title: Notification title
        body: Notification body text
        notification_type: Type of notification (event, message, connection, etc.)
        url: Optional URL to open when clicked
    """
    try:
        # Check user preferences
        prefs = NotificationPreference.objects.filter(user=user).first()

        if not prefs or not prefs.push_enabled:
            return False

        # Check if user wants this type of notification
        type_mapping = {
            'event': prefs.push_event_reminders,
            'message': prefs.push_new_messages,
            'connection': prefs.push_connection_requests,
            'profile': prefs.push_profile_updates,
            'journey': prefs.push_journey_updates,
        }

        if notification_type in type_mapping and not type_mapping[notification_type]:
            return False  # User disabled this notification type

        # Check quiet hours
        if prefs.is_quiet_hours:
            return False  # Don't send during quiet hours

        # Prepare payload
        payload = {
            'head': title,
            'body': body,
            'icon': '/static/crush_lu/img/crush-logo.png',
            'url': url or 'https://crush.lu/',
        }

        # Send to all active subscriptions
        subscriptions = user.push_subscriptions.filter(is_active=True)
        for subscription in subscriptions:
            try:
                send_user_notification(
                    user=user,
                    payload=payload,
                    subscription=subscription.subscription_info
                )
            except Exception as e:
                # Mark subscription as inactive if it fails
                subscription.is_active = False
                subscription.save()

        return True

    except Exception as e:
        print(f"Error sending notification: {e}")
        return False
```

### 8. Create Service Worker

Create `static/crush_lu/js/service-worker.js`:

```javascript
// Service Worker for Push Notifications

self.addEventListener('push', function(event) {
    const data = event.data.json();
    const options = {
        body: data.body,
        icon: data.icon || '/static/crush_lu/img/crush-logo.png',
        badge: '/static/crush_lu/img/crush-badge.png',
        vibrate: [200, 100, 200],
        data: {
            url: data.url || 'https://crush.lu/'
        },
        actions: [
            {action: 'open', title: 'Open'},
            {action: 'close', title: 'Close'}
        ]
    };

    event.waitUntil(
        self.registration.showNotification(data.head, options)
    );
});

self.addEventListener('notificationclick', function(event) {
    event.notification.close();

    if (event.action === 'open' || !event.action) {
        event.waitUntil(
            clients.openWindow(event.notification.data.url)
        );
    }
});
```

### 9. Create Push Notification JavaScript

Create `static/crush_lu/js/push-notifications.js`:

```javascript
// Push Notification Management

class PushNotificationManager {
    constructor() {
        this.vapidPublicKey = null;
        this.subscription = null;
    }

    async init(vapidPublicKey) {
        this.vapidPublicKey = vapidPublicKey;

        // Check if service workers are supported
        if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
            console.log('Push notifications not supported');
            return false;
        }

        // Register service worker
        try {
            const registration = await navigator.serviceWorker.register('/static/crush_lu/js/service-worker.js');
            console.log('Service Worker registered:', registration);
            return registration;
        } catch (error) {
            console.error('Service Worker registration failed:', error);
            return false;
        }
    }

    async subscribe() {
        try {
            const registration = await navigator.serviceWorker.ready;

            // Request permission
            const permission = await Notification.requestPermission();
            if (permission !== 'granted') {
                alert('Please enable notifications to receive updates!');
                return false;
            }

            // Subscribe to push
            const subscription = await registration.pushManager.subscribe({
                userVisibleOnly: true,
                applicationServerKey: this.urlBase64ToUint8Array(this.vapidPublicKey)
            });

            this.subscription = subscription;

            // Send subscription to server
            await this.sendSubscriptionToServer(subscription);

            return true;
        } catch (error) {
            console.error('Failed to subscribe:', error);
            return false;
        }
    }

    async sendSubscriptionToServer(subscription) {
        const deviceName = this.getDeviceName();

        const response = await fetch('/notifications/subscribe/', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': this.getCookie('csrftoken')
            },
            body: JSON.stringify({
                subscription: subscription.toJSON(),
                device_name: deviceName
            })
        });

        return response.json();
    }

    getDeviceName() {
        const ua = navigator.userAgent;
        if (ua.includes('Chrome')) return 'Chrome';
        if (ua.includes('Firefox')) return 'Firefox';
        if (ua.includes('Safari')) return 'Safari';
        if (ua.includes('Edge')) return 'Edge';
        return 'Unknown Browser';
    }

    urlBase64ToUint8Array(base64String) {
        const padding = '='.repeat((4 - base64String.length % 4) % 4);
        const base64 = (base64String + padding)
            .replace(/-/g, '+')
            .replace(/_/g, '/');

        const rawData = window.atob(base64);
        const outputArray = new Uint8Array(rawData.length);

        for (let i = 0; i < rawData.length; ++i) {
            outputArray[i] = rawData.charCodeAt(i);
        }
        return outputArray;
    }

    getCookie(name) {
        let cookieValue = null;
        if (document.cookie && document.cookie !== '') {
            const cookies = document.cookie.split(';');
            for (let i = 0; i < cookies.length; i++) {
                const cookie = cookies[i].trim();
                if (cookie.substring(0, name.length + 1) === (name + '=')) {
                    cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                    break;
                }
            }
        }
        return cookieValue;
    }
}

// Initialize when page loads
const pushManager = new PushNotificationManager();
```

### 10. Usage Examples

#### Send Event Reminder
```python
from crush_lu.views import send_notification

# When event is 1 hour away
send_notification(
    user=user,
    title="Event Reminder 🎉",
    body=f"Your event '{event.title}' starts in 1 hour!",
    notification_type='event',
    url=f'https://crush.lu/events/{event.id}/'
)
```

#### Send New Message Notification
```python
# When user receives a new message
send_notification(
    user=recipient,
    title=f"New message from {sender.first_name}",
    body=message.message[:100],
    notification_type='message',
    url=f'https://crush.lu/connections/{connection.id}/'
)
```

#### Send Connection Request
```python
# When someone requests to connect
send_notification(
    user=recipient,
    title="New Connection Request 💕",
    body=f"{requester.first_name} wants to connect with you!",
    notification_type='connection',
    url='https://crush.lu/connections/'
)
```

### 11. Template Integration

Add to `crush_lu/templates/crush_lu/base.html`:

```html
{% load static %}

<script src="{% static 'crush_lu/js/push-notifications.js' %}"></script>
<script>
    // Initialize push notifications
    document.addEventListener('DOMContentLoaded', async () => {
        const vapidPublicKey = '{{ WEBPUSH_SETTINGS.VAPID_PUBLIC_KEY }}';
        await pushManager.init(vapidPublicKey);
    });
</script>
```

Add notification preferences link to user menu:

```html
<a href="{% url 'crush_lu:notification_preferences' %}" class="dropdown-item">
    <i class="bi bi-bell"></i> Notification Preferences
</a>
```

### 12. Create Preferences Template

Create `crush_lu/templates/crush_lu/notification_preferences.html`:

```html
{% extends "crush_lu/base.html" %}

{% block title %}Notification Preferences{% endblock %}

{% block content %}
<div class="container mt-4">
    <h2><i class="bi bi-bell"></i> Notification Preferences</h2>

    <form method="post" class="mt-4">
        {% csrf_token %}

        <!-- Push Notifications Section -->
        <div class="card mb-4">
            <div class="card-header bg-primary text-white">
                <h5 class="mb-0"><i class="bi bi-phone"></i> Push Notifications</h5>
            </div>
            <div class="card-body">
                <div id="push-status" class="alert alert-info">
                    <i class="bi bi-info-circle"></i> Push notifications are
                    {% if prefs.push_enabled %}
                        <strong>enabled</strong>
                    {% else %}
                        <strong>disabled</strong>
                    {% endif %}
                </div>

                <button type="button" id="enable-push-btn" class="btn btn-crush-primary mb-3">
                    <i class="bi bi-bell-fill"></i> Enable Push Notifications
                </button>

                <div class="form-check">
                    <input type="checkbox" class="form-check-input" id="push_event_reminders"
                           name="push_event_reminders" {% if prefs.push_event_reminders %}checked{% endif %}>
                    <label class="form-check-label" for="push_event_reminders">
                        Event reminders
                    </label>
                </div>

                <div class="form-check">
                    <input type="checkbox" class="form-check-input" id="push_new_messages"
                           name="push_new_messages" {% if prefs.push_new_messages %}checked{% endif %}>
                    <label class="form-check-label" for="push_new_messages">
                        New messages
                    </label>
                </div>

                <div class="form-check">
                    <input type="checkbox" class="form-check-input" id="push_connection_requests"
                           name="push_connection_requests" {% if prefs.push_connection_requests %}checked{% endif %}>
                    <label class="form-check-label" for="push_connection_requests">
                        Connection requests
                    </label>
                </div>

                <div class="form-check">
                    <input type="checkbox" class="form-check-input" id="push_journey_updates"
                           name="push_journey_updates" {% if prefs.push_journey_updates %}checked{% endif %}>
                    <label class="form-check-label" for="push_journey_updates">
                        Journey updates
                    </label>
                </div>
            </div>
        </div>

        <!-- Email Notifications Section -->
        <div class="card mb-4">
            <div class="card-header bg-secondary text-white">
                <h5 class="mb-0"><i class="bi bi-envelope"></i> Email Notifications</h5>
            </div>
            <div class="card-body">
                <div class="form-check">
                    <input type="checkbox" class="form-check-input" id="email_event_reminders"
                           name="email_event_reminders" {% if prefs.email_event_reminders %}checked{% endif %}>
                    <label class="form-check-label" for="email_event_reminders">
                        Event reminders
                    </label>
                </div>

                <div class="form-check">
                    <input type="checkbox" class="form-check-input" id="email_new_messages"
                           name="email_new_messages" {% if prefs.email_new_messages %}checked{% endif %}>
                    <label class="form-check-label" for="email_new_messages">
                        New messages
                    </label>
                </div>

                <div class="form-check">
                    <input type="checkbox" class="form-check-input" id="email_connection_requests"
                           name="email_connection_requests" {% if prefs.email_connection_requests %}checked{% endif %}>
                    <label class="form-check-label" for="email_connection_requests">
                        Connection requests
                    </label>
                </div>
            </div>
        </div>

        <!-- Quiet Hours Section -->
        <div class="card mb-4">
            <div class="card-header bg-info text-white">
                <h5 class="mb-0"><i class="bi bi-moon"></i> Quiet Hours</h5>
            </div>
            <div class="card-body">
                <p class="text-muted">Don't send notifications during these hours</p>

                <div class="row">
                    <div class="col-md-6">
                        <label for="quiet_hours_start">Start Time</label>
                        <input type="time" class="form-control" id="quiet_hours_start"
                               name="quiet_hours_start" value="{{ prefs.quiet_hours_start|time:'H:i' }}">
                    </div>
                    <div class="col-md-6">
                        <label for="quiet_hours_end">End Time</label>
                        <input type="time" class="form-control" id="quiet_hours_end"
                               name="quiet_hours_end" value="{{ prefs.quiet_hours_end|time:'H:i' }}">
                    </div>
                </div>
            </div>
        </div>

        <button type="submit" class="btn btn-primary">
            <i class="bi bi-save"></i> Save Preferences
        </button>
    </form>
</div>

<script>
document.getElementById('enable-push-btn').addEventListener('click', async function() {
    const success = await pushManager.subscribe();
    if (success) {
        document.getElementById('push-status').innerHTML =
            '<i class="bi bi-check-circle"></i> Push notifications are <strong>enabled</strong>';
        document.getElementById('push-status').className = 'alert alert-success';
    }
});
</script>
{% endblock %}
```

### 13. Azure Environment Variables

Add to Azure App Service configuration:

```bash
az webapp config appsettings set \
    --name django-app-ajfffwjb5ie3s-app-service \
    --resource-group rg-django-app-ajfffwjb5ie3s \
    --settings \
    VAPID_PUBLIC_KEY="<your-public-key>" \
    VAPID_PRIVATE_KEY="<your-private-key>"
```

## Testing

1. **Local Testing**:
   - Run server with HTTPS (required for push): `python manage.py runserver_plus --cert-file cert.crt`
   - Or use ngrok: `ngrok http 8000`

2. **Test Notification**:
```python
python manage.py shell

from django.contrib.auth.models import User
from crush_lu.views import send_notification

user = User.objects.first()
send_notification(
    user=user,
    title="Test Notification",
    body="This is a test!",
    notification_type='event'
)
```

## Next Steps

1. Install dependencies
2. Generate VAPID keys
3. Create models and migrations
4. Add views and URLs
5. Create JavaScript files
6. Test locally
7. Deploy to production
8. Integrate into existing features (events, messages, connections)

## Browser Support

- ✅ Chrome/Edge (Desktop & Android)
- ✅ Firefox (Desktop & Android)
- ✅ Safari (macOS 16+, iOS 16.4+)
- ❌ Safari (older versions)

## Future Enhancements

- Badge counts for unread notifications
- Notification history/center
- Group notifications
- Rich media in notifications (images)
- Action buttons in notifications
- Desktop notification sounds
