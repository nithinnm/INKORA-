# INKORA UI refresh

The owner/admin portal, sign-in and account pages, maintenance screens, private
customer upload page, and loopback kiosk share the black, cyan, violet, and pink
palette. The portal adds a responsive workspace navigation, glossy panels,
consistent forms and status indicators. Kiosk transitions animate only when a
session changes state. Both stylesheets honour reduced-motion preferences.

The original approved logo is still required as a file. Place its PNG at
`inkora/static/branding/inkora-logo.png`; both applications automatically use it.
Until then, the text wordmark is deliberate and no approximate logo is supplied.

This changes presentation only. Owner isolation, CSRF, MFA, device credentials,
private uploads and simulation/payment boundaries remain in place. No migration
is required. Portal deployment needs a new Cloud Build image and immutable digest;
the touchscreen needs a repository update and process restart. The currently
hosted Cloud Run revision is unchanged by these source changes.

Validated locally with the existing test suite and Chromium desktop (1440 px)
and phone (390 px) renders. The reviewed pages fit their viewport; wide data
tables retain their own horizontal scrolling. Screenshots use synthetic data.

## Responsive staging job pickup

The touchscreen checks QR scan status every five seconds, then checks an active
scanned session every second. It stops polling at completion, cancellation, expiry
or review. The simulator checks for queued work every second only when a fresh,
private local activity hint says the touchscreen has observed a queued upload.
Before upload it does not make fast cloud claim requests. The hint contains only
a boolean and monotonic timestamp, no credentials or customer identifiers.

Heartbeats remain every 30 seconds. With no touchscreen or a stale hint, a slow
30-second queue check preserves standalone simulator operation. Network/API
failures retain 30-second backoff. Missing/malformed/symlink hints cannot initiate
fast polling. Private download verification and journal protection still precede
completion. A scan can take up to five seconds to appear; after it is detected,
queued uploads are picked up promptly, subject to network and verification time.

Both processes must run on the same machine with the same identity path to share
the local hint. Restart both Cloud Shell processes; no Cloud Run rebuild,
deployment, migration, or credential changes are required.
