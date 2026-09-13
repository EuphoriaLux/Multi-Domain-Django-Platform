# Crush.lu email delivery health

This runbook covers the operational rollout of multipart email, reply routing,
and hard-bounce suppression. The code is safe to deploy with bounce processing
disabled.

## Deployment order

1. Deploy the application and run migration `0251_email_delivery_suppression`.
2. Confirm `support@crush.lu` is a real, monitored mailbox. If another mailbox
   owns customer replies, set `CRUSH_REPLY_TO_EMAIL` to that address before
   deployment.
3. Send one test email and inspect its raw source. It must contain both
   `text/plain` and `text/html`, retain any attachment, and carry `Reply-To`.
4. Grant the existing Microsoft Graph app registration **Mail.Read application
   permission** and record the administrator-consent approval. `Mail.Send`
   alone is not enough for reading delivery reports.
5. Confirm `CRUSH_EMAIL_BOUNCE_MAILBOXES` contains every Crush sender mailbox
   (currently `noreply@crush.lu` and `love@crush.lu`). NDRs normally arrive in
   Inbox, which is the `CRUSH_EMAIL_BOUNCE_FOLDER` default. If Exchange rules
   route them to dedicated folders, set `CRUSH_EMAIL_BOUNCE_FOLDERS` with an
   explicit Graph folder ID for every mailbox; folder IDs cannot be shared
   between mailboxes.
6. Inspect a genuine Microsoft 365 NDR and set
   `CRUSH_EMAIL_BOUNCE_TRUSTED_DOMAINS` to the exact tenant sender domain (for
   example `your-tenant.onmicrosoft.com`). Never configure a wildcard or the
   broad `onmicrosoft.com` parent. Processing additionally requires Exchange's
   unique internal-authentication and originating-direction headers plus
   structured delivery-status metadata.
7. Run a dry run from an application shell:

   ```powershell
   python manage.py process_email_bounces --days 14 --limit 100
   ```

   Review hard, soft, unknown, and ignored counts. Only messages with verified
   delivery-report metadata are classified; the classifier suppresses only a
   permanent failure with exactly one unambiguous external recipient. Stored
   diagnostics contain classification indicators, not the original mail body.
8. Set `CRUSH_EMAIL_BOUNCE_PROCESSING_ENABLED=true`, then repeat with `--apply`.
   Verify the new `Email bounce events` and `Email suppressions` records in the
   Crush coach admin.
9. Add a daily managed trigger that executes this command. The production task
   backend is inline and no database worker runs on Azure, so do not use
   `.enqueue()` as a scheduler. This repository change intentionally leaves the
   trigger unconfigured until `Mail.Read`, the dry-run review, and the rollout
   decision are complete. Alert on command failure and on a sudden rise in hard
   or unknown results.

## Rollback

- Set `CRUSH_EMAIL_BOUNCE_PROCESSING_ENABLED=false` to stop new suppression
  writes.
- Deactivate an individual suppression in the coach admin to allow that
  address again. Do not delete its bounce event; it is the audit trail.
- Set `CRUSH_REPLY_TO_EMAIL` back to the previous monitored address if reply
  handling needs to be rolled back.

Temporary failures such as mailbox-full, quota, timeout, or 4.x responses are
recorded as soft bounces and never create a suppression.
