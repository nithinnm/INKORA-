# Device hardware adapter

Target: Raspberry Pi, local CUPS, Epson WF-C5890. This is an implementation
foundation, not a field-approved physical printing agent. It is not wired into
Cloud Run or the simulator and introduces no cloud migrations or dependencies.

## Implemented behavior

`agent/hardware.py` interprets synthetic or observed IPP attributes. Missing,
stale, malformed or contradictory telemetry is unknown. Jam, empty paper/ink,
open cover, disconnected/stopped queues and other blocking conditions refuse new
jobs. Low supplies warn without inventing measured percentages. Unknown ink
sentinels remain unknown; no manual 100% fallback is used. No tray availability
is inferred from missing data.

Exact CUPS job ID and printer URI are required when interpreting job status.
Cancelled, aborted, missing and timed-out jobs require review. A completed CUPS
job is labelled `spooler_completed`; it does not assert physical sheets emerged.
Sheets completed are reported separately from document page counts.

`agent/submission_journal.py` commits a handoff intent before a future printer
submission. A second process or restarted agent cannot reserve the same job
again. If the returned CUPS ID was stored, recovery calls for inspecting that
exact job. If not, it calls for operator review, including a crash before actual
submission. It deliberately has no replay/reset/settlement operation. The journal
uses owner-only file permissions and synchronous SQLite commits. Protect its
parent directory as well; it is not an encrypted credential store. It stores no
device or claim tokens. Integration must authenticate and verify the document
before reserving handoff, and keep cloud claim/lease fencing intact.

## Read-only Pi probe

On a future dedicated test Pi with pycups installed and its exact queue configured,
run from the repository root:

```bash
python3 -m agent.cups_probe --queue Epson_WF_C5890
```

This reads only the local `/run/cups/cups.sock`, in a worker bounded to five
seconds. It does not install packages, modify queues, print documents, access
Firebase or contact the fleet backend. Exit code 0 means a fresh idle accepting
queue with no observed blockers; 2 means unavailable, busy or unknown. An idle
queue remains insufficient evidence of physical printer output. Raw native
errors and printer metadata are not dumped. No hardware is required for tests:

```bash
python3 -m pytest -q tests/test_hardware.py
```

## Remaining acceptance work

A physical submission driver, durable download/checksum storage, bounded lease
renewal, reconciliation and operator resolution still need integration. Verify
actual driver attributes and colour/duplex/media capabilities before accepting
quotes for hardware. Test power loss at each handoff boundary, paper jams and
partial output on real hardware before field release. Synthetic response tests
cannot establish installed driver compatibility or actual print quality.

No physical jobs or real charges are enabled by this change. Razorpay test-mode
integration is the next separate milestone; never count simulation quotes as
revenue or treat a spooler observation as payment settlement authorization.
