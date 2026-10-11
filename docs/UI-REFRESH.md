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

The simulator with `--jobs` checks the queue every second independently of its
30-second heartbeat. The touchscreen refreshes an active session every second
and keeps its single-request guard. Network/API failures retain 30-second backoff.
Private download verification and durable journal protection still precede
completion. This removes the previous up-to-30-second queue polling delay; it
cannot guarantee instant completion across network, validation, or real printing.
These local tool changes require restarting both Cloud Shell processes, with no
Cloud Run rebuild, deployment, database migration or credential changes.
