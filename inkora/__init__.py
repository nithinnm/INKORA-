import hashlib
import os
import secrets
import time
from datetime import timedelta
from functools import wraps

import click
from flask import Flask, abort, jsonify, redirect, render_template, request, session, url_for
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from werkzeug.exceptions import HTTPException
from werkzeug.security import check_password_hash, generate_password_hash

db = SQLAlchemy()
csrf = CSRFProtect()


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(254), unique=True, nullable=False)
    name = db.Column(db.String(120), nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(10), nullable=False)
    disabled = db.Column(db.Boolean, nullable=False, default=False)
    session_version = db.Column(db.Integer, nullable=False, default=0)
    totp_encrypted = db.Column(db.Text)
    totp_counter = db.Column(db.BigInteger, nullable=False, default=-1)
    failed_attempts = db.Column(db.Integer, nullable=False, default=0)
    locked_until = db.Column(db.Float, nullable=False, default=0)
    __table_args__ = (db.CheckConstraint("role IN ('admin','owner')"),)


class Kiosk(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    owner_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    owner = db.relationship(User, lazy='joined')
    name = db.Column(db.String(120), nullable=False)
    location = db.Column(db.String(200), nullable=False)
    activation_hash = db.Column(db.String(64), unique=True)
    activation_expires = db.Column(db.Float)
    device_hash = db.Column(db.String(64), unique=True)
    previous_device_hash = db.Column(db.String(64), unique=True)
    previous_device_until = db.Column(db.Float)
    last_seen = db.Column(db.Float)
    printer_status = db.Column(db.String(20), nullable=False, default='unknown')
    prices = db.Column(db.JSON, nullable=False, default=lambda:{'bw_single':250,'bw_duplex':300,'color_single':1000,'color_duplex':1600,'art_single':4000,'art_duplex':7000})
    configuration_version = db.Column(db.Integer, nullable=False, default=1)
    software_version = db.Column(db.String(40), nullable=False, default='unknown')

    @property
    def status(self):
        owner=self.owner
        if owner and owner.disabled:
            return 'Disabled'
        if not self.device_hash:
            return 'Not activated'
        if not self.last_seen or time.time() - self.last_seen > 90:
            return 'Offline'
        return 'Online' if self.printer_status == 'ready' else 'Needs attention'


class Audit(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    actor_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    action = db.Column(db.String(80), nullable=False)
    target_id = db.Column(db.Integer)
    timestamp = db.Column(db.Float, nullable=False, default=time.time)


def create_app(config=None):
    app = Flask(__name__)
    environment = os.environ.get('INKORA_ENV', 'development')
    if environment not in ('development','staging','production'):
        raise RuntimeError('Invalid INKORA_ENV')
    production = environment == 'production'
    secure_deployment = environment in ('staging','production')
    app.config.update(
        SECRET_KEY=os.environ.get('INKORA_SESSION_SECRET'),
        SQLALCHEMY_DATABASE_URI=os.environ.get('DATABASE_URL', 'sqlite:///fleet.db'),
        SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax',
        SESSION_COOKIE_SECURE=secure_deployment, PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
        MAX_CONTENT_LENGTH=11 * 1024 * 1024, PRODUCTION=production, SECURE_DEPLOYMENT=secure_deployment,
        GCS_BUCKET=os.environ.get('INKORA_GCS_BUCKET'), CLOUD_PROJECT=os.environ.get('INKORA_CLOUD_PROJECT'), PUBLIC_URL=os.environ.get('INKORA_PUBLIC_URL'),
        ENCRYPTION_KEY=os.environ.get('INKORA_ENCRYPTION_KEY'),
        SQLALCHEMY_ENGINE_OPTIONS={'pool_pre_ping': True, 'hide_parameters': True},
        PRINT_STORAGE=os.path.join(app.instance_path, 'print_files'),
    )
    if config:
        app.config.update(config)
    if not app.config['SECRET_KEY'] or len(app.config['SECRET_KEY']) < 32:
        raise RuntimeError('Set INKORA_SESSION_SECRET to at least 32 random characters.')
    if (app.config['PRODUCTION'] or app.config['SECURE_DEPLOYMENT']) and not app.config['SQLALCHEMY_DATABASE_URI'].startswith('postgresql'):
        raise RuntimeError('Production requires PostgreSQL, not the local SQLite database.')
    if (app.config['PRODUCTION'] or app.config['SECURE_DEPLOYMENT']) and not app.config['ENCRYPTION_KEY']:
        raise RuntimeError('Production requires INKORA_ENCRYPTION_KEY for authenticator secrets.')
    if app.config['ENCRYPTION_KEY']:
        from cryptography.fernet import Fernet
        Fernet(app.config['ENCRYPTION_KEY'])
    if app.config['CLOUD_PROJECT']=='print-kiosk-64820' or app.config['GCS_BUCKET'] in ('print-kiosk-64820.firebasestorage.app','print-kiosk-64820.appspot.com'):
        raise RuntimeError('The original kiosk cloud project/bucket must remain untouched.')
    if app.config['SECURE_DEPLOYMENT'] and (not app.config['CLOUD_PROJECT'] or not app.config['GCS_BUCKET'] or not (app.config['PUBLIC_URL'] or '').startswith('https://')):
        raise RuntimeError('Cloud staging/production requires private GCS, a separate INKORA_CLOUD_PROJECT and an HTTPS INKORA_PUBLIC_URL.')
    if app.config['SQLALCHEMY_DATABASE_URI'].startswith('postgresql'):
        app.config['SQLALCHEMY_ENGINE_OPTIONS'].update(pool_size=2,max_overflow=2,pool_timeout=10,connect_args={'connect_timeout':5})
    if app.config['PUBLIC_URL']:
        from urllib.parse import urlsplit
        public=urlsplit(app.config['PUBLIC_URL'])
        if public.scheme!='https' or not public.hostname or public.username or public.password or public.query or public.fragment or public.path not in ('','/'):
            raise RuntimeError('INKORA_PUBLIC_URL must be an HTTPS base URL without credentials, query or path.')
        app.config['PUBLIC_URL']=app.config['PUBLIC_URL'].rstrip('/')
    db.init_app(app)
    csrf.init_app(app)
    Migrate(app, db)
    dummy_hash = generate_password_hash(secrets.token_urlsafe(32))

    def login_required(admin=False):
        def decorate(fn):
            @wraps(fn)
            def wrapper(*args, **kwargs):
                user = db.session.get(User, session.get('user_id')) if session.get('user_id') else None
                if not user or user.disabled or session.get('session_version', 0) != user.session_version:
                    return redirect(url_for('login'))
                if (app.config['PRODUCTION'] or app.config['SECURE_DEPLOYMENT']) and user.role == 'admin' and request.endpoint != 'mfa' and (not user.totp_encrypted or not session.get('mfa_verified')):
                    return redirect(url_for('mfa' if not user.totp_encrypted else 'login'))
                if admin and user.role != 'admin':
                    abort(403)
                return fn(user, *args, **kwargs)
            return wrapper
        return decorate

    def field(name, maximum):
        value = request.form.get(name, '').strip()
        if not value or len(value) > maximum:
            abort(400, f'Invalid {name}')
        return value

    @app.before_request
    def block_live_simulation():
        if app.config['PRODUCTION'] and (request.path.startswith('/jobs') or request.path.startswith('/print/') or '/jobs/' in request.path or request.path.endswith('/customer-sessions')):
            abort(404)

    @app.after_request
    def headers(response):
        response.headers['Content-Security-Policy'] = "default-src 'self'; style-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'self'"
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Cache-Control'] = 'no-store'
        if app.config['PRODUCTION'] or app.config['SECURE_DEPLOYMENT']:
            response.headers['Strict-Transport-Security'] = 'max-age=31536000'
        return response

    @app.errorhandler(IntegrityError)
    def conflict(error):
        db.session.rollback()
        return render_template('error.html', message='That record already exists.'), 409

    @app.errorhandler(HTTPException)
    def http_error(error):
        if request.path.startswith('/api/'):
            return jsonify(error=error.description), error.code
        return render_template('error.html',message=error.description),error.code

    @app.errorhandler(Exception)
    def unexpected_error(error):
        db.session.rollback()
        # No request URLs, bodies, cookies, SQL parameters or credentials in logs.
        app.logger.error('Request failed: %s',type(error).__name__)
        if request.path.startswith('/api/'):
            return jsonify(error='Service temporarily unavailable'),503
        return render_template('error.html',message='Service temporarily unavailable. Please try again later.'),503

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        error = None
        if request.method == 'POST':
            email = field('email', 254).lower()
            password = field('password', 256)
            user = db.session.scalar(select(User).where(User.email == email).with_for_update())
            valid = check_password_hash(user.password_hash if user else dummy_hash, password)
            if user and user.locked_until <= time.time() and valid and not user.disabled:
                user.failed_attempts = 0
                user.locked_until = 0
                db.session.add(Audit(actor_id=user.id, action='login'))
                db.session.commit()
                session.clear()
                if user.totp_encrypted:
                    session.update(pending_user=user.id, pending_expiry=time.time()+300, pending_version=user.session_version)
                    return redirect(url_for('challenge'))
                session['user_id'] = user.id
                session['session_version'] = user.session_version
                session.permanent = True
                return redirect(url_for('dashboard'))
            if user and user.locked_until <= time.time():
                user.failed_attempts += 1
                if user.failed_attempts >= 5:
                    user.locked_until = time.time() + 900
                    user.failed_attempts = 0
                db.session.commit()
            error = 'Unable to sign in. Check your details or try again later.'
        return render_template('login.html', error=error), (401 if error else 200)

    @app.post('/logout')
    def logout():
        session.clear()
        return redirect(url_for('login'))

    @app.get('/')
    @login_required()
    def dashboard(user):
        query = select(Kiosk).order_by(Kiosk.id)
        if user.role != 'admin':
            query = query.where(Kiosk.owner_id == user.id)
        kiosks = db.session.scalars(query).all()
        owners = db.session.scalars(select(User).where(User.role == 'owner')).all() if user.role == 'admin' else []
        from .control_room import summary
        from .support import active_count
        return render_template('dashboard.html', user=user, kiosks=kiosks, owners=owners,
                               online=sum(k.status == 'Online' for k in kiosks), room=summary(user),
                               active_requests=active_count(user))

    @app.post('/owners')
    @login_required(admin=True)
    def owner_create(user):
        password = request.form.get('password') or secrets.token_urlsafe(48)
        if not 14 <= len(password) <= 256:
            abort(400, 'Use a password of at least 14 characters.')
        owner = User(name=field('name', 120), email=field('email', 254).lower(),
                     role='owner', password_hash=generate_password_hash(password))
        if '@' not in owner.email:
            abort(400, 'Invalid email')
        db.session.add(owner)
        db.session.flush()
        db.session.add(Audit(actor_id=user.id, action='owner_created', target_id=owner.id))
        db.session.commit()
        if not request.form.get('password'):
            from .security import AccountToken
            token=secrets.token_urlsafe(48)
            db.session.add(AccountToken(token_hash=digest(token),user_id=owner.id,expires=time.time()+1800))
            db.session.commit()
            return render_template('recovery.html',path='/account/reset/'+token)
        return redirect(url_for('dashboard'))

    @app.post('/kiosks')
    @login_required(admin=True)
    def kiosk_create(user):
        try:
            owner = db.session.get(User, int(request.form.get('owner_id', '')))
        except ValueError:
            abort(400)
        if not owner or owner.role != 'owner':
            abort(400, 'Select a valid owner')
        kiosk = Kiosk(owner_id=owner.id, name=field('name', 120), location=field('location', 200))
        db.session.add(kiosk)
        db.session.flush()
        db.session.add(Audit(actor_id=user.id, action='kiosk_created', target_id=kiosk.id))
        db.session.commit()
        return redirect(url_for('dashboard'))

    @app.post('/kiosks/<int:kiosk_id>/activation')
    @login_required(admin=True)
    def activation_create(user, kiosk_id):
        kiosk = db.get_or_404(Kiosk, kiosk_id)
        if kiosk.device_hash:
            abort(409, 'Already activated')
        code = secrets.token_urlsafe(24)
        kiosk.activation_hash = digest(code)
        kiosk.activation_expires = time.time() + 900
        db.session.add(Audit(actor_id=user.id, action='activation_issued', target_id=kiosk.id))
        db.session.commit()
        return render_template('activation.html', code=code, kiosk=kiosk)

    @app.post('/kiosks/<int:kiosk_id>/revoke')
    @login_required(admin=True)
    def revoke(user, kiosk_id):
        kiosk = db.get_or_404(Kiosk, kiosk_id)
        kiosk.device_hash = None
        kiosk.previous_device_hash = None
        kiosk.previous_device_until = None
        kiosk.activation_hash = None
        kiosk.activation_expires = None
        kiosk.last_seen = None
        kiosk.printer_status = 'unknown'
        db.session.add(Audit(actor_id=user.id, action='device_revoked', target_id=kiosk.id))
        db.session.commit()
        return redirect(url_for('dashboard'))

    @app.post('/api/devices/activate')
    @csrf.exempt
    def activate():
        body = request.get_json(silent=True) or {}
        if not isinstance(body, dict):
            abort(400)
        code = body.get('code')
        if not isinstance(code, str) or not 20 <= len(code) <= 100:
            abort(400)
        token = secrets.token_urlsafe(48)
        result = db.session.execute(update(Kiosk).where(
            Kiosk.activation_hash == digest(code), Kiosk.activation_expires > time.time(),
            Kiosk.device_hash.is_(None),
        ).values(device_hash=digest(token), activation_hash=None, activation_expires=None).returning(Kiosk.id))
        kiosk_id = result.scalar_one_or_none()
        if kiosk_id is None:
            db.session.rollback()
            return jsonify(error='Invalid, expired, or consumed activation code'), 409
        db.session.add(Audit(action='device_activated', target_id=kiosk_id))
        db.session.commit()
        return jsonify(kiosk_id=kiosk_id, device_token=token), 201

    @app.post('/api/devices/heartbeat')
    @csrf.exempt
    def heartbeat():
        auth = request.headers.get('Authorization', '')
        if not auth.startswith('Bearer ') or not 32 <= len(auth[7:]) <= 100:
            abort(401)
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            abort(400)
        status = body.get('printer_status')
        version = body.get('software_version')
        if status not in ('ready', 'busy', 'error', 'paper_empty', 'unknown') or not isinstance(version, str) or not 1 <= len(version) <= 40:
            abort(400)
        kiosk_id = db.session.execute(update(Kiosk).where(
            db.or_(Kiosk.device_hash == digest(auth[7:]), db.and_(Kiosk.previous_device_hash == digest(auth[7:]), Kiosk.previous_device_until > time.time())),
            Kiosk.owner_id.in_(select(User.id).where(User.disabled.is_(False))),
        ).values(last_seen=time.time(), printer_status=status, software_version=version).returning(Kiosk.id)).scalar_one_or_none()
        if kiosk_id is None:
            db.session.rollback()
            abort(401)
        db.session.commit()
        return jsonify(kiosk_id=kiosk_id, status='Online' if status == 'ready' else 'Needs attention')

    @app.get('/health/live')
    def live():
        return jsonify(status='alive')

    @app.get('/health/ready')
    def ready():
        try:
            db.session.execute(select(User).limit(1))
            from .jobs import PrintJob
            db.session.execute(select(PrintJob).limit(1))
            if app.config['SECURE_DEPLOYMENT']:
                from pathlib import Path
                from alembic.script import ScriptDirectory
                head=ScriptDirectory(str(Path(__file__).resolve().parent.parent/'migrations')).get_current_head()
                actual=db.session.execute(db.text('SELECT version_num FROM alembic_version')).scalar_one()
                if actual!=head:
                    raise RuntimeError('Migrations are not current')
        except Exception:
            db.session.rollback()
            return jsonify(status='unavailable'), 503
        return jsonify(status='ready')

    @app.cli.command('init-db')
    def init_db():
        """Initialize a NEW development database; not a production migration tool."""
        if app.config['PRODUCTION'] or app.config['SECURE_DEPLOYMENT']:
            raise click.ClickException('Production migrations are required before deployment.')
        db.create_all()

    @app.cli.command('create-admin')
    @click.option('--email', prompt=True)
    @click.option('--name', prompt=True)
    @click.password_option()
    def create_admin(email, name, password):
        if len(password) < 14 or '@' not in email or len(email) > 254 or not 1 <= len(name) <= 120:
            raise click.ClickException('Use a valid email, name, and password of at least 14 characters.')
        db.session.add(User(email=email.lower().strip(), name=name, role='admin', password_hash=generate_password_hash(password)))
        db.session.commit()
        click.echo('Admin created. No default account or password is installed.')

    from .pricing import register_pricing
    register_pricing(app, login_required)
    from .security import register_security
    register_security(app, login_required)
    from .jobs import register_jobs
    register_jobs(app, login_required)
    from .customer import register_customer
    register_customer(app)
    from .devices import register_devices
    register_devices(app)
    from .maintenance import register_maintenance
    register_maintenance(app)
    from .support import register_support
    register_support(app, login_required)
    return app
