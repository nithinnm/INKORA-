# INKORA customer touchscreen

The kiosk screen is distinct from the buyer/owner control room and INKORA fleet
administration. It follows the uploaded reference: INKORA branding, black/cyan/
violet/pink palette, “Print the way you want.” and “Tap to Start”.

Staging flow: start → private three-minute upload QR → phone scan/upload and
print settings → immutable simulation quote/queue → agent claim/checksum →
simulated printing → completion → next customer. Printer errors and ambiguous
jobs stop for operator review. Real UPI/payment confirmation is not simulated as
money received; live payment and physical output remain production integrations.

`tools/kiosk_terminal.py` serves the touchscreen on the kiosk computer's loopback
interface. The browser never receives its permanent device token or Google IAM
token. Device credentials stay in the private agent identity file. Same-origin
checks, a per-process CSRF nonce and Host validation protect local mutations.
The simulator separately provides heartbeat and journaled job processing.
Do not expose this development terminal server directly to a network.

The cloud API adds a device-scoped customer-session status operation; it returns
only state, pages and quote, never a document URL or another kiosk's session.
This requires the new image release but no database migration or secret rotation.

Run on a kiosk computer using the pinned runtime dependencies in a private venv:

```bash
python3 -m venv "$HOME/inkora-agent-venv"
"$HOME/inkora-agent-venv/bin/python" -m pip install --require-hashes -r requirements-runtime-hashed.txt
"$HOME/inkora-agent-venv/bin/python" tools/kiosk_terminal.py --identity="$HOME/inkora-test01.json" --gcloud-auth
```

Open the touchscreen browser on that same computer's loopback port 8765. The
`--gcloud-auth` option is for the private Cloud Shell staging pilot and requires
its logged-in gcloud CLI; production devices need their approved access path.
For Cloud Shell Web Preview, specify its exact hostname with `--preview-host`.

Keep the previously verified simulator running with `--jobs` and its private
durable `--journal`. The terminal itself never performs physical printing.
Device credential revocation is enforced at every cloud call.

The QR points to the configured cloud upload URL. Cloud Run currently requires
Google IAM, so an ordinary phone cannot yet use this as a public customer pilot.
Verify capability-path log exclusions and the private pilot first, then perform
an explicit reviewed public pilot access change. Do not weaken SSL, CSRF or
tenant isolation to work around IAM. Scan links, authenticator keys and device
identity files must not be shared in screenshots or chat.

The exact INKORA logo image has not been supplied as a reusable asset in the
repository; this version uses the existing INKORA wordmark. Replace it with the
authorized brand asset when available rather than downloading from the legacy
kiosk project or changing that project.
