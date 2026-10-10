"""Owner-scoped service requests with append-only history and guarded updates."""
import re
import time
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo
from flask import abort, redirect, render_template, request, url_for
from sqlalchemy import select, update, func
from . import db, Kiosk, Audit
from .security import limit

STATUSES = ('open', 'in_progress', 'resolved')
TRANSITIONS = {'open': {'open', 'in_progress'},
               'in_progress': {'in_progress', 'resolved'},
               'resolved': {'open'}}


class ServiceRequest(db.Model):
    id = db.Column(db.String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    kiosk_id = db.Column(db.Integer, db.ForeignKey('kiosk.id'), nullable=False, index=True)
    owner_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    requester_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    request_key = db.Column(db.String(32), nullable=False)
    title = db.Column(db.String(120), nullable=False)
    description = db.Column(db.String(2000), nullable=False)
    category = db.Column(db.String(20), nullable=False)
    status = db.Column(db.String(20), nullable=False, default='open', index=True)
    version = db.Column(db.Integer, nullable=False, default=1)
    created_at = db.Column(db.Float, nullable=False, default=time.time)
    updated_at = db.Column(db.Float, nullable=False, default=time.time)
    __table_args__ = (
        db.UniqueConstraint('requester_id', 'request_key', name='uq_service_request_retry'),
        db.CheckConstraint("status IN ('open','in_progress','resolved')", name='ck_service_request_status'),
        db.CheckConstraint("category IN ('printer','paper_ink','connectivity','other')", name='ck_service_request_category'),
        db.CheckConstraint('version > 0', name='ck_service_request_version'),
    )


class ServiceEvent(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    request_id = db.Column(db.String(32), db.ForeignKey('service_request.id'), nullable=False, index=True)
    actor_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    status = db.Column(db.String(20), nullable=False)
    note = db.Column(db.String(2000), nullable=False)
    created_at = db.Column(db.Float, nullable=False, default=time.time)


def scope(user):
    return [] if user.role == 'admin' else [ServiceRequest.owner_id == user.id, Kiosk.owner_id == user.id]


def request_query(user):
    return select(ServiceRequest).join(Kiosk, Kiosk.id == ServiceRequest.kiosk_id).where(*scope(user))


def active_count(user):
    return db.session.scalar(select(func.count(ServiceRequest.id)).join(Kiosk,
        Kiosk.id == ServiceRequest.kiosk_id).where(*scope(user), ServiceRequest.status != 'resolved'))


def register_support(app, login_required):
    def text(name, minimum, maximum):
        value = request.form.get(name, '').strip()
        if not minimum <= len(value) <= maximum:
            abort(400, f'{name.title()} must contain {minimum}–{maximum} characters.')
        return value

    def visible(user, request_id):
        ticket = db.session.scalar(request_query(user).where(ServiceRequest.id == request_id))
        if not ticket:
            abort(404)
        return ticket

    @app.template_filter('support_time')
    def support_time(value):
        return datetime.fromtimestamp(value, ZoneInfo('Asia/Kolkata')).strftime('%d %b %Y %H:%M IST')

    @app.get('/support')
    @login_required()
    def service_requests(user):
        try:
            page = int(request.args.get('page', '1'))
            if not 1 <= page <= 10000:
                raise ValueError()
        except ValueError:
            abort(400, 'Invalid page.')
        tickets = db.session.execute(request_query(user).add_columns(Kiosk.name)
            .order_by(ServiceRequest.updated_at.desc(), ServiceRequest.id)
            .offset((page - 1)*50).limit(51)).all()
        kiosks = select(Kiosk).order_by(Kiosk.name, Kiosk.id)
        if user.role != 'admin':
            kiosks = kiosks.where(Kiosk.owner_id == user.id)
        return render_template('support.html', user=user, tickets=tickets[:50],
            has_next=len(tickets)>50, page=page, kiosks=db.session.scalars(kiosks).all(), request_key=uuid.uuid4().hex)

    @app.post('/support')
    @login_required()
    def create_service_request(user):
        key = request.form.get('request_key', '')
        if not re.fullmatch('[a-f0-9]{32}', key):
            abort(400, 'Invalid request key. Reload the form.')
        try:
            kiosk_id = int(request.form.get('kiosk_id', ''))
        except ValueError:
            abort(400, 'Select a kiosk.')
        kiosk = db.session.get(Kiosk, kiosk_id)
        if not kiosk or (user.role != 'admin' and kiosk.owner_id != user.id):
            abort(404)
        title = text('title', 5, 120)
        description = text('description', 10, 2000)
        category = request.form.get('category')
        if category not in ('printer', 'paper_ink', 'connectivity', 'other'):
            abort(400, 'Select a category.')
        previous = db.session.scalar(request_query(user).where(
            ServiceRequest.requester_id == user.id, ServiceRequest.request_key == key))
        if previous:
            if (previous.kiosk_id, previous.title, previous.description, previous.category) != (kiosk.id, title, description, category):
                abort(409, 'Request key already used for different details. Reload the form.')
            return redirect(url_for('service_request_detail', request_id=previous.id))
        limit('support:'+str(user.id), 10, 3600)
        ticket = ServiceRequest(kiosk_id=kiosk.id, owner_id=kiosk.owner_id, requester_id=user.id,
                                request_key=key, title=title, description=description, category=category)
        db.session.add(ticket)
        db.session.flush()
        db.session.add(ServiceEvent(request_id=ticket.id, actor_id=user.id, status='open', note='Service request opened.'))
        db.session.add(Audit(actor_id=user.id, action='service_request_opened', target_id=kiosk.id))
        db.session.commit()
        return redirect(url_for('service_request_detail', request_id=ticket.id))

    @app.get('/support/<request_id>')
    @login_required()
    def service_request_detail(user, request_id):
        ticket = visible(user, request_id)
        events = db.session.scalars(select(ServiceEvent).where(ServiceEvent.request_id == ticket.id)
            .order_by(ServiceEvent.created_at, ServiceEvent.id)).all()
        return render_template('support_detail.html', user=user, ticket=ticket,
            kiosk=db.session.get(Kiosk, ticket.kiosk_id), events=events,
            transitions=sorted(TRANSITIONS[ticket.status]))

    @app.post('/support/<request_id>/update')
    @login_required(admin=True)
    def update_service_request(user, request_id):
        ticket = visible(user, request_id)
        note = text('note', 10, 2000)
        status = request.form.get('status')
        try:
            version = int(request.form.get('version', ''))
        except ValueError:
            abort(400, 'Invalid request version.')
        if status not in TRANSITIONS[ticket.status]:
            abort(409, 'Invalid status transition. Refresh the request.')
        changed = db.session.execute(update(ServiceRequest).where(ServiceRequest.id == ticket.id,
            ServiceRequest.version == version, ServiceRequest.status == ticket.status).values(
                status=status, version=ServiceRequest.version + 1, updated_at=time.time())
                .returning(ServiceRequest.id)).scalar_one_or_none()
        if not changed:
            abort(409, 'Another update was saved. Refresh before changing this request.')
        db.session.add(ServiceEvent(request_id=ticket.id, actor_id=user.id, status=status, note=note))
        db.session.add(Audit(actor_id=user.id, action='service_request_updated', target_id=ticket.kiosk_id))
        db.session.commit()
        return redirect(url_for('service_request_detail', request_id=ticket.id))
