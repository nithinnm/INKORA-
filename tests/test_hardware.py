"""Hardware acceptance cases use synthetic IPP responses, never a real printer."""
import subprocess
from types import SimpleNamespace

import pytest

from agent.hardware import Availability, JobOutcome, job_observation, printer_health
from agent import cups_probe


def healthy(**changes):
    return {'printer-state': 3, 'printer-is-accepting-jobs': True,
            'printer-state-reasons': ['none'], **changes}


def job(**changes):
    return {'job-id': 42, 'job-printer-uri': 'ipp://localhost/printers/test',
            'job-state': 9, 'job-media-sheets-completed': 2, **changes}


def observe(attributes, **changes):
    return job_observation(attributes, expected_job_id=42,
                           expected_printer_uri='ipp://localhost/printers/test', **changes)


@pytest.mark.parametrize('attrs', [None, {}, [], healthy(**{'printer-state': True}),
    healthy(**{'printer-is-accepting-jobs': 'true'}),
    healthy(**{'printer-state-reasons': []}),
    healthy(**{'printer-state-reasons': ['none', 'media-low-warning']})])
def test_incomplete_or_contradictory_telemetry_blocks_jobs(attrs):
    assert printer_health(attrs).availability == Availability.UNKNOWN
    assert not printer_health(attrs).may_start_job


def test_fresh_idle_without_supply_measurements_keeps_ink_unknown():
    status = printer_health(healthy())
    assert status.may_start_job and status.supply_percent == ()
    assert not printer_health(healthy(), fresh=False).may_start_job


@pytest.mark.parametrize('reason', ['media-jam', 'media-jam-warning', 'media-empty-error',
    'marker-supply-empty', 'cover-open', 'offline', 'connecting-to-device',
    'output-area-full', 'spool-area-full'])
def test_hardware_blockers_refuse_print_even_with_idle_queue(reason):
    status = printer_health(healthy(**{'printer-state-reasons': [reason]}))
    assert status.availability == Availability.UNAVAILABLE and not status.may_start_job


def test_unknown_errors_cannot_be_treated_as_ready():
    assert not printer_health(healthy(**{'printer-state-reasons': ['vendor-fault-error']})).may_start_job
    assert not printer_health(healthy(**{'printer-state-reasons': ['other']})).may_start_job


def test_low_supply_warns_but_empty_supply_blocks_and_sentinels_stay_unknown():
    attrs = healthy(**{'printer-state-reasons': ['marker-supply-low-warning'],
                       'marker-levels': [8, -1, -2, -3, 101, True]})
    result = printer_health(attrs)
    assert result.may_start_job and result.warning
    assert result.supply_percent == (8, None, None, None, None, None)
    assert not printer_health(healthy(**{'marker-levels': [0]})).may_start_job


@pytest.mark.parametrize('changes', [{'printer-state': 4}, {'printer-state': 5},
                                     {'printer-is-accepting-jobs': False}])
def test_processing_or_disabled_queue_cannot_start_another_job(changes):
    assert not printer_health(healthy(**changes)).may_start_job


@pytest.mark.parametrize('state,outcome', [(3, JobOutcome.PENDING), (4, JobOutcome.HELD),
    (5, JobOutcome.PROCESSING), (6, JobOutcome.HELD), (7, JobOutcome.NEEDS_REVIEW),
    (8, JobOutcome.NEEDS_REVIEW), (9, JobOutcome.SPOOLER_COMPLETED),
    (99, JobOutcome.NEEDS_REVIEW), (True, JobOutcome.NEEDS_REVIEW)])
def test_exact_terminal_job_state_is_required(state, outcome):
    result = observe(job(**{'job-state': state}))
    assert result.outcome == outcome
    assert not result.physical_output_verified


@pytest.mark.parametrize('attrs', [None, {}, job(**{'job-id': 43}),
    job(**{'job-id': True}), job(**{'job-printer-uri': 'ipp://localhost/printers/other'})])
def test_missing_job_or_wrong_queue_requires_review(attrs):
    assert observe(attrs).outcome == JobOutcome.NEEDS_REVIEW


def test_timeout_never_becomes_success_and_sheets_are_not_document_pages():
    assert observe(job(), timed_out=True).outcome == JobOutcome.NEEDS_REVIEW
    assert observe(job()).sheets_completed == 2
    assert observe(job(**{'job-media-sheets-completed': -1})).sheets_completed is None


@pytest.mark.parametrize('failure', [subprocess.TimeoutExpired('probe', 5),
    subprocess.CalledProcessError(1, 'probe'), OSError('unavailable')])
def test_probe_failure_returns_unknown_without_fallback_or_raw_error(monkeypatch, failure):
    def fail(*args, **kwargs):
        raise failure
    monkeypatch.setattr(cups_probe.subprocess, 'run', fail)
    assert cups_probe.probe('Epson_WF_C5890').availability == Availability.UNKNOWN


@pytest.mark.parametrize('output', [b'not json', b'x' * 16385, b'null'])
def test_malformed_or_oversized_probe_data_fails_closed(monkeypatch, output):
    monkeypatch.setattr(cups_probe.subprocess, 'run', lambda *a, **k: SimpleNamespace(stdout=output))
    assert not cups_probe.probe('Epson_WF_C5890').may_start_job


def test_probe_is_bounded_and_uses_exact_queue_without_shell(monkeypatch):
    def run(command, **kwargs):
        assert command[-1] == 'Epson_WF_C5890'
        assert kwargs['timeout'] == 5 and not kwargs.get('shell')
        assert 'getPrinterAttributes' in command[2] and 'printFile' not in command[2]
        assert '/run/cups/cups.sock' in command[2]
        return SimpleNamespace(stdout=b'{"printer-state":3,"printer-is-accepting-jobs":true,"printer-state-reasons":["none"]}')
    monkeypatch.setattr(cups_probe.subprocess, 'run', run)
    assert cups_probe.probe('Epson_WF_C5890').may_start_job


@pytest.mark.parametrize('queue', ['-x', '../printer', 'printer;command', '', None])
def test_invalid_queue_rejected_before_probe(queue):
    with pytest.raises(ValueError):
        cups_probe.probe(queue)


def test_crash_before_cups_id_requires_review_and_never_resubmits(tmp_path):
    from agent.submission_journal import SubmissionJournal
    path = tmp_path/'journal.db'
    journal = SubmissionJournal(path)
    journal.reserve_handoff('a'*32, 'ipp://localhost/printers/test')
    journal.close()
    restarted = SubmissionJournal(path)
    assert restarted.recovery()[0]['action'] == 'operator_review'
    with pytest.raises(RuntimeError, match='do not resubmit'):
        restarted.reserve_handoff('a'*32, 'ipp://localhost/printers/test')
    restarted.close()


def test_crash_after_cups_id_reconciles_exact_job_and_preserves_fence(tmp_path):
    from agent.submission_journal import SubmissionJournal
    path = tmp_path/'journal.db'
    journal = SubmissionJournal(path)
    journal.reserve_handoff('a'*32, 'ipp://localhost/printers/test')
    journal.record_submission('a'*32, 42)
    journal.close()
    restarted = SubmissionJournal(path)
    row = restarted.recovery()[0]
    assert row['action'] == 'inspect_exact_cups_job' and row['cups_job_id'] == 42
    with pytest.raises(RuntimeError):
        restarted.record_submission('a'*32, 43)
    with pytest.raises(RuntimeError):
        restarted.reserve_handoff('a'*32, 'ipp://localhost/printers/test')
    restarted.close()


def test_two_agent_connections_cannot_reserve_same_job(tmp_path):
    from agent.submission_journal import SubmissionJournal
    first = SubmissionJournal(tmp_path/'journal.db')
    second = SubmissionJournal(tmp_path/'journal.db')
    first.reserve_handoff('a'*32, 'ipp://localhost/printers/test')
    with pytest.raises(RuntimeError):
        second.reserve_handoff('a'*32, 'ipp://localhost/printers/test')
    assert len(first.recovery()) == 1
    first.close()
    second.close()


def test_journal_rejects_shared_permissions_and_symlinks(tmp_path):
    from agent.submission_journal import SubmissionJournal
    path = tmp_path/'shared.db'
    path.touch(mode=0o644)
    path.chmod(0o644)
    with pytest.raises(ValueError, match='owner-only'):
        SubmissionJournal(path)
    link = tmp_path/'link.db'
    link.symlink_to(path)
    with pytest.raises(OSError):
        SubmissionJournal(link)
