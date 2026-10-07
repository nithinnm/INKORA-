# INKORA Fleet

A new fleet platform built separately from the existing kiosk. This is a hardened, locally verified **private staging pilot**, not a live production kiosk release. Read [the production review](docs/PRODUCTION-REVIEW.md) for the source audit, architecture, recovery rules and launch gates.

Implemented: role-scoped portals, encrypted MFA, owner invitations/recovery and session revocation, database-backed rate limits, kiosk activation/rotation/revocation, heartbeats, customer QR sessions, private PDF adapters, simulated job processing/review/retention, durable simulator journaling and a shared black/cyan/violet/pink theme. PDF uploads and simulated job processing are implemented for development only. Real payments, physical printer drivers, cloud deployment and software updates are not implemented.

## Develop

```bash
cd /workspace/INKORA-
python -m venv .venv
.venv/bin/pip install -r requirements-lock.txt
export INKORA_SESSION_SECRET="$(.venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(48))')"
.venv/bin/flask --app inkora:create_app db upgrade
.venv/bin/flask --app inkora:create_app create-admin
.venv/bin/gunicorn --bind 127.0.0.1:8000 'inkora:create_app()'
```

Keep a stable, securely stored session secret across restarts; generating another invalidates sessions. No default password or seeded account. The CLI prompts without echoing the password. Use an isolated local database; never point this foundation at the original kiosk database. Fresh local SQLite databases live under ignored `instance/`. Existing schemas created with init-db need a reviewed baseline or a new database; do not overwrite them blindly. Use `DATABASE_URL` with `postgresql+psycopg://…` for future PostgreSQL validation; hosted staging/production refuse SQLite and init-db. Read the deployment runbook and remaining launch gates before deploying.

```bash
.venv/bin/python -m pytest -q
```

## Device simulator protocol

As admin, create an owner, register a kiosk and issue an activation code. POST JSON `{"code":"<activation code>"}` to `/api/devices/activate`. Securely store the returned device token (it is not recoverable from the database). POST JSON `{"printer_status":"ready","software_version":"sim-0.1"}` to `/api/devices/heartbeat`, with `Authorization: Bearer <device token>`. Supported states: ready, busy, error, paper_empty, unknown. A heartbeat older than 90 seconds makes the kiosk offline. Any supplied kiosk ID is ignored: the token determines identity. API credentials must stay on the agent, not in a public tablet page. This protocol simulates status only; it does not print.

`/health/live` checks process responsiveness; `/health/ready` checks database schema access. Neither proves production launch readiness.

## Workspace and GitHub

Code is created and tested in this cloud checkout. GitHub receives tracked files through commits and pushes; it does not contain running processes, virtualenvs, secrets or local databases. Hosting is separate (planned Cloud Run/PostgreSQL/private storage). No changes have been pushed or deployed by this initial build. Existing cloud tasks are isolated: use this checkout, without creating another worktree unless explicitly requested.


## Milestone 0.2: device lifecycle and PostgreSQL

The hardware-free simulator is `tools/kiosk_simulator.py`. Activation prompts for the code without displaying it. Keep identity files outside the checkout; they contain a device credential and are created with mode 0600.

```bash
.venv/bin/python tools/kiosk_simulator.py --url http://127.0.0.1:8000 --identity /tmp/inkora-device.json --activate --once
.venv/bin/python tools/kiosk_simulator.py --url http://127.0.0.1:8000 --identity /tmp/inkora-device.json --status paper_empty --once
```

Without `--once` it sends heartbeats every 30 seconds. Stop it to simulate loss of connectivity; status becomes offline after 90 seconds. Admins can revoke a credential and issue a new activation code; old tokens are rejected. Revocation can race with a request already in flight, but subsequent authenticated requests are denied. Simulators never submit real print jobs or take payments.

PostgreSQL migration commands (set the secret and `DATABASE_URL` securely first):

```bash
.venv/bin/flask --app inkora:create_app db upgrade
.venv/bin/flask --app inkora:create_app db check
```

The initial migration is for an empty fleet database. **Do not apply it blindly to the existing development SQLite schema created with `init-db`**: preserve that file and use a new database, or perform a separately reviewed schema-baseline migration. Never point migration or test commands at kiosk #1.

For a dedicated local PostgreSQL instance, run `postgres:17` with port 5432 bound only to 127.0.0.1. The onboarding instance uses Docker container `inkora-dev-postgres` with database `inkora`. Local trust authentication is confined to this development container; production requires authenticated access and TLS. Test commands reset only a dedicated database named `inkora_test`:

```bash
INKORA_TEST_DATABASE_URL=postgresql+psycopg://postgres@127.0.0.1:5432/inkora_test .venv/bin/python -m pytest -q
```

CI uses PostgreSQL and applies migrations before test fixtures. PostgreSQL tests include eight simultaneous activation attempts. SQLite runs deliberately skip the PostgreSQL concurrency check. These tests establish identity lifecycle, not 100-device load capacity, real printer recovery or full production readiness.

## Milestone 0.3: private PDF simulation

Run migrations on the separate fleet PostgreSQL database, sign in and open **Simulated printing**. Activate a kiosk and send a ready heartbeat first. Uploads are restricted to your kiosks (admins may test any kiosk). Queues require recent ready status. Pricing follows the supplied portal rates, integer paise and per-page bulk discount rounding; this is a test quote, never revenue or a payment.

PDFs are capped at 10 MB and 250 pages; encrypted and malformed files are rejected. A separate validator process has a 256 MB memory limit, five CPU seconds and an eight-second wall-clock timeout. This is resource isolation, not a complete malware sandbox. Documents use random server filenames under ignored `instance/print_files`, owner-only permissions, and device-plus-claim authentication for download. Local disk storage is for development only; it must be replaced by private durable object storage before Cloud Run deployment. Retention/cleanup scheduling remains a launch gate; do not upload sensitive customer documents to this development environment.

```bash
.venv/bin/python tools/kiosk_simulator.py --url http://127.0.0.1:8001 --identity /tmp/inkora-device.json --journal /tmp/inkora-spool.db --jobs --once
```

Enroll with `--activate` first if no identity file exists. `--jobs` claims at most one job, downloads and verifies the SHA-256 checksum, then reports simulated printing/completion. No printer driver runs. Each kiosk may hold one active job. Claims expire after five minutes; the next claim request marks expired claims **needs_review** without automatically requeuing. A physical printer's ambiguous output needs operator reconciliation, which is not yet implemented. Completed-job retry returns a conflict rather than submitting another print. The current pipeline is authenticated operator testing; a public customer QR/upload session and payment authorization remain future milestones.


## Walkthrough and release evidence

Start with [WALKTHROUGH.md](docs/WALKTHROUGH.md) for admin, owner, device and customer flows. See [VALIDATION.md](docs/VALIDATION.md) for passed checks and their limits, and [OPERATIONS.md](docs/OPERATIONS.md) for container build, cloud staging, backups and incident handling. Cloud deployment/account details are still required; live payments and actual printer integration remain separate launch gates. Publishing a development snapshot does not launch the business.
