# INKORA walkthrough

This walkthrough covers the working **private staging pilot**. It does not authorize a sales launch. The first kiosk and its Firebase resources remain separate. The pilot never takes real payments or prints physical sheets.

## What you can do now

Admin login → owner invitation → kiosk registration → device activation → heartbeat → customer QR → private PDF upload → test quote → device claim → checksum verification → simulated completion → admin review/retention.

Every page uses the same theme in `inkora/static/theme.css`: black backgrounds, cyan links, violet controls and the original pink accent. Owner pages query only that owner's kiosks and jobs. Devices derive kiosk identity from credentials, never a kiosk ID supplied by the client.

## 1. Prepare the separate fleet environment

Use `/workspace/INKORA-`; do not modify or migrate kiosk #1. Follow README for Python dependencies and the isolated PostgreSQL container. Set a stable `INKORA_SESSION_SECRET` securely, set `DATABASE_URL` to the **new fleet database**, and apply:

```bash
.venv/bin/flask --app inkora:create_app db upgrade
.venv/bin/flask --app inkora:create_app db check
.venv/bin/flask --app inkora:create_app create-admin
```

The admin CLI asks for name, email and password without echoing the password. There are no shipped default credentials. Start Gunicorn with the configuration in `deploy/gunicorn.conf.py`; verify `/health/ready` and the login form with local requests. Use environment settings for secrets, not GitHub files or chat. Generating another session secret invalidates sessions; losing the encryption key makes enrolled MFA secrets unusable.

For hosted staging use `INKORA_ENV=staging`, separate `INKORA_CLOUD_PROJECT`, private `INKORA_GCS_BUCKET`, an HTTPS `INKORA_PUBLIC_URL`, PostgreSQL, and `INKORA_ENCRYPTION_KEY` (a Fernet key). Cloud staging refuses missing configuration. Live `INKORA_ENV=production` blocks all simulated print endpoints; simply changing this flag does **not** enable a real payment or printer adapter.

## 2. Sign in and secure the admin account

Open the hosted portal's `/login`, sign in, then open **Account security**. Add the displayed setup key to your authenticator and enter its six-digit code. Hosted staging requires this before accessing admin screens. On subsequent sign-ins, enter a fresh authenticator code; reuse of a code is rejected. MFA secrets are encrypted before they enter the database.

If the authenticator is lost, a trusted operator with controlled console access can run `reset-admin-mfa`, confirm the action, and re-enroll. This is audited and revokes all existing sessions. It is not a public recovery endpoint. Protect console/IAM access separately.

## 3. Invite an owner

On the dashboard, enter the owner's name and email under **Create owner**. The portal displays an expiring private invitation link. Verify the recipient and share the link privately; no email is sent automatically. The owner chooses their password. The link works once and expires after 30 minutes.

Under **Owner access**, admins can issue another recovery link or disable an owner. Password reset revokes existing owner sessions. Disabled owners cannot sign in, and their device requests are rejected. There is currently no self-service email recovery provider; manual verified link sharing is the supported pilot workflow.

Sign in as a second owner to verify they cannot see the first owner's fleet. Owners cannot create other owners, activate another owner's kiosk, revoke devices or resolve admin review actions.

## 4. Register and activate a kiosk

Choose the owner, kiosk name and location, then select **Register kiosk**. Its status is initially **Not activated**. Select **Issue code**. Activation codes are single-use and expire in 15 minutes; issuing another replaces the previous code.

On a separate terminal run the hardware-free device:

```bash
.venv/bin/python tools/kiosk_simulator.py \
  --url YOUR_PORTAL_BASE_URL \
  --identity /tmp/inkora-device.json \
  --activate --once
```

Use the configured portal base URL; local development accepts loopback HTTP and other deployments require HTTPS. The activation code is prompted without echoing it. The simulator saves the identity file with owner-only permissions. Store this outside source control and never put the device token in a tablet page.

Admins can open **Pricing** beside each kiosk and set its own rates. Existing jobs keep their original amount and quote snapshot; device configuration is available through its authenticated configuration API.

A ready heartbeat changes the dashboard to **Online**. Sending `--status paper_empty`, `error` or `busy` changes it to **Needs attention**. Stop heartbeats for more than 90 seconds and it becomes **Offline**. No new job/session can start for a kiosk that is stale or not ready.

Admins can use **Revoke device** to invalidate credentials and then re-enroll. The device rotation API supports a 60-second old-token retry window; proposed replacement credentials must be cryptographically random. Credentials from one kiosk cannot access another kiosk's jobs.

## 5. Run the customer QR workflow

Send a ready heartbeat and ask the simulator to write a QR:

```bash
.venv/bin/python tools/kiosk_simulator.py \
  --url YOUR_PORTAL_BASE_URL \
  --identity /tmp/inkora-device.json \
  --customer-qr /tmp/inkora-customer.png --once
```

Use a new PNG filename each time; existing files are preserved. Display that QR locally and scan it on the test phone. For local development, a loopback QR will only work on the same computer: use hosted staging for a real phone, rather than weakening the HTTPS rule.

The QR expires after three minutes. The first phone browser is bound to the session; a second browser cannot reuse it. Only one active customer session is allowed per kiosk. Rendering the QR itself does not consume the phone's browser binding.

Upload a synthetic PDF, choose black & white/color/art and single/double sided. Maximum: 10 MB and 250 pages. Malformed and encrypted PDFs are rejected; validation runs in a process with CPU, memory and timeout limits. Refreshing or retrying the upload does not create another job.

The customer sees the immutable **test quote** and job state. No payment is collected; no earnings or 10% settlement is booked. All pricing is integer paise with the reference portal's bulk-discount rounding. The currently supported customer status capability ends one day after session expiry.

## 6. Process a simulated job

Run:

```bash
.venv/bin/python tools/kiosk_simulator.py \
  --url YOUR_PORTAL_BASE_URL \
  --identity /tmp/inkora-device.json \
  --journal /tmp/inkora-spool.db --jobs --once
```

The agent claims only its kiosk's queue, downloads the private document with a short-lived claim, checks SHA-256, and reports simulated printing and completion. Each kiosk can hold one active job. The durable journal records progress before transitions. An unresolved journal entry blocks further claiming after restart instead of silently replaying a print.

The API also supports lease renewal while a valid claim is active. The simulator's simple PDF simulation completes quickly; a physical agent must renew leases and reconcile printer submission IDs. A failed checksum stops completion. Completed-job duplicate transitions conflict rather than requeueing output.

The **Simulated printing** screen shows jobs. Owners see their own jobs; admins see all. The customer can refresh their upload page to see the result. Customer requests never expose the private document URL or permanent device token.

## 7. Review failures and retain less data

Expired claims become **needs_review**. The maintenance command makes this happen even if the device never polls again. An admin can cancel or explicitly retry with a recorded reason. Retrying requires confirmation that no physical output occurred and the document must still exist. This is an operator judgment, not a guarantee of exactly-once physical printing.

Schedule:

```bash
.venv/bin/flask --app inkora:create_app maintenance --retention-hours 24
```

It cancels old queued jobs, deletes old terminal/review documents, records deletion and prunes expired rate/recovery records. It never automatically requeues an ambiguous print. Run it as a separate scheduled container job every 15 minutes; do not depend on a background thread in Cloud Run. Review stale spool entries alongside the cloud review result before clearing a local journal. Avoid customer-sensitive documents throughout the pilot.

Private GCS downloads are authenticated by the backend and generation-pinned. The bucket should use uniform access, public access prevention, no public grants, and the supplied two-day lifecycle as a fallback to scheduled 24-hour deletion. Soft-delete/version retention must match the business deletion policy. The code's GCS adapter contract is tested; actual cloud IAM, encryption, lifecycle and connectivity are **not yet verified**.

## 8. What GitHub and hosting do

The code is built/tested in this cloud checkout. GitHub stores commits and runs the included CI checks once the code is pushed. Virtual environments, identity files, secrets, PDFs, database files, journals and build wheels are excluded from Git. Hosting runs the service separately.

`Dockerfile` builds a non-root image with a pinned Python base and hash-verified runtime wheels. `deploy/cloudrun-staging.yaml` is a reviewable template, not an applied deployment. It limits instances and concurrency and uses Secret Manager references. Migrations are a separate release step, not something every web worker runs.

Use an isolated project, database, bucket, service account and billing budget. Do not reuse kiosk #1's resources. Keep Cloud Run IAM authenticated for the private pilot; a real phone/device needs an approved access path. Do not silently grant public invocation just to make scanning convenient. Public launch also needs a tested edge abuse-control strategy and approved customer policies.

## 9. Verification available now

- PostgreSQL functional/security tests, including concurrent activation and job claims.
- MFA encryption/replay rejection, invitation/reset/session revocation, disabled-account access.
- Customer QR expiry/browser binding and duplicate-upload protection.
- Cross-owner/device denial, private download checks, PDF validation, lease/review/retention.
- Retry-safe device rotation and durable simulator restart handling.
- Signature/checksum verification primitive for an offline agent release; no automatic updater/rollout is claimed.
- Container build/startup, non-root process, dependency audit and synthetic backup/restore.
- 100-device local heartbeat smoke test; see `VALIDATION.md` for measured scope.

Run the documented PostgreSQL tests against **only** the dedicated `inkora_test` database. They deliberately reset that database. The backup/restore and fleet-load scripts create isolated synthetic targets and leave them for inspection. Neither may be pointed at a production database.

## 10. Live launch still requires outside evidence

Before accepting a customer's money or selling a production kiosk:

1. Supply the separate cloud project, region, domain, PostgreSQL provider, service account and private bucket. Confirm IAM, TLS, scheduled maintenance, alerts and backups on real staging.
2. Run the cloud load/restart/outage suite and measure the bill. The ₹1,000/month target is not established by local tests. Define RPO/RTO and demonstrate a managed restore.
3. Integrate the approved Razorpay merchant/linked-account arrangement. Build event deduplication, amount/currency/merchant checks, refunds, reconciliation and the 10% append-only ledger. Decide GST, gateway-fee treatment, refunds and settlement policy with business/accounting review.
4. Supply the missing hardware listener/status files and test an actual Pi/Epson: trays, duplex, jams, partial output, reboot, CUPS submission reconciliation, token storage and power/network loss.
5. Implement a tested hardware agent, staged signed updater with rollback, and hardware acceptance. The simulator and verification helper do not replace this.
6. Approve contact/refund/privacy terms, deletion timelines and support escalation. No copied prototype policy is treated as legally approved.

Changing a flag, publishing the development environment or passing simulator tests does not complete these launch gates.

## Optional: one local deployment with Compose

After downloading the hash-verified wheels and building `inkora:staging`, run:

```bash
.venv/bin/python tools/dev_environment.py
docker compose --env-file .build/compose.env up -d db
docker compose --env-file .build/compose.env run --rm web flask --app inkora:create_app db upgrade
docker compose --env-file .build/compose.env up -d web
docker compose --env-file .build/compose.env exec web flask --app inkora:create_app create-admin
```

The helper creates development-only bindings if missing and preserves an existing private file. Existing injected bindings are not copied into it. Compose binds the pilot web port 8086 and database port 5434 only on loopback. The database is retained under ignored `instance/postgres-data`; synthetic local files use a Docker volume. A restart persistence check passed. Neither volumes nor live processes should be assumed to restore in a new task without checking; GCS remains the durable hosted storage plan. This setup has local trust authentication and is not a production deployment configuration.
