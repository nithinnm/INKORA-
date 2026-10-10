# Supplied hardware reference review

Reviewed on 10 October 2026 as source text only. Neither supplied module was
executed and no legacy Firebase, storage, printer or kiosk was contacted.

## Identified target

The Linux path names `Epson_WF_C5890`, describes a Raspberry Pi with CUPS/IPP
Everywhere, and uses `pycups` or `lp`/`lpstat`. The Windows development path names
Canon G2000 and uses SumatraPDF/Windows spool APIs. These defaults identify the
intended integration; they do not prove the installed queue, firmware or actual
IPP attributes. Verify those on a separate test Pi before connecting a live kiosk.

## Reusable behavior

Keep per-job colour and duplex settings, blocking jam/empty-paper/empty-ink
states, low-supply warnings, periodic heartbeats, job progress and cleanup of
local documents. Query installed printer capabilities rather than assuming
that a particular colour/tray option is supported by every driver.

## Production gaps

| Reference behavior | Required fleet behavior |
| --- | --- |
| Missing printer, failed CUPS query or unknown status reports ready | Report unknown/unavailable and refuse new physical jobs |
| Missing ink telemetry falls back to a manual file/default 100% | Preserve unknown values; distinguish simulated/manual from measured values |
| Disappearance from active queue is success | Inspect exact CUPS job terminal state; cancelled/aborted/unknown are not completed |
| Queue timeout returns whether job was ever seen; fallback estimates print duration | Timeout or observation loss requires reconciliation/operator review |
| Completion text asserts last sheet printed | Distinguish spooler completion from verified physical output; document printer evidence limits |
| Firestore read then update claims a job | Use existing device-scoped atomic claim, lease and fencing protocol |
| Global Firestore job watcher | Use assigned-kiosk API scope; preserve owner isolation |
| No durable submission journal around printer handoff | Persist submission intent before handoff and exact CUPS job ID afterward; crash window requires review, never blind resubmission |
| Download directly into job-named file | Use bounded private local storage and verify the backend checksum before submission |
| Printing subprocesses have no timeout | Bound handoff and observation operations without automatically repeating an uncertain submission |
| Invalid/missing timestamp bypasses deadline check | Rely on validated backend authorization/expiry; malformed state must fail closed |
| Automatic blocked-job retry by changing shared status | Retry only proven pre-submission failures; ambiguous/partial output requires explicit review |

Sheets-completed telemetry is not identical to document pages for duplex or
multiple copies. Keep these measures separate. Tray levels and supported art
media are not established by these modules and must remain unknown until read
from the installed printer.

## Integration sequence

1. Inspect a separate test Pi's OS, installed CUPS queue and advertised attributes
   without submitting a document or changing the queue.
2. Build a read-only hardware adapter with explicit unknown states and tests
   against recorded/synthetic IPP responses.
3. Add a durable, device-scoped physical spool adapter to the existing fleet
   protocol. Keep simulator deployment separate and production fail-closed.
4. Test colour, duplex, correct tray/media, queue rejection, cancellation, jam,
   partial output, network loss, power loss and restart around submission.
5. Enable paid printing only after payment authorization and reconciliation are
   implemented and these hardware acceptance gates pass.

The uploaded legacy listener must not be launched against the new deployment or
used to bypass existing database roles, device authentication or staging guards.
