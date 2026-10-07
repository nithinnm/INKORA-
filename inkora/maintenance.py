"""Idempotent maintenance commands, suitable for scheduled container jobs."""
import time
import click
from sqlalchemy import select, update
from . import db
from . import storage as documents
from .jobs import PrintJob
from .security import RateBucket, AccountToken
from .customer import CustomerSession


def register_maintenance(app):
    @app.cli.command('maintenance')
    @click.option('--retention-hours',default=24,type=click.IntRange(1,168))
    def maintenance(retention_hours):
        now=time.time()
        # Run independently of device polling so operator alerts remain meaningful.
        db.session.execute(update(PrintJob).where(PrintJob.state.in_(['claimed','printing']),PrintJob.lease_until<=now)
            .values(state='needs_review',claim_hash=None))
        db.session.execute(update(PrintJob).where(PrintJob.state=='queued',PrintJob.created_at<now-retention_hours*3600).values(state='cancelled'))
        db.session.commit()
        deleted=0
        jobs=db.session.scalars(select(PrintJob).where(PrintJob.created_at<now-retention_hours*3600,
            PrintJob.deleted_at.is_(None),PrintJob.state.in_(['completed','cancelled','needs_review']))).all()
        for job in jobs:
            documents.delete(job.id)  # Fail rather than claim deletion when storage is unavailable.
            job.deleted_at=now
            db.session.commit();deleted+=1
        db.session.execute(db.delete(RateBucket).where(RateBucket.expires<now))
        db.session.execute(db.delete(AccountToken).where(AccountToken.expires<now))
        # Session records with a job are retained for status lookup; capabilities expire.
        used=select(PrintJob.customer_session_id).where(PrintJob.customer_session_id.is_not(None))
        db.session.execute(db.delete(CustomerSession).where(CustomerSession.expires<now-86400,CustomerSession.id.not_in(used)))
        db.session.commit()
        click.echo(f'Maintenance complete; {deleted} document(s) deleted. No job requeued.')
