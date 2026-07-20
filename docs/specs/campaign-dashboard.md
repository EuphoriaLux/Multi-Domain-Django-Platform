# Design Specification: Campaign & Remarketing Dashboard

This document defines the functional and technical specifications for the new **Campaign & Remarketing Dashboard** to be integrated into the Crush.lu Coach Panel (Django Admin).

---

## 1. Objectives & Overview
The dashboard will provide a centralized interface for coaches and superusers to manage outreach across three communication channels:
1.  **Email:** Composing and sending newsletter campaigns to targeting segments.
2.  **WhatsApp:** Monitoring delivery status of outbound messages, reviewing inbound support replies, and sending template-based outreach messages.
3.  **Web Push (PWA):** Broadcasting notifications to subscribed devices.

Additionally, it tracks **Automated Profile Reminders** and shows conversion metrics (how many nudged users completed their profile).

---

## 2. User Interface Design
The dashboard will live at `/crush-admin/campaign-dashboard/` and utilize a premium, tabbed interface styled to match the Coach Panel branding. It will use **Tailwind CSS** (via the existing compilation pipeline) and **Alpine.js** for reactive tab switching and preview renderings.

### Tab 1: Email Campaigns
*   **Analytics Cards:**
    *   *Total Sent Campaigns*
    *   *Total Sent Emails* (sum of all recipient receipts)
    *   *Active Drafts*
*   **Recent Campaigns Table:** List of the last 10 newsletters showing:
    *   Subject line, Target Segment, Target Language
    *   Status badge (`Draft`, `Sending`, `Sent`, `Failed`)
    *   Sent date & delivery statistics (Sent / Failed / Skipped)
*   **Inline Campaign Composer:** A form to create a new draft:
    *   Fields: Subject, Target Segment, Language, Body HTML, Event association (optional).
    *   Interactive preview of the template before sending.

### Tab 2: WhatsApp Outreach
*   **Analytics Cards:**
    *   *Total Outbound Messages*
    *   *Delivery Funnel:* Breakdown by status (`Queued`, `Sent`, `Delivered`, `Read`, `Failed`).
    *   *Inbound Replies:* Count of unread/total incoming messages.
*   **Outbound Messages Log:** List of the last 20 messages sent, showing recipient, template name, parameters, status, and time.
*   **Inbound Support Inbox:** List of the last 10 messages received from users (phone, name, text preview, time).
*   **Template-Based Sender Form:**
    *   Coaches can select a pre-approved Meta WhatsApp template (e.g. `event_reminder`, `profile_completion_nudge`).
    *   Input fields are generated dynamically for template parameters (like name, event details).
    *   Sends to the selected segment or individual user.

### Tab 3: Web Push/PWA Campaigns
*   **Analytics Cards:**
    *   *Active Subscriptions:* Count of active push tokens across PWA installations.
    *   *PWA Registrations:* Total registered PWA installations.
*   **Web Push Composer Form:**
    *   Fields: Title, Body, Target Link URL, Target Segment.
    *   Broadcast button to queue FCM/Web Push notifications to all matching active subscribers.

### Tab 4: Automated Reminders
*   **Analytics Cards:**
    *   *Total Reminders Sent:* Grouped by 24h, 72h, and 7d.
    *   *Funnel Conversion Rate:* Percentage of users who completed onboarding after receiving a reminder.
*   **Recent Reminders Log:** List of last 20 reminders showing user, reminder type, date sent, and their current profile verification status (`verified`, `pending`, `incomplete`).

### Tab 5: Remarketing Segments
*   **Segments List:** A table showing all 17+ segments from `user_segments.py` with:
    *   Segment name and description.
    *   **Live Count:** The current number of users matching the segment.
    *   **Quick Outreach Buttons:** "Compose Email", "Send WhatsApp", "Send Push" pre-targeted to this segment.

---

## 3. Technical Implementation

### URL Mapping
Add to `azureproject/urls_crush.py`:
```python
path('crush-admin/campaign-dashboard/', campaign_dashboard_view, name='campaign_dashboard'),
```

### Dashboard Controller (`crush_lu/admin/campaign_dashboard.py`)
A custom view controller decorated with `@login_required` and restricted to superusers and active coaches. It gathers database aggregates:
*   `Newsletter.objects.all()`
*   `WhatsAppMessage.objects.all()`
*   `WhatsAppInboundMessage.objects.all()`
*   `ProfileReminder.objects.all()`
*   Dynamic counts from `get_segment_definitions()` in `user_segments.py`.

### Daily Automation Task (`crush_lu/tasks.py`)
Register a background task utilizing Django 6.0's native task framework to run the daily profile completion reminders:
```python
@task(priority=1)
def run_daily_profile_reminders_task():
    """Daily job that sends 24h, 72h, and 7d reminders to incomplete profiles."""
    from django.core.management import call_command
    logger.info("[TASK] Running daily profile reminders...")
    try:
        call_command("send_profile_reminders", type="all", verbosity=1)
        logger.info("[TASK] Daily profile reminders sent successfully")
    except Exception as e:
        logger.error(f"[TASK] Failed to run daily profile reminders: {e}")
```

---

## 4. Verification Plan

### Unit Tests
Create `crush_lu/tests/test_campaign_dashboard.py` to test:
1.  **Access Control:** Access is blocked for anonymous/non-staff users, but allowed for superusers and active coaches.
2.  **Stats Aggregation:** Dashboard metrics match database records.
3.  **Reminders Task:** Calling `run_daily_profile_reminders_task` enqueues and runs without errors.
