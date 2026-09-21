# Arborist enquiry intake

The existing `/en|de|fr/kontakt/` form saves an `ArboristLead` before notification.
Customers may provide email or phone, then add up to 12 categorised photos and
notes in their submitting browser session. The receipt is not an emailed public
access link. Losing that session requires contacting the arborist.

## Release prerequisites

- Apply Arborist migration 0002. No existing bookings are rewritten.
- Provision a dedicated Azure Blob container with **Private (no anonymous access)**.
  Set `AZURE_ARBORIST_PRIVATE_CONTAINER` for each deployment slot (default
  `arborist-private`); use a separate staging container. Existing account settings
  supply credentials. Reads/writes check container privacy and fail closed.
- No CDN/public URL is generated. Photos pass through a session/permission-checked
  view with no-store. Local files use `private-arborist/`, outside MEDIA_ROOT;
  never mount this directory in a public web server.
- Uploaded JPEG/PNG/WebP images are limited to 8 MB and 24 megapixels, resized to
  at most 2400 pixels and re-encoded without original metadata. HEIC is not
  supported in this release; the form asks for supported formats.
- Give authorised staff Arborist lead permissions and related photo/event view
  permissions in Django. Private-photo retrieval requires lead view permission.
- Validate EN/DE/FR form, photo upload and admin permissions on staging. Do not
  submit production smoke-test enquiries that would email real customers.

## Operations

Use `/arborist-admin/` to assign an owner, next action, follow-up date and status.
Filter overdue follow-ups or failed notifications. Each mail leg is tracked
separately; retry only unsent legs using the bounded admin action (one lead at a time).
Email is attempted after the enquiry transaction commits, never before saving it.
The small notification callback is synchronous; it is not a background queue.
Pending notifications left after a process crash remain visible for staff retry.
If a mail was accepted just before a crash, check mail logs before retrying:
provider delivery and database status cannot be made exactly-once atomically.

The source defaults to website and can be recorded manually for phone/referral
leads. Campaign attribution captures only allowlisted campaign labels and public
landing paths after explicit analytics consent. First/last touch is browser-session
scoped; unknown or withdrawn consent clears the attribution session. Do not put
personal data in campaign labels. No click-ID or customer-data export is enabled.

Enquiry photos are not used for AI training or public marketing. Staff can delete
photos/enquiries under the business's retention policy; files are removed after
the database deletion commits. Establish the retention period and published
privacy details with the operator before production rollout. Failed storage
deletions are logged and require operator reconciliation.

This release does not implement automatic diagnosis, market-price estimation,
quote/invoice/payment accounting, commission payouts, Sheets synchronisation or
paid campaigns. Those are subsequent phases of the growth plan.

## Validation

Run `python -m pytest arborist/tests -n 0`, `python manage.py check`,
`python manage.py makemigrations --check --dry-run`, and scoped Ruff checks using
the project virtual environment and a local test SECRET_KEY. No production
credentials or copied production `.env` are needed for the unit tests.
