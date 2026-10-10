# INKORA production review

Status: public simulated staging pilot; real paid printing has not passed production acceptance.

## Current evidence — 10 October 2026

This section supersedes historical provisioning statements below. Separate GCP
staging, Neon migrations and least-privilege application role, Secret Manager,
private GCS storage and Cloud Run readiness have been completed. Admin login/MFA,
owner onboarding, owner isolation, kiosk activation, QR expiry and simulated
phone-upload-to-completion were demonstrated in staging. Scheduled maintenance
executed successfully under its scheduler service account. Two operational
alerts were created and their shared email delivery path was tested successfully;
the temporary test policy was removed. See MONITORING.md for the scope of that test.

The GitHub workflow YAML parsing error was fixed in commit `2e67c01`;
the replacement run's outcome still needs confirmation. Local test results do
not establish GitHub runner success. Real payments, revenue-share accounting,
physical printer integration and failure recovery remain production gates.
Do not promote the simulator by changing the environment flag.

## Evidence and scope

Reviewed the supplied web_portal.py and complete pasted discussion. The discussion is historical context, not executable instructions or proof that prior work exists. Only web_portal.py was attached; hardware_status.py, pi_listener.py, abuse_detection.py, dependency manifests, logo, Firebase rules, and deployed infrastructure were not supplied. No legacy services or data were contacted or modified. The working defaults are a separate fleet platform, no live payments while approval is pending, hardware simulation first, and a 10% gross-revenue maintenance model pending commercial confirmation.

## Confirmed gaps in the supplied portal

Line numbers refer to the supplied file, not the new implementation.

| Priority | Evidence | Consequence | Required solution |
|---|---|---|---|
| Critical | Admin page/data at 2770–2776 lack authentication | A hidden URL does not protect operations data | Authenticated roles and owner-scoped queries |
| Critical | Firebase initialization at 34–50 hard-codes the legacy project and bucket | Reusing unchanged code can touch kiosk #1 | Separate cloud project, database, storage, credentials and billing |
| Critical | One hardware document at 1829; job data at 2296 has no kiosk/owner identity | No trustworthy multi-kiosk routing or isolation | Device identity, tenant relationships, scoped claims |
| High | Webhook at 2501–2525 checks then separately writes job and transaction | Concurrent deliveries can race; partial writes can leave inconsistent payment state | Unique provider events, transactional state changes, reconciliation |
| High | Payment confirmation at 2509 trusts job notes without checking expected amount, currency or merchant mapping | A valid signature alone does not prove the correct order was funded | Bind immutable payment attempt, merchant, amount, INR currency, capture status |
| High | Complaint POST at 2859 has no customer authorization; reprint at 2887 checks then updates | Unauthorized complaints and concurrent duplicate reprints | Session-bound customer capability and atomic reprint policy |
| High | TEST_MODE at 103–106 marks uploaded jobs paid | Accidental production activation enables free printing | Separate simulator adapter and deployment guard; no live bypass |
| High | Complaint descriptions inserted into innerHTML at 1757 | Unescaped customer content can execute in admin browser | Escaped templates/textContent and CSP |
| High | PDF parse at 2237–2251 runs inside request, limited primarily by upload bytes | Small malicious PDFs can consume excessive CPU/memory | Isolated parser with page, time and memory limits; reject encryption and unsupported content |
| Medium | Global web_user abuse identity at 2280 | One user can affect everyone; helper implementation missing | Distributed scoped rate limits and upload quotas |
| Medium | Float prices at 2284–2287 | Accounting rounding can diverge | Integer paise, immutable quote and pricing version |
| Medium | Dashboard day at 2779 uses UTC midnight | Indian reporting day differs | Store UTC; report using Asia/Kolkata boundaries |
| Medium | Short UUID-derived IDs at 2291–2292 | Collision and enumeration risk grow | Full random IDs; authorization independent of IDs |
| Medium | Debug development server at final line | Unsuitable production runtime | Gunicorn, TLS at ingress, bounded workers and health probes |

Preserve the UX concepts: bounded scan/upload sessions, atomic upload reservation, fail-closed stale hardware checks, tray policy, and clear progress/error screens. Do not copy the monolithic portal into the new production path.

## Architecture decisions

Use one modular backend, one PostgreSQL database, private object storage, and a standard device protocol. Python/Flask preserves the language of the actual supplied code; the historical Express description does not match it. Server-rendered pages keep the first portal simple and avoid a second build/runtime. A richer frontend can consume the same API later if warranted.

Production target: containerized backend on Cloud Run, managed PostgreSQL chosen after cost and availability comparison, and separate private GCS bucket. The local SQLite database is development-only. Initial schema migrations and PostgreSQL identity integration tests are now implemented. Deployment assets, ongoing migration review and broader production integration tests remain launch requirements. No cloud account or billing resources were created.

Devices make outbound HTTPS requests; server-derived identity controls kiosk scope. Tablets must not store permanent device credentials. A future local agent broker issues short-lived customer sessions. Activation codes are high-entropy, single-use, expiring, hashed at rest and consumed atomically. Admin revocation and reactivation plus owner-only simulator token storage are implemented. Add automated credential rotation and hardware secure-storage validation before field use.

Polling and heartbeat intervals are configuration, not scale guarantees. At 100 kiosks, 30-second heartbeats alone produce 8,640,000 requests/month. Measure request duration, database writes, cold starts, log volume and egress. Store latest health with bounded history rather than every payload forever. Registered idle devices still generate cost if they send heartbeats. Near-zero cost and ₹1,000/month at ten kiosks remain unverified targets; payment fees, backups, database availability and support costs are separate.

## Domain and recovery contract

Every kiosk belongs to an owner. Users, devices and customer sessions are separate identities. Jobs reference owner, kiosk, immutable quote, file metadata/checksum and authorization. Store files in private storage, not database rows. Downloads require short-lived scoped authorization and retention/deletion rules.

Future print states: uploaded → authorized → queued → claimed → printing → completed. Failed, cancelled and needs_review are explicit branches. Devices claim jobs atomically with leases and fencing tokens. Local durable spool persists download checksum, claim and printer submission ID. A crash after printer submission is ambiguous: reconcile with CUPS; do not blindly submit again. Exactly-once physical printing cannot be guaranteed solely by a cloud transaction. Partial output requires operator review/reprint policy and user communication.

Internet loss pauses new sessions/authorizations. Already authorized and verified downloaded jobs may finish; persist outcomes locally and reconcile on reconnect. Cloud retry must never create a second print. Do not let simulator completion represent actual sheets printed.

Future payments use a provider interface, with an explicit sandbox implementation unavailable in production. Await approved Razorpay merchant/linked-account structure. Tracking 10% owed is not automatic settlement. Decide gross basis, GST, fees, discounts, partial refunds, invoice timing and rounding with the business/accounting policy. Append ledger entries and reversals, never rewrite history; deduplicate events and reconcile provider totals.

## Launch gates

1. Authentication: invitations, password reset, admin MFA, session revocation, email delivery, distributed rate limiting and audit retention. Current initial-password provisioning is for development.
2. Database: versioned migrations, PostgreSQL transaction/concurrency tests, connection budget, encrypted backups, point-in-time recovery where supported, and a tested restore with agreed RPO/RTO.
3. Customer printing: scoped sessions, private PDF upload/download, isolated validation, immutable pricing, durable device spool, leases and CUPS reconciliation.
4. Hardware: real Pi/Epson tray discovery, duplex/page selection, jam/paper/ink failure, partial-print recovery, power loss and reboot acceptance. Missing hardware files require independent inspection.
5. Devices: revocation, rotation, encrypted local credentials, release signatures, pinned verification key, rollback, staged updates and update failure recovery. No arbitrary remote shell endpoint.
6. Payments: approved ownership/settlement arrangement, test-mode provider contract, amount/currency binding, duplicate/out-of-order webhook tests, refunds and reconciliation. No live payments yet.
7. Operations: CI, container build, isolated staging, TLS, secret management, log redaction, alerts, support runbooks, rollback and retention jobs.
8. Validation: two owners/two kiosks concurrently; cross-owner and cross-device denials; duplicate activation/job claims; network/database outages; restart during upload/printing; payment arrives after expiry; duplicate refund; interrupted update. Run 100-device load tests against staging with explicit latency/error/cost targets.
9. Accessibility/UI: shared tokens for every screen, readable contrast, keyboard/focus tests, responsive phone/tablet layouts, reduced motion and labelled empty/error/loading states. Admin, owner, customer, recovery, MFA and review pages now share the palette; real-device touch layouts and browser accessibility acceptance remain to be verified.
10. Privacy/legal: document retention and deletion, consent/support access, merchant disclosures, refunds and contact details; replace prototype policy text with business-approved policies.

No percentage completion is assigned: completion must be established by passing acceptance gates, not estimated code volume.


## Validated milestone 0.3

Implemented development-only operator PDF uploads, resource-bounded validation, private local files, integer-paise quotes, owner-scoped job listing, device-scoped atomic claims, checksum-verified simulator downloads and sequential state transitions. PostgreSQL tests cover concurrent claims, cross-device denial, expired-lease handling and simulator HTTP completion. Endpoints fail closed in production mode. Remaining: public customer sessions, durable private cloud storage, retention scheduling, job upload idempotency keys, lease renewal, operator review/reprint UX, physical printer spool reconciliation, payment adapter and deployment. Successful simulated completion is not evidence of physical print success or production readiness.


## Current release status

Account invitation/reset, encrypted TOTP with replay protection, server-side session version revocation, distributed database limits, customer QR/browser-bound upload, GCS/private-local adapters, scheduled maintenance command, review/cancel/retry, lease renewal, retry-safe device rotation, durable simulator journal and signature verification primitive are implemented. A non-root hash-verified container and Cloud Run staging template are prepared; Python dependencies were patched after the audit. See VALIDATION.md for observed results and WALKTHROUGH.md for operating instructions.

Production is intentionally fail-closed: the live mode does not expose simulated print routes. Actual Google Cloud configuration and IAM, cloud storage/lifecycle, managed backups, edge access controls, approved payments/ledger, hardware driver and CUPS recovery, signed staged updater, approved policies and a cloud outage/load/cost drill remain outstanding. No first-kiosk modification, cloud provisioning, GitHub push, real email, payment or physical printing was performed.
