# Validation record

Validated in the current isolated cloud workspace on 6 October 2026. No GitHub CI run, live cloud deployment, publication of this revision, customer payment or physical print is claimed.

| Check | Observed result | Scope/limit |
|---|---|---|
| PostgreSQL tests | 35 passed | Real local PostgreSQL 17, applied migrations; synthetic users/documents |
| SQLite development tests | 33 passed, 2 skipped | PostgreSQL concurrency tests intentionally skipped |
| Parallel activation | One success from eight requests | Credential consumption transaction |
| Parallel job claim | One success from eight requests | Single kiosk and queued job |
| Customer session | QR, browser binding, expiry and duplicate upload checked | Staging simulation; no real payment |
| Account protection | MFA encryption/replay, recovery/session revocation, disable, rate limits checked | No automated email provider |
| Device lifecycle | Rotation retry window, old-token expiry, revocation and cross-device denial checked | Simulator credentials, not hardware secure element |
| PDF workflow | Malformed/encrypted/over-limit rejection, private download/checksum, state transitions | Parser resource limits; not a malware certification |
| Recovery | Lease expiry/review/retention and interrupted journal checked | No CUPS/hardware output reconciliation |
| Dependency audit | No known Python dependency vulnerabilities after patches | Audit service result, not proof of absence of all vulnerabilities; OS image scan outstanding |
| Container | Hash-verified offline build, non-root UID 10001, login/readiness and pip check passed | Local development bindings; no real Cloud Run/GCS connectivity |
| Synthetic restore | 3 users, 2 kiosks, 1 job, 1 audit record and matching migration revision restored | Local pg_dump/pg_restore, not managed-provider PITR/RPO/RTO |
| 100-device smoke | 300 HTTP heartbeats; zero errors; all 100 online | Local 2-worker/4-thread server, 20 concurrent clients, 3 heartbeat rounds; no PDF throughput or cloud billing measurement |
| Local timing | 1.694 seconds total; p50 116.88 ms; p95 196.37 ms | One local run, not sustained traffic or cloud SLA |
| GCS adapter | Generation conditions, checksum mode and private response contract tested | Stubbed bucket; real IAM, lifecycle and connection unverified |
| Agent signatures | Signed manifest accepted, modified artifact/manifest rejected | Offline verification primitive; no automatic updater/rollout |
| Failures | Database outage returns unavailable and leaves liveness separate | Fault injection, not a real cloud outage drill |

The tests run the intended checks and can fail: cross-scope requests, bad claims, stale leases, duplicate transitions, tampered signatures, invalid credentials and missing staging configuration are rejected. The SQL test database is explicitly named `inkora_test`; tests may reset only that target. Load and restore scripts create separate synthetic databases and preserve them for inspection.

Current live-launch blockers are in WALKTHROUGH.md and PRODUCTION-REVIEW.md. Passing this record supports a private staging pilot, not production kiosk sales.

Additional verification: the Compose pilot migrated a new bind-mounted PostgreSQL database, started the non-root web image, and retained a synthetic disabled account across a database restart. That probe was removed after verification. Both container readiness endpoints passed afterward. This is restart persistence in the current instance, not proof of restoration into a new task.

Per-kiosk pricing changes, immutable historical quote snapshots, authenticated device configuration and protection against binding the known original kiosk cloud project/bucket are included in the final tests.
