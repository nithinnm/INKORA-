"""Fail-closed interpretation of CUPS/IPP observations, without printer submission."""
from dataclasses import dataclass
from enum import Enum
from collections.abc import Mapping


class Availability(str, Enum):
    READY = 'ready'
    BUSY = 'busy'
    UNAVAILABLE = 'unavailable'
    UNKNOWN = 'unknown'


@dataclass(frozen=True)
class PrinterHealth:
    availability: Availability
    reason: str
    supply_percent: tuple[int | None, ...] = ()
    warning: bool = False

    @property
    def may_start_job(self):
        return self.availability == Availability.READY


def _integer(value):
    return isinstance(value, int) and not isinstance(value, bool)


def printer_health(attributes, *, fresh=True):
    """Interpret one fresh, exact-queue response. Unknown observations block jobs.

    Ink sentinels (-1/-2/-3) remain unknown. No default ink/paper percentages.
    An idle CUPS queue is not proof that a physical printer can produce output.
    """
    if not fresh or not isinstance(attributes, Mapping):
        return PrinterHealth(Availability.UNKNOWN, 'telemetry_unavailable')
    state = attributes.get('printer-state')
    accepting = attributes.get('printer-is-accepting-jobs')
    reasons = attributes.get('printer-state-reasons')
    if isinstance(reasons, str):
        reasons = [reasons]
    if (not _integer(state) or state not in (3, 4, 5)
            or not isinstance(accepting, bool) or not isinstance(reasons, (list, tuple))
            or not reasons or any(not isinstance(r, str) or not r for r in reasons)):
        return PrinterHealth(Availability.UNKNOWN, 'incomplete_telemetry')
    levels = attributes.get('marker-levels', [])
    if not isinstance(levels, (list, tuple)):
        levels = []
    supply = tuple(v if _integer(v) and 0 <= v <= 100 else None for v in levels)
    normalized = {r.removesuffix('-error').removesuffix('-warning').removesuffix('-report') for r in reasons}
    blockers = (
        ('media-jam', 'paper_jam'), ('media-empty', 'paper_empty'),
        ('marker-supply-empty', 'supply_empty'), ('toner-empty', 'supply_empty'),
        ('cover-open', 'cover_open'), ('door-open', 'cover_open'),
        ('offline', 'offline'), ('connecting-to-device', 'connection_pending'),
        ('paused', 'paused'), ('shutdown', 'shutdown'), ('stopped-partly', 'stopped'),
        ('spool-area-full', 'spool_full'), ('output-area-full', 'output_full'),
    )
    for ipp_reason, reason in blockers:
        if ipp_reason in normalized:
            return PrinterHealth(Availability.UNAVAILABLE, reason, supply)
    if state == 5 or not accepting:
        return PrinterHealth(Availability.UNAVAILABLE, 'queue_stopped', supply)
    # Unknown errors and unsuffixed reasons cannot silently authorize printing.
    allowed_reports = {'media-low', 'marker-supply-low', 'toner-low'}
    for reason in reasons:
        if reason == 'none':
            continue
        base = reason.removesuffix('-warning').removesuffix('-report')
        if reason.endswith('-error') or base not in allowed_reports:
            return PrinterHealth(Availability.UNKNOWN, 'unrecognized_condition', supply)
    if 'none' in reasons and len(set(reasons)) > 1:
        return PrinterHealth(Availability.UNKNOWN, 'contradictory_telemetry', supply)
    if 0 in supply:
        return PrinterHealth(Availability.UNAVAILABLE, 'supply_empty', supply)
    warning = any(r != 'none' for r in reasons) or any(v is not None and v <= 10 for v in supply)
    availability = Availability.BUSY if state == 4 else Availability.READY
    return PrinterHealth(availability, 'processing' if state == 4 else 'idle', supply, warning)


class JobOutcome(str, Enum):
    PENDING = 'pending'
    PROCESSING = 'processing'
    HELD = 'held'
    SPOOLER_COMPLETED = 'spooler_completed'
    NEEDS_REVIEW = 'needs_review'


@dataclass(frozen=True)
class JobObservation:
    outcome: JobOutcome
    reason: str
    sheets_completed: int | None = None
    physical_output_verified: bool = False


def job_observation(attributes, *, expected_job_id, expected_printer_uri, timed_out=False):
    """Require exact job/queue identity; absence and timeout never mean success.

    IPP completed (9) proves only the spooler's terminal state. It does not prove
    all requested pages emerged, and must not alone authorize money settlement.
    """
    if timed_out or not isinstance(attributes, Mapping):
        return JobObservation(JobOutcome.NEEDS_REVIEW, 'observation_lost')
    if (not _integer(expected_job_id) or expected_job_id <= 0
            or not isinstance(expected_printer_uri, str) or not expected_printer_uri
            or not _integer(attributes.get('job-id'))
            or attributes.get('job-id') != expected_job_id
            or attributes.get('job-printer-uri') != expected_printer_uri):
        return JobObservation(JobOutcome.NEEDS_REVIEW, 'identity_mismatch')
    state = attributes.get('job-state')
    sheets = attributes.get('job-media-sheets-completed')
    sheets = sheets if _integer(sheets) and sheets >= 0 else None
    if not _integer(state):
        return JobObservation(JobOutcome.NEEDS_REVIEW, 'unknown_job_state', sheets)
    outcomes = {3: JobOutcome.PENDING, 4: JobOutcome.HELD, 5: JobOutcome.PROCESSING,
                6: JobOutcome.HELD, 7: JobOutcome.NEEDS_REVIEW,
                8: JobOutcome.NEEDS_REVIEW, 9: JobOutcome.SPOOLER_COMPLETED}
    reasons = {3: 'pending', 4: 'held', 5: 'processing', 6: 'stopped',
               7: 'cancelled', 8: 'aborted', 9: 'spooler_completed'}
    return JobObservation(outcomes.get(state, JobOutcome.NEEDS_REVIEW),
                          reasons.get(state, 'unknown_job_state'), sheets)
