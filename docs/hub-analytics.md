# Hub analytics

`GET /hub/analytics/overview?days=28` provides the staff Hub with read-only,
aggregate data from four sources. The accepted periods are 7, 28, and 90 days.
The route requires a Django staff user and never returns member-level rows or
provider credentials.

## Sources

- Google Search Console: discovery totals, daily history, landing pages, and the
  top 100 search queries (`search.queries`).
- Google Analytics 4: sessions, users, engagement, and landing pages.
- Azure Application Insights: authenticated Crush.lu page activity, custom
  events, and exception counts, adjusted for telemetry sampling.
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
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | empty | Existing slot-sticky Azure setting; its `ApplicationId` enables read-only query access. |
| `HUB_ANALYTICS_CACHE_SECONDS` | `900` | Aggregate response cache duration. |
| `HUB_ANALYTICS_HTTP_TIMEOUT_SECONDS` | `15` | External-provider HTTP timeout and Search Console total request budget. |

No Google or Azure secret belongs in the frontend build. Before enabling the
external sources:

1. Grant the service account in `GOOGLE_INDEXING_KEY_JSON` read access to the
   Search Console property and Viewer access to GA4 property `516337382`.
2. Enable the Django App Service managed identity and grant it a read-only
   Azure Monitor role on the Application Insights resource.
3. Ensure the slot-sticky `APPLICATIONINSIGHTS_CONNECTION_STRING` contains
   `ApplicationId`; the query API derives its identifier from that existing
   setting, so no separate cross-slot companion setting is required.

After deploying, sign in to `hub.crush.lu` as staff, open **Analytics**, and
verify the source-status panel before using the figures operationally.
Application Insights uses the same delayed reporting window as Search Console
and GA4, with calendar boundaries and daily buckets in `Europe/Luxembourg`.

## Search Console for agents

`GET /api/analytics/search/` (Bearer `ANALYTICS_API_KEY`, same dark gate and
rate limit as the other analytics tools) returns aggregate Search Console rows
for read-only analysis. It touches no database.

| Parameter | Default | Notes |
| --- | --- | --- |
| `dimensions` | `query` | 1-3 of `query`, `page`, `country`, `device`, `date`, comma-separated |
| `from` / `to` | last 90 days ending 3 days ago | Search Console keeps 16 months; range up to 486 days |
| `limit` | `250` | 1-1000 rows |

Example: `/api/analytics/search/?dimensions=query,page&limit=500`. Google
already withholds rare queries, so every row aggregates many searches. Results
are cached for one hour.
