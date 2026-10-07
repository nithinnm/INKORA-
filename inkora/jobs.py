"""Development-only print pipeline. Never authorizes a live payment or printer."""
import hashlib
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
import uuid
import tempfile
from . import storage as documents
from flask import abort, current_app, jsonify, redirect, render_template, request, send_file, url_for
from sqlalchemy import select, update
from . import db, csrf, Kiosk, digest


class JobReview(db.Model):
    id=db.Column(db.Integer,primary_key=True)
    job_id=db.Column(db.String(32),db.ForeignKey('print_job.id'),nullable=False)
    actor_id=db.Column(db.Integer,db.ForeignKey('user.id'),nullable=False)
    action=db.Column(db.String(10),nullable=False)
    reason=db.Column(db.String(500),nullable=False)
    timestamp=db.Column(db.Float,nullable=False,default=time.time)


class PrintJob(db.Model):
    id = db.Column(db.String(32), primary_key=True)
    owner_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    kiosk_id = db.Column(db.Integer, db.ForeignKey('kiosk.id'), nullable=False, index=True)
    pages = db.Column(db.Integer, nullable=False)
    amount_paise = db.Column(db.Integer, nullable=False)
    mode = db.Column(db.String(10), nullable=False)
    duplex = db.Column(db.Boolean, nullable=False)
    file_hash = db.Column(db.String(64), nullable=False)
    state = db.Column(db.String(20), nullable=False, default='queued')
    created_at = db.Column(db.Float, nullable=False, default=time.time)
    claim_hash = db.Column(db.String(64))
    lease_until = db.Column(db.Float)
    quote_snapshot = db.Column(db.JSON)
    deleted_at = db.Column(db.Float)
    customer_session_id = db.Column(db.String(32), unique=True)
    pricing_version = db.Column(db.String(20), nullable=False, default='reference-v1')
    __table_args__ = (db.CheckConstraint("state IN ('queued','claimed','printing','completed','needs_review','cancelled')", name='ck_print_job_state'),
                      db.CheckConstraint('pages BETWEEN 1 AND 250'), db.CheckConstraint('amount_paise > 0'))


def register_jobs(app, login_required):
    def enabled():
        if app.config['PRODUCTION']:
            abort(404)

    def storage():
        root = Path(app.config['PRINT_STORAGE'])
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        return root

    def device():
        enabled()
        from .security import authenticate_device
        return authenticate_device()

    def payload():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            abort(400)
        return body

    def claim_condition(kiosk, job_id, token):
        if not isinstance(token, str) or not 32 <= len(token) <= 100:
            abort(400)
        return (PrintJob.id == job_id, PrintJob.kiosk_id == kiosk.id,
                PrintJob.claim_hash == digest(token), PrintJob.lease_until > time.time())

    @app.get('/jobs')
    @login_required()
    def jobs(user):
        enabled()
        query = select(PrintJob).order_by(PrintJob.created_at.desc()).limit(100)
        kiosks = select(Kiosk).order_by(Kiosk.id)
        if user.role != 'admin':
            query = query.where(PrintJob.owner_id == user.id)
            kiosks = kiosks.where(Kiosk.owner_id == user.id)
        return render_template('jobs.html', admin=user.role=='admin', jobs=db.session.scalars(query).all(), kiosks=db.session.scalars(kiosks).all())

    @app.post('/jobs')
    @login_required()
    def upload_job(user):
        enabled()
        try:
            kiosk_id = int(request.form.get('kiosk_id', ''))
        except ValueError:
            abort(400)
        kiosk = db.get_or_404(Kiosk, kiosk_id)
        if user.role != 'admin' and kiosk.owner_id != user.id:
            abort(404)
        if kiosk.status != 'Online':
            abort(409, 'Kiosk must be online and ready')
        mode = request.form.get('mode')
        duplex = request.form.get('duplex')
        if mode not in ('bw', 'color', 'art') or duplex not in ('single', 'duplex'):
            abort(400)
        create_job(kiosk, request.files.get('file'), mode, duplex)
        return redirect(url_for('jobs'))

    @app.post('/api/devices/jobs/claim')
    @csrf.exempt
    def claim():
        kiosk = device()
        if kiosk.status != 'Online':
            abort(409, 'Device must report ready')
        # Expiry is ambiguous: no automatic requeue or duplicate printing.
        db.session.execute(update(PrintJob).where(PrintJob.kiosk_id == kiosk.id,
            PrintJob.state.in_(['claimed','printing']), PrintJob.lease_until <= time.time()).values(state='needs_review', claim_hash=None))
        active = db.session.scalar(select(PrintJob.id).where(PrintJob.kiosk_id == kiosk.id,
            PrintJob.state.in_(['claimed','printing'])).limit(1))
        if active:
            db.session.commit()
            return '', 204
        job = db.session.scalar(select(PrintJob).where(PrintJob.kiosk_id == kiosk.id,
            PrintJob.state == 'queued').order_by(PrintJob.created_at).with_for_update(skip_locked=True).limit(1))
        if not job:
            db.session.commit()
            return '', 204
        token = secrets.token_urlsafe(48)
        job.state = 'claimed'; job.claim_hash = digest(token); job.lease_until = time.time()+300
        db.session.commit()
        return jsonify(job_id=job.id, claim_token=token, file_hash=job.file_hash, pages=job.pages,
                       mode=job.mode, duplex=job.duplex, simulation=True), 200

    @app.get('/api/devices/jobs/<job_id>/file')
    def download(job_id):
        kiosk = device()
        token = request.headers.get('X-Claim-Token')
        job = db.session.scalar(select(PrintJob).where(*claim_condition(kiosk,job_id,token), PrintJob.state == 'claimed'))
        if not job:
            abort(404)
        if job.deleted_at:
            abort(410)
        try:
            return documents.response(job.id)
        except FileNotFoundError:
            abort(410)

    @app.post('/api/devices/jobs/<job_id>/transition')
    @csrf.exempt
    def transition(job_id):
        kiosk = device(); body = payload(); state = body.get('state')
        if state not in ('printing','completed','needs_review','cancelled'):
            abort(400)
        previous = ['claimed'] if state == 'printing' else ['printing'] if state == 'completed' else ['claimed','printing']
        changed = db.session.execute(update(PrintJob).where(*claim_condition(kiosk,job_id,body.get('claim_token')),
            PrintJob.state.in_(previous)).values(state=state).returning(PrintJob.id)).scalar_one_or_none()
        if not changed:
            abort(409, 'Invalid or expired claim/state transition')
        db.session.commit()
        return jsonify(state=state, simulation=True)


    @app.post('/api/devices/jobs/<job_id>/renew')
    @csrf.exempt
    def renew(job_id):
        kiosk=device();body=payload()
        changed=db.session.execute(update(PrintJob).where(*claim_condition(kiosk,job_id,body.get('claim_token')),
            PrintJob.state.in_(['claimed','printing'])).values(lease_until=time.time()+300).returning(PrintJob.id)).scalar_one_or_none()
        if not changed:
            abort(409,'Claim expired or unavailable')
        db.session.commit()
        return jsonify(lease_seconds=300)

    @app.post('/jobs/<job_id>/review')
    @login_required(admin=True)
    def review(user,job_id):
        enabled()
        from . import Audit
        action=request.form.get('action')
        reason=request.form.get('reason','').strip()
        if action not in ('cancel','retry') or not 10<=len(reason)<=500:
            abort(400,'Select an action and give a review reason of 10–500 characters.')
        job=db.session.scalar(select(PrintJob).where(PrintJob.id==job_id).with_for_update())
        if not job:
            abort(404)
        if job.state!='needs_review':
            abort(409)
        if action=='retry' and (job.deleted_at or request.form.get('confirm')!='checked'):
            abort(400,'Confirm no physical output before retrying; document must still exist.')
        job.state='queued' if action=='retry' else 'cancelled'
        job.claim_hash=None;job.lease_until=None
        db.session.add(Audit(actor_id=user.id,action='job_'+action+':'+job.id))
        db.session.add(JobReview(job_id=job.id,actor_id=user.id,action=action,reason=reason))
        db.session.commit()
        return redirect(url_for('jobs'))



def create_job(kiosk, file, mode, duplex, customer_session_id=None):
    if not file or not (file.filename or '').lower().endswith('.pdf'):
        abort(400, 'Upload a PDF')
    job_id=uuid.uuid4().hex
    fd,name=tempfile.mkstemp(suffix='.pdf')
    path=Path(name); uploaded=False
    try:
        with os.fdopen(fd,'wb') as target:
            total=0
            while chunk:=file.stream.read(65536):
                total+=len(chunk)
                if total>10*1024*1024:
                    abort(413,'PDF exceeds 10 MB')
                target.write(chunk)
        with path.open('rb') as source:
            if source.read(5)!=b'%PDF-':
                abort(400,'Invalid PDF')
        result=subprocess.run([sys.executable,str(Path(__file__).with_name('pdf_check.py')),str(path)],capture_output=True,timeout=8,check=False)
        if result.returncode!=0:
            abort(400,'Unreadable, encrypted, over-limit or unsafe PDF')
        pages=int(result.stdout.strip())
        rate=kiosk.prices.get(mode+'_'+duplex)
        if not isinstance(rate,int) or not 25<=rate<=100000:
            abort(400,'Invalid print options')
        discount=30 if pages>=101 else 20 if pages>=51 else 10 if pages>=21 else 0
        per_page=(rate*(100-discount)+50)//100
        job=PrintJob(id=job_id,owner_id=kiosk.owner_id,kiosk_id=kiosk.id,pages=pages,amount_paise=pages*per_page,
            mode=mode,duplex=duplex=='duplex',file_hash=hashlib.sha256(path.read_bytes()).hexdigest(),customer_session_id=customer_session_id,
            pricing_version='kiosk-v'+str(kiosk.configuration_version),quote_snapshot={'rate_paise':rate,'discount_percent':discount,'per_page_paise':per_page})
        documents.put(job_id,path); uploaded=True
        db.session.add(job); db.session.flush()
        if customer_session_id is None:
            db.session.commit()
        return job
    except subprocess.TimeoutExpired:
        abort(400,'PDF validation timed out')
    except BaseException:
        if uploaded:
            documents.delete(job_id)
        raise
    finally:
        path.unlink(missing_ok=True)
