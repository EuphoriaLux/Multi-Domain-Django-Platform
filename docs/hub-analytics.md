# Hub analytics

`GET /hub/analytics/overview?days=28` provides the staff Hub with read-only,
aggregate data from four sources. The accepted periods are 7, 28, and 90 days.
The route requires a Django staff user and never returns member-level rows or
provider credentials.

## Sources

- Google Search Console: discovery totals, daily history, and landing pages.
- Google Analytics 4: sessions, users, engagement, and landing pages.
- Azure Application Insights: authenticated page activity, custom events, and
  exception counts.
- Crush.lu: current profile-verification counts and persisted weekly KPI
  snapshots.

Search Console and GA4 are compared by date and normalized landing-page path
only. They are not joined at user level. Weekly signup and verification values
are trend indicators, not cohort conversion rates.

Each external provider is isolated. A missing permission or temporary upstream
failure marks only that source unavailable; the endpoint still returns the
other aggregates. Results are cached server-side for 15 minutes by default.

## Configuration

| Setting | Default | Purpose |
| --- | --- | --- |
| `GOOGLE_INDEXING_KEY_JSON` | empty | Existing server-side Google service-account JSON. |
| `HUB_ANALYTICS_GSC_SITE_URL` | `sc-domain:crush.lu` | Search Console property. |
| `HUB_ANALYTICS_GA4_PROPERTY_ID` | `516337382` | GA4 property ID. |
| `HUB_ANALYTICS_APP_INSIGHTS_APP_ID` | empty | Optional Application Insights application ID override used by the query API. |
| `HUB_ANALYTICS_CACHE_SECONDS` | `900` | Aggregate response cache duration. |
| `HUB_ANALYTICS_HTTP_TIMEOUT_SECONDS` | `15` | Application Insights HTTP timeout. |

No Google or Azure secret belongs in the frontend build. Before enabling the
external sources:

1. Grant the service account in `GOOGLE_INDEXING_KEY_JSON` read access to the
   Search Console property and Viewer access to GA4 property `516337382`.
2. Enable the Django App Service managed identity and grant it a read-only
   Azure Monitor role on the Application Insights resource.
3. Ensure `APPLICATIONINSIGHTS_CONNECTION_STRING` contains `ApplicationId`.
   Otherwise, set `HUB_ANALYTICS_APP_INSIGHTS_APP_ID` to the Application
   Insights application ID as an explicit override. This is not the
   instrumentation key or Azure resource ID.

After deploying, sign in to `hub.crush.lu` as staff, open **Analytics**, and
verify the source-status panel before using the figures operationally.
