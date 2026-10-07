# Staging operations and release checklist

Use a new INKORA fleet project. No deployment or billable resource has been created by this build.

## Build and release

1. Install the tested pinned dependencies; run PostgreSQL tests and `pip-audit --local`.
2. Download wheels using the hashed runtime/build lockfiles into `.build/wheels`:

```bash
.venv/bin/python -m pip download --only-binary=:all: \
  -r requirements-runtime-hashed.txt -r requirements-build-lock.txt \
  --require-hashes -d .build/wheels
docker build --network=none -t inkora:staging .
```

3. Inspect the image and scan OS/base-image vulnerabilities with your registry's scanner. Python dependencies have been audited; an OS scanner was not available in this task. Rebuild/update the pinned base when a reviewed security patch is needed.
4. Review and push an immutable image to the new project's registry. Fill the Cloud Run template placeholders; keep Secret Manager values out of source and command logs.
5. Back up the target database and apply migrations as a controlled one-off job using the new image. Check migration head. Do not run `init-db` or auto-create tables in hosted staging/production.
6. Deploy initially with no customer traffic and authenticated ingress. Verify health, schema readiness, MFA, owner isolation and private object upload/download. Review the exact revision before traffic promotion.
7. Run the private pilot walkthrough, then deliberately restart services and simulate loss of connectivity, storage and database access. Record failures, recovery and p95 latency.
8. Roll back traffic to the previous tested image if needed. Database rollbacks must be independently reviewed for data loss; prefer backward-compatible forward migrations. No blind `db downgrade` in production.

## Configuration and access

- `DATABASE_URL`: separate PostgreSQL database; use `sslmode=verify-full` and a trusted CA for remote providers, or the provider's supported authenticated Cloud SQL transport. Never disable certificate verification.
- `INKORA_SESSION_SECRET`: stable random secret, at least 32 characters; rotate deliberately with session revocation.
- `INKORA_ENCRYPTION_KEY`: stable Fernet key stored separately from the database; backup securely. Re-enrollment or a planned key-rewrap process is required when rotating it.
- `INKORA_ENV=staging`: hosted security rules and mandatory admin MFA, simulated print workflow.
- `INKORA_CLOUD_PROJECT`: the new fleet project ID. The known original kiosk project is explicitly rejected.
- `INKORA_GCS_BUCKET`: a private bucket belonging to the new project only.
- `INKORA_PUBLIC_URL`: HTTPS base URL without credentials/query/fragment; use the actual hosted endpoint or approved domain.
- Managed service-account credentials: application default credentials; do not ship service-account JSON in the image.

Grant runtime minimum object read/create/delete permissions on its bucket plus access to the three named Secret Manager secrets. No project-wide owner role. Separate release/migration privileges from runtime where the database provider permits. Public access prevention and uniform bucket access must be verified. A fallback bucket lifecycle is supplied; confirm soft-delete/versioning does not unintentionally retain customer files beyond the approved policy.

The template limits concurrent requests to two with one thread per worker, so at most two 256 MB PDF validators run in a 1 GiB instance. Verify real peak RSS under staging stress before increasing this.

The template's 2 workers × maximum 4 DB connections × 3 instances implies up to 24 web DB connections, plus migrations and maintenance. Keep limits within the database provider's budget. At 100 devices with 30-second heartbeats, expect about 8.64 million requests/month even before printing. Do not equate an idle registered device with zero usage.

## Scheduled maintenance and alerts

Schedule the `flask --app inkora:create_app maintenance --retention-hours 24` command as a one-off container job every 15 minutes with the same storage/database bindings. Run only one maintenance task at a time. A failed storage delete must fail the job and alert an operator; it must not be recorded as successful deletion.

Alert on sustained 5xx, database readiness failures, storage deletion failures, growing queued/review jobs, and old device heartbeats. Log error classes, revisions and aggregate timings; do not log customer capability paths, tokens, documents, passwords or cookies. Gunicorn access logs are disabled, but provider/edge request logging must also exclude capability-bearing `/print/` paths. Validate this in the actual project.

Have an incident owner, support channel and escalation timer before delivery. During a printer outage stop new customer sessions, preserve paid-job evidence, and review ambiguous output before retry/refund. During storage or database outages fail closed. Backup/export access is privileged and audited.

## Backup and restoration

Enable the provider's encrypted backups/PITR where available. Agree an RPO and RTO based on cost and business needs before launch; no untested time targets are promised. Keep database backups and encryption keys recoverable through separate controlled access.

`tools/restore_drill.py` demonstrated a **local synthetic** pg_dump/pg_restore operation, not managed-provider disaster recovery. Run an equivalent staged restore into an isolated target on the chosen provider and verify schema, account isolation, jobs, ledger consistency, files and MFA-key availability before granting production readiness.

## Agent releases

`tools/verify_release.py` verifies an Ed25519 signature over the exact manifest bytes and artifact SHA-256. The trusted key must be pinned through a controlled provisioning process. No self-updating executable, extraction, fleet rollout or printer integration is implemented. Release signing private keys stay outside GitHub and outside kiosks. A future updater requires power-loss-safe staging, rollback and 1→5→fleet rollout acceptance on real hardware.

For a local repeatable pilot, `compose.yaml` retains PostgreSQL files in the ignored workspace instance directory and binds both service ports only on loopback. Restart persistence was verified with a synthetic disabled account, which was removed afterward. Do not put this trust-auth development database on a public interface. Before snapshot publication, take a consistent database export into a private ignored location; never assume a filesystem snapshot of an active database replaces a backup. Fresh-task restoration and named-volume restoration are not yet independently tested.
