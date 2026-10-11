# Owner maintenance requests

Owners open `/support` from the INKORA control room and choose an assigned kiosk,
category, title and problem description. Submission creates an open request with
service history. Repeat submission of the same form preserves the original
request; changed content with the same retry key is rejected. New submissions
are bounded to ten per user per hour. Requests do not send email or dispatch a
technician automatically.

INKORA administrators can view the fleet queue, start work, record progress,
resolve a request with a note, or reopen it with a reason. Notes and transitions
are appended to history. Stale/concurrent edits return a conflict rather than
silently overwriting another administrator's work. Owners can read their service
history but cannot perform INKORA status updates. There is no history deletion UI.

Ownership is derived from the selected kiosk, not a browser-supplied owner ID.
Both the recorded owner and current kiosk owner must match for an owner to access
history. If a kiosk is reassigned, neither owner can see the former owner's
request history; INKORA administrators retain access. Owners see only assigned
kiosks in the request form and only scoped requests/counts. Request bodies and
notes are escaped and not written to application logs. Do not submit secrets,
payment details, private customer URLs or customer documents.

All screens use the shared INKORA palette, CSRF protection, authenticated sessions
and existing admin MFA enforcement. Listing is paginated in batches of fifty.
Dates are shown in Asia/Kolkata. No hardware or payment functionality is removed.

## Verification

Local PostgreSQL: 123 tests passed after adding this feature, including owner and
cross-owner checks, HTML escaping, CSRF, retry protection, stale writes and
concurrent administrator updates. A separate synthetic PostgreSQL migration
from `a4d68bedf731` to `e7b01486cf20` preserved an existing owner, kiosk prices and
completed print job. None of these checks contacted Neon or the live kiosk.

## Staging rollout requirements

This feature is committed but has not been deployed. It requires a new immutable
image because the deployed image lacks the support code and migration.

1. Inspect the current staging revision/image/configuration before building.
   Preserve runtime service account, environment values and pinned secret refs.
2. Verify a restorable staging backup/recovery point and Neon staging endpoint.
3. Apply only the additive migration using Flask-Migrate and the migration owner
   role. Do not rerun completed migrations, use `init-db`, create tables manually,
   or grant the application schema ownership. Alembic applies only the new head.
4. Grant the existing `inkora_app` role DML on the two new tables and USAGE/SELECT
   on the new service-event sequence. Existing grants on old tables do not cover
   new ones unless appropriate default privileges were already configured.
5. Deploy the new digest with existing configuration, then verify readiness,
   login, owner request creation, INKORA updates, history and cross-owner denials.

The application's readiness check requires an exact Alembic head. After the
migration, the old image's readiness endpoint may report unavailable until the
new image is deployed. Coordinate this rollout in a staging maintenance window;
do not claim a zero-downtime migration. Never downgrade the database to remove
support tables after real requests are stored. Rollback needs a compatible
application image and reviewed recovery plan.

Do not reuse the original maintenance-setup helper blindly to rebuild the
scheduled retention job; it pins an older image for an unchanged maintenance
command. Inspect and preserve that working job and schedule separately.

### Verified preflight and migration helper

On 11 October 2026, the staging read-only job confirmed `a4d68bedf731`, role
`inkora_app`, database `inkora`, with neither service table present. The immutable
maintenance image was built successfully as
`asia-south1-docker.pkg.dev/inkora-510915/inkora/web@sha256:bc7b8fcb810bf2173333d59e8833df8cbf250338c083d9249c2aae2390f83295`.
The user confirmed creation of safety branch `pre-maintenance-20261010` from the
staging project's `production` branch. This is a same-project recovery branch,
not an independently tested offsite backup.

`tools/cloudshell_support_migrate.py` accepts an unpooled `neondb_owner` URL only
at a hidden terminal prompt. It compares the endpoint/database to the current
pinned application secret and forces psycopg plus full certificate verification
using the container CA bundle. URLs/passwords are not written to source files,
terminal output or CLI arguments. A dedicated migrator service account receives
access only to a temporary migration secret, not the runtime's existing three
secrets. The worker uses a temporary factory session key solely to initialize the
migration process; it never changes deployed login or MFA keys.

The worker upgrades only from the observed head to `e7b01486cf20`, verifies and
adds DML grants on the two new tables plus USAGE/SELECT on the new sequence. It
never uses `init-db`, downgrades, drops data or grants schema ownership. An already
upgraded head skips DDL and verifies grants. Existing migration jobs stop the
helper for inspection rather than being recreated blindly.

After successful execution, the helper deletes only its own temporary migration
job and privileged secret. It does not redeploy the web service. On any failure,
stop and inspect the execution; temporary resources can remain and need cleanup
after diagnosis. Do not change architecture or rerun creation commands blindly.
The runner was exercised successfully against a separate local PostgreSQL
migration fixture, preserving existing data and verifying application grants.

Immediately after migration, deploy the already-built maintenance digest using
an image-only Cloud Run update. Then verify readiness, application-role access,
owner/administrator service requests, isolation and the existing printing flow.
Do not rebuild the image for helper-only changes: the helper's worker code is
passed to the already-built image through the one-off job command.
