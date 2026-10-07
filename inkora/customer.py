"""Short-lived, kiosk-issued customer capabilities for staging simulations."""
import io
import secrets
import time
import uuid
import qrcode
from flask import abort, jsonify, render_template, request, send_file, session
from sqlalchemy import select
from . import db, csrf, Kiosk, User, digest
from .jobs import create_job, PrintJob
from .security import limit
from . import storage as documents


class CustomerSession(db.Model):
    id=db.Column(db.String(32),primary_key=True)
    token_hash=db.Column(db.String(64),unique=True,nullable=False)
    kiosk_id=db.Column(db.Integer,db.ForeignKey('kiosk.id'),nullable=False,index=True)
    expires=db.Column(db.Float,nullable=False,index=True)
    browser_hash=db.Column(db.String(64))


def register_customer(app):
    def enabled():
        if app.config['PRODUCTION']:
            abort(404)

    @app.post('/api/devices/customer-sessions')
    @csrf.exempt
    def customer_session_create():
        enabled()
        from .security import authenticate_device
        kiosk=authenticate_device()
        if kiosk.status!='Online':
            abort(409,'Kiosk unavailable')
        active=db.session.scalar(select(CustomerSession).where(CustomerSession.kiosk_id==kiosk.id,
            CustomerSession.expires>time.time()).limit(1))
        if active:
            abort(409,'A customer session is already active; wait for its expiry.')
        token=secrets.token_urlsafe(48)
        record=CustomerSession(id=uuid.uuid4().hex,token_hash=digest(token),kiosk_id=kiosk.id,expires=time.time()+180)
        db.session.add(record);db.session.commit()
        base=app.config['PUBLIC_URL'] or request.url_root.rstrip('/')
        return jsonify(upload_url=base+'/print/'+token,expires_in=180,simulation=True),201

    def get_session(token):
        enabled()
        if not 32<=len(token)<=100:
            abort(404)
        # Limits are capability-scoped, not a shared customer identity.
        limit('customer:'+digest(token),30,60)
        record=db.session.scalar(select(CustomerSession).where(CustomerSession.token_hash==digest(token)).with_for_update())
        if not record:
            abort(404)
        browser=session.get('customer_browser')
        if not browser:
            browser=secrets.token_urlsafe(32);session['customer_browser']=browser
        if record.browser_hash and record.browser_hash!=digest(browser):
            abort(403,'This session is already open on another browser.')
        if not record.browser_hash:
            record.browser_hash=digest(browser)
        return record

    @app.route('/print/<token>',methods=['GET','POST'])
    def customer_upload(token):
        record=get_session(token)
        job=db.session.scalar(select(PrintJob).where(PrintJob.customer_session_id==record.id))
        kiosk=db.session.get(Kiosk,record.kiosk_id)
        if record.expires+86400<time.time():
            abort(410,'This session has ended.')
        if not job and record.expires<time.time():
            abort(410,'This QR has expired. Start a new session at the kiosk.')
        if request.method=='POST':
            if job:
                db.session.commit()
                return render_template('customer.html',job=job,kiosk=kiosk),200
            owner=db.session.get(User,kiosk.owner_id)
            if kiosk.status!='Online' or owner.disabled:
                abort(409,'Kiosk is temporarily unavailable')
            mode=request.form.get('mode');duplex=request.form.get('duplex')
            if mode not in ('bw','color','art') or duplex not in ('single','duplex'):
                abort(400)
            job=create_job(kiosk,request.files.get('file'),mode,duplex,record.id)
            # No second job if the upload request is retried or the browser refreshes.
            try:
                db.session.commit()
            except BaseException:
                db.session.rollback();documents.delete(job.id);raise
        else:
            db.session.commit()
        return render_template('customer.html',job=job,kiosk=kiosk)

    @app.get('/print/<token>/qr.png')
    def customer_qr(token):
        enabled()
        record=db.session.scalar(select(CustomerSession).where(CustomerSession.token_hash==digest(token)))
        if not record or record.expires<time.time():
            abort(410)
        base=app.config['PUBLIC_URL'] or request.url_root.rstrip('/')
        image=qrcode.make(base+'/print/'+token)
        output=io.BytesIO();image.save(output,format='PNG');output.seek(0)
        return send_file(output,mimetype='image/png')
