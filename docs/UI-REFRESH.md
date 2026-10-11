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
