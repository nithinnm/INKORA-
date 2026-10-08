# Neon staging acceptance

Current reported deployment: `inkora-510915`, Mumbai `asia-south1`, service
`inkora-staging`, ready revision `inkora-staging-00003-tkp`. The immutable image is
`asia-south1-docker.pkg.dev/inkora-510915/inkora/web@sha256:f1f85033dd99cb7c6096c970d4987c9f779aff45a72f0b6eb92a936095208cef`.
Do not rerun migrations or Cloud SQL instructions in the earlier deployment guide.

The runtime uses Neon pooled `inkora_app`, PostgreSQL schema head `a4d68bedf731`,
and Secret Manager bindings: database URL version 2, session secret version 1,
encryption key version 1. The database URL uses `sslmode=verify-full` and
`sslrootcert=/etc/ssl/certs/ca-certificates.crt`. Secrets must stay stable.
Confirm the live service still has these bindings before future changes.

## Login and MFA

The admin record has been verified enabled, without MFA enrolled. The local
regression test exercises HTTPS login, CSRF tokens, MFA enrollment, dashboard
and logout with secure cookies and CSRF enabled. It does not establish that a
particular Cloud Shell browser session is healthy.

For `The CSRF session token is missing`, close old preview tabs, clear site data
for the preview origin, reopen Cloud Shell Web Preview on port 8080 and navigate
to `/login`. Use one tab and freshly rendered forms. Avoid the browser Back
button after sign-in/sign-out. Keep the preview hostname stable during MFA.
Never turn off CSRF or secure cookies to work around preview errors.

Sign in, enroll the displayed key privately in your authenticator, submit the
current code and confirm the dashboard. Then sign out and sign in again with
password plus a fresh authenticator code. If the fresh flow still fails, record
the failing path/action and whether the browser has a `session` cookie; never
copy its value, the authenticator key or code into chat.

## Temporary bootstrap cleanup after login verification

Only after confirming login/MFA, delete the one-off job and its temporary
password secret. These are not the three application secrets. Removal leaves
the admin password hash and web service unchanged.

```bash
gcloud run jobs delete inkora-staging-admin-bootstrap --project=inkora-510915 --region=asia-south1
gcloud secrets delete inkora-bootstrap-6168ffddbe0b --project=inkora-510915
```

## Retention job for Neon (no Cloud SQL attachment)

Inspect job metadata first. If `inkora-staging-maintenance` already exists,
inspect/reuse it instead of running a creation command. The following is for a
missing job and uses the known numeric secret versions. No database DDL occurs.

```bash
gcloud run jobs create inkora-staging-maintenance \
  --project=inkora-510915 --region=asia-south1 \
  --image=asia-south1-docker.pkg.dev/inkora-510915/inkora/web@sha256:f1f85033dd99cb7c6096c970d4987c9f779aff45a72f0b6eb92a936095208cef \
  --service-account=inkora-staging@inkora-510915.iam.gserviceaccount.com \
  --set-env-vars=INKORA_ENV=staging,INKORA_CLOUD_PROJECT=inkora-510915,INKORA_GCS_BUCKET=inkora-510915-staging-documents,INKORA_PUBLIC_URL=https://inkora-staging-433513064052.asia-south1.run.app \
  --set-secrets=DATABASE_URL=inkora-staging-database-url:2,INKORA_SESSION_SECRET=inkora-staging-session-secret:1,INKORA_ENCRYPTION_KEY=inkora-staging-encryption-key:1 \
  --command=flask --args=--app,inkora:create_app,maintenance,--retention-hours,24 \
  --tasks=1 --parallelism=1 --max-retries=0 --task-timeout=600s --cpu=1 --memory=1Gi
```

Maintenance intentionally cancels expired queued jobs, marks expired claims for
review and deletes retained documents according to policy. Inspect retained
pilot data before the first execution. Schedule and alert only after its first
execution is verified; reuse the scheduler IAM/HTTP pattern from the deployment
guide, with no Cloud SQL resources or flags.

## MVP acceptance gates

1. Admin login, MFA enrollment, logout and MFA challenge verified in the hosted browser.
2. Owner invitation/password, owner isolation, kiosk registration and per-kiosk prices verified.
3. Device activation, heartbeat, customer QR/PDF, immutable quote and simulated job completion verified.
4. Expired/ambiguous jobs go to review without automatic reprinting; device/account revocation works.
5. GCS private access, request-log exclusions for capability paths and document retention verified.
6. Maintenance scheduler and failure alerts configured; database restore and stable MFA-key recovery tested.

Do not open public device/customer access until the private pilot and log
exclusions are verified. Cloud Run IAM authentication currently makes bare
customer QR URLs and ordinary device calls inaccessible without Google
authentication. Changing access is a separate explicit pilot rollout step.

This is a simulated-printing staging MVP. Physical printer acceptance, live
payments/ledger and signed agent rollout remain separate production gates.
