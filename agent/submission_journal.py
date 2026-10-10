"""Durable local printer handoff fence. No submission, replay or settlement API."""
import os
from pathlib import Path
import re
import sqlite3


class SubmissionJournal:
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            if os.fstat(fd).st_mode & 0o077:
                raise ValueError('Journal must have owner-only permissions.')
        finally:
            os.close(fd)
        self.connection = sqlite3.connect(path, timeout=5)
        self.connection.execute('PRAGMA synchronous=FULL')
        self.connection.execute('''CREATE TABLE IF NOT EXISTS submissions (
            job_id TEXT PRIMARY KEY, state TEXT NOT NULL,
            cups_job_id INTEGER, printer_uri TEXT NOT NULL)''')
        self.connection.commit()

    def reserve_handoff(self, job_id, printer_uri):
        """Commit intent BEFORE sending to CUPS. Never repeat a reserved handoff.

        A crash after this commit but before storing the returned CUPS ID has an
        ambiguous outcome, including when no document reached the printer.
        Operator reconciliation is required; uncertainty must not trigger replay.
        """
        if not isinstance(job_id, str) or not re.fullmatch(r'[a-f0-9]{32}', job_id):
            raise ValueError('Expected a fleet job ID.')
        if not isinstance(printer_uri, str) or not printer_uri.startswith(('ipp://', 'ipps://')):
            raise ValueError('Expected an IPP queue URI.')
        try:
            with self.connection:
                self.connection.execute('INSERT INTO submissions VALUES (?, ?, NULL, ?)',
                                        (job_id, 'handoff_intent', printer_uri))
        except sqlite3.IntegrityError:
            raise RuntimeError('Handoff already reserved; do not resubmit.') from None

    def record_submission(self, job_id, cups_job_id):
        if not isinstance(cups_job_id, int) or isinstance(cups_job_id, bool) or cups_job_id <= 0:
            raise ValueError('Expected an exact positive CUPS job ID.')
        with self.connection:
            cursor = self.connection.execute('''UPDATE submissions SET state='submitted', cups_job_id=?
                WHERE job_id=? AND state='handoff_intent' AND cups_job_id IS NULL''', (cups_job_id, job_id))
            if cursor.rowcount != 1:
                raise RuntimeError('Unexpected journal state; do not resubmit.')

    def recovery(self):
        """Return reconciliation instructions; never make interrupted jobs ready."""
        rows = self.connection.execute('SELECT job_id, state, cups_job_id, printer_uri FROM submissions ORDER BY job_id')
        return [{'job_id': job, 'state': state, 'cups_job_id': number,
                 'printer_uri': uri, 'action': 'operator_review' if number is None else 'inspect_exact_cups_job'}
                for job, state, number, uri in rows]

    def close(self):
        self.connection.close()
