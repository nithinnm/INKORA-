"""Tenant-scoped control-room summaries from existing simulated-job data."""
from datetime import datetime, time as daytime, timedelta, timezone
from zoneinfo import ZoneInfo
from sqlalchemy import case, func, select
from . import db, Kiosk
from .jobs import PrintJob

INDIA = ZoneInfo('Asia/Kolkata')


def summary(user):
    now = datetime.now(timezone.utc).astimezone(INDIA)
    start = datetime.combine(now.date(), daytime.min, tzinfo=INDIA)
    end = start + timedelta(days=1)
    scope = [] if user.role == 'admin' else [PrintJob.owner_id == user.id, Kiosk.owner_id == user.id]
    query = select(
        func.count(PrintJob.id),
        func.coalesce(func.sum(PrintJob.pages), 0),
        func.coalesce(func.sum(case((PrintJob.state == 'completed', 1), else_=0)), 0),
        func.coalesce(func.sum(case((PrintJob.state == 'needs_review', 1), else_=0)), 0),
        func.coalesce(func.sum(case((PrintJob.state == 'completed', PrintJob.amount_paise), else_=0)), 0),
    ).join(Kiosk, Kiosk.id == PrintJob.kiosk_id).where(
        *scope, PrintJob.created_at >= start.timestamp(), PrintJob.created_at < end.timestamp())
    total, pages, completed, review, quoted = db.session.execute(query).one()
    recent = db.session.execute(select(PrintJob, Kiosk.name).join(Kiosk, Kiosk.id == PrintJob.kiosk_id)
        .where(*scope).order_by(PrintJob.created_at.desc(), PrintJob.id).limit(10)).all()
    return dict(jobs=total, pages=pages, completed=completed, review=review,
                average_pages=round(pages / total, 1) if total else 0,
                completed_quote=f'{quoted / 100:.2f}', day=start.strftime('%d %b %Y'),
                recent=[dict(id=j.id, kiosk=name, pages=j.pages, amount=f'{j.amount_paise / 100:.2f}',
                             state=j.state, submitted=datetime.fromtimestamp(j.created_at, INDIA).strftime('%d %b %H:%M IST'))
                        for j, name in recent])
