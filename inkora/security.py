"""Database-backed limits, account recovery and mandatory production admin MFA."""
import base64
import hashlib
import secrets
import time
import click
from cryptography.fernet import Fernet
import pyotp
from flask import abort, jsonify, redirect, render_template, request, session, url_for
from sqlalchemy import select, update
from werkzeug.security import generate_password_hash
from . import db, User, Audit, digest


class RateBucket(db.Model):
    key = db.Column(db.String(64), primary_key=True)
    count = db.Column(db.Integer, nullable=False)
    expires = db.Column(db.Float, nullable=False, index=True)


class AccountToken(db.Model):
    token_hash = db.Column(db.String(64), primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    expires = db.Column(db.Float, nullable=False)


def limit(key, maximum, window):
    now = time.time()
    # Fixed-window key and atomic UPSERT work across workers/instances.
    bucket_key = digest(key + ':' + str(int(now // window)))
    if db.engine.dialect.name == 'postgresql':
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    stmt = insert(RateBucket).values(key=bucket_key, count=1, expires=now+window)
    count = db.session.execute(stmt.on_conflict_do_update(index_elements=['key'],
        set_={'count': RateBucket.count + 1}).returning(RateBucket.count)).scalar_one()
    db.session.commit()
    if count > maximum:
        abort(429, 'Too many attempts. Please try again later.')


def cipher():
    from flask import current_app
    key = current_app.config['ENCRYPTION_KEY']
    if not key:
        key = base64.urlsafe_b64encode(hashlib.sha256(current_app.config['SECRET_KEY'].encode()).digest())
    return Fernet(key)


def verify_totp(user, code):
    if not user.totp_encrypted or not isinstance(code, str) or not code.isdigit() or len(code) != 6:
        return False
    totp = pyotp.TOTP(cipher().decrypt(user.totp_encrypted.encode()).decode())
    counter = int(time.time() // 30)
    for candidate in (counter-1, counter, counter+1):
        if candidate > user.totp_counter and secrets.compare_digest(totp.at(candidate * 30), code):
            user.totp_counter = candidate
            return True
    return False


def register_security(app, login_required):
    @app.before_request
    def basic_limits():
        if request.path == '/login' and request.method == 'POST':
            limit('login:' + request.remote_addr, 50, 300)
        elif request.path.startswith('/api/devices/'):
            limit('device-transport:' + str(request.remote_addr), 6000, 60)
            credential = request.headers.get('Authorization', '')
            # Activation has no credential; throttle separately by transport address.
            limit('device:' + (credential if credential else str(request.remote_addr)), 120, 60)
        elif request.path.startswith('/account/reset/'):
            limit('reset:' + str(request.remote_addr), 30, 300)

    @app.route('/account/mfa', methods=['GET','POST'])
    @login_required()
    def mfa(user):
        if user.totp_encrypted:
            return render_template('message.html', title='Authenticator enabled', message='Your account uses two-step authentication.')
        if 'mfa_setup' not in session:
            session['mfa_setup'] = cipher().encrypt(pyotp.random_base32().encode()).decode()
        secret = cipher().decrypt(session['mfa_setup'].encode()).decode()
        if request.method == 'POST':
            limit('mfa-enroll:' + str(user.id), 10, 300)
            if not pyotp.TOTP(secret).verify(request.form.get('code',''), valid_window=0):
                abort(400, 'Invalid authenticator code')
            user.totp_encrypted = cipher().encrypt(secret.encode()).decode()
            user.totp_counter = int(time.time() // 30)
            db.session.add(Audit(actor_id=user.id, action='mfa_enabled'))
            db.session.commit()
            session.pop('mfa_setup', None)
            session['mfa_verified'] = True
            return redirect(url_for('dashboard'))
        return render_template('mfa.html', secret=secret)

    @app.route('/account/challenge', methods=['GET','POST'])
    def challenge():
        user = db.session.get(User, session.get('pending_user')) if session.get('pending_user') else None
        if not user or user.disabled or session.get('pending_expiry',0) < time.time() or session.get('pending_version') != user.session_version:
            session.clear()
            return redirect(url_for('login'))
        if request.method == 'POST':
            limit('mfa:' + str(user.id), 10, 300)
            user = db.session.scalar(select(User).where(User.id==user.id).with_for_update())
            if not verify_totp(user, request.form.get('code','')):
                abort(401, 'Invalid or already used authenticator code')
            db.session.commit()
            session.clear()
            session.update(user_id=user.id, session_version=user.session_version, mfa_verified=True)
            session.permanent=True
            return redirect(url_for('dashboard'))
        return render_template('challenge.html')

    @app.post('/accounts/<int:user_id>/recovery')
    @login_required(admin=True)
    def recovery(admin, user_id):
        user = db.get_or_404(User,user_id)
        if user.role != 'owner':
            abort(403)
        token = secrets.token_urlsafe(48)
        db.session.execute(db.delete(AccountToken).where(AccountToken.user_id==user.id))
        db.session.add(AccountToken(token_hash=digest(token),user_id=user.id,expires=time.time()+1800))
        db.session.add(Audit(actor_id=admin.id,action='owner_recovery_issued',target_id=user.id))
        db.session.commit()
        return render_template('recovery.html', path='/account/reset/'+token)

    @app.post('/accounts/<int:user_id>/disable')
    @login_required(admin=True)
    def disable(admin,user_id):
        user = db.get_or_404(User,user_id)
        if user.role != 'owner':
            abort(403)
        user.disabled=True; user.session_version+=1
        db.session.execute(db.delete(AccountToken).where(AccountToken.user_id==user.id))
        from . import Kiosk
        from .jobs import PrintJob
        kiosk_ids=[]
        for kiosk in db.session.scalars(select(Kiosk).where(Kiosk.owner_id==user.id).with_for_update(of=Kiosk)).all():
            kiosk_ids.append(kiosk.id)
            kiosk.device_hash=None;kiosk.previous_device_hash=None;kiosk.previous_device_until=None
            kiosk.activation_hash=None;kiosk.activation_expires=None;kiosk.last_seen=None
        db.session.execute(update(PrintJob).where(PrintJob.kiosk_id.in_(kiosk_ids),PrintJob.state.in_(['claimed','printing'])).values(state='needs_review',claim_hash=None))
        db.session.execute(update(PrintJob).where(PrintJob.kiosk_id.in_(kiosk_ids),PrintJob.state=='queued').values(state='cancelled'))
        db.session.add(Audit(actor_id=admin.id,action='owner_disabled',target_id=user.id))
        db.session.commit()
        return redirect(url_for('dashboard'))

    @app.post('/accounts/<int:user_id>/enable')
    @login_required(admin=True)
    def enable(admin,user_id):
        user=db.get_or_404(User,user_id)
        if user.role!='owner' or not user.disabled:
            abort(409)
        user.disabled=False;user.session_version+=1
        user.password_hash=generate_password_hash(secrets.token_urlsafe(48))
        token=secrets.token_urlsafe(48)
        db.session.add(AccountToken(token_hash=digest(token),user_id=user.id,expires=time.time()+1800))
        db.session.add(Audit(actor_id=admin.id,action='owner_reenabled',target_id=user.id))
        db.session.commit()
        return render_template('recovery.html',path='/account/reset/'+token)

    @app.route('/account/reset/<token>',methods=['GET','POST'])
    def reset(token):
        account_token=db.session.scalar(select(AccountToken).where(AccountToken.token_hash==digest(token),AccountToken.expires>time.time()).with_for_update())
        if not account_token:
            abort(410, 'This recovery link is invalid or expired.')
        if request.method=='POST':
            password=request.form.get('password','')
            if not 14 <= len(password) <= 256:
                abort(400,'Use 14–256 characters')
            user=db.session.get(User,account_token.user_id)
            if user.disabled:
                abort(403)
            user.password_hash=generate_password_hash(password)
            user.session_version+=1; user.failed_attempts=0; user.locked_until=0
            db.session.delete(account_token)
            db.session.add(Audit(actor_id=user.id,action='password_reset'))
            db.session.commit(); session.clear()
            return redirect(url_for('login'))
        return render_template('reset.html')

    @app.cli.command('reset-admin-mfa')
    @click.option('--email',prompt=True)
    @click.confirmation_option(prompt='Reset admin MFA and revoke every session?')
    def reset_admin_mfa(email):
        user=db.session.scalar(select(User).where(User.email==email.lower().strip(),User.role=='admin'))
        if not user:
            raise click.ClickException('Admin not found')
        user.totp_encrypted=None; user.totp_counter=-1; user.session_version+=1
        db.session.add(Audit(actor_id=user.id,action='admin_mfa_reset_console'))
        db.session.commit()
        click.echo('MFA reset and sessions revoked. Re-enrollment is required in production.')


def authenticate_device():
    from . import Kiosk
    auth=request.headers.get('Authorization','')
    if not auth.startswith('Bearer ') or not 32<=len(auth[7:])<=100:
        abort(401)
    kiosk=db.session.scalar(select(Kiosk).join(User,User.id==Kiosk.owner_id).where(
        db.or_(Kiosk.device_hash==digest(auth[7:]),db.and_(Kiosk.previous_device_hash==digest(auth[7:]),Kiosk.previous_device_until>time.time())),User.disabled.is_(False)).with_for_update(of=Kiosk))
    if not kiosk:
        abort(401)
    return kiosk
