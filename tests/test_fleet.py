import time
import os
from concurrent.futures import ThreadPoolExecutor
from flask_migrate import upgrade
import pytest
from werkzeug.security import generate_password_hash
from inkora import create_app, db, User, Kiosk, digest

@pytest.fixture
def app(tmp_path):
    database = os.environ.get('INKORA_TEST_DATABASE_URL', 'sqlite://')
    if database != 'sqlite://' and not database.endswith('/inkora_test'):
        raise RuntimeError('Tests may reset only the dedicated inkora_test database')
    app = create_app({'TESTING':True, 'SECRET_KEY':'x'*48, 'SQLALCHEMY_DATABASE_URI':database, 'WTF_CSRF_ENABLED':False, 'PRINT_STORAGE':str(tmp_path/'private')})
    with app.app_context():
        if database == 'sqlite://':
            db.create_all()
        else:
            upgrade()
            db.session.execute(db.text('TRUNCATE rate_bucket, audit, kiosk, "user" RESTART IDENTITY CASCADE'))
            db.session.commit()
        db.session.add_all([User(id=1,name='Admin',email='admin@example.test',role='admin',password_hash=generate_password_hash('long-password-example')),User(id=2,name='Owner A',email='a@example.test',role='owner',password_hash=generate_password_hash('long-password-example')),User(id=3,name='Owner B',email='b@example.test',role='owner',password_hash=generate_password_hash('long-password-example'))])
        db.session.flush()
        if database != 'sqlite://':
            db.session.execute(db.text("SELECT setval(pg_get_serial_sequence('\"user\"', 'id'), 3)"))
        db.session.add_all([Kiosk(id=1,owner_id=2,name='Alpha',location='A',activation_hash=digest('a'*32),activation_expires=time.time()+900),Kiosk(id=2,owner_id=3,name='Beta',location='B')])
        db.session.commit()
        if database != 'sqlite://':
            db.session.execute(db.text("SELECT setval(pg_get_serial_sequence('kiosk', 'id'), 2)"))
            db.session.commit()
    return app

def sign_in(client,email):
    return client.post('/login',data={'email':email,'password':'long-password-example'})

def test_owner_isolation_and_permissions(app):
    c=app.test_client(); assert sign_in(c,'a@example.test').status_code==302
    page=c.get('/').text
    assert 'Alpha' in page and 'Beta' not in page
    assert c.post('/owners',data={}).status_code==403
    assert c.post('/kiosks/2/activation').status_code==403

def test_owner_control_room_isolates_jobs_and_never_counts_quotes_as_revenue(app):
    from inkora.jobs import PrintJob
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from unittest.mock import patch
    fixed=datetime(2026,10,8,12,tzinfo=ZoneInfo('Asia/Kolkata')).timestamp()
    with app.app_context():
        db.session.add_all([
            PrintJob(id='a'*32,owner_id=2,kiosk_id=1,pages=2,amount_paise=750,
                     mode='bw',duplex=False,file_hash='0'*64,state='completed',created_at=fixed),
            PrintJob(id='b'*32,owner_id=3,kiosk_id=2,pages=99,amount_paise=99000,
                     mode='bw',duplex=False,file_hash='1'*64,state='completed',created_at=fixed),
            PrintJob(id='c'*32,owner_id=2,kiosk_id=1,pages=1,amount_paise=250,
                     mode='bw',duplex=False,file_hash='2'*64,state='queued',created_at=fixed),
            PrintJob(id='d'*32,owner_id=2,kiosk_id=1,pages=10,amount_paise=10000,
                     mode='bw',duplex=False,file_hash='3'*64,state='completed',created_at=fixed-86400),
        ])
        db.session.commit()
    import inkora.control_room as room
    with patch.object(room,'datetime',wraps=datetime) as clock:
        clock.now.return_value=datetime.fromtimestamp(fixed,ZoneInfo('UTC'))
        owner=app.test_client();sign_in(owner,'a@example.test')
        html=owner.get('/').text
        assert 'OWNER CONTROL ROOM' in html and '₹7.50' in html
        assert 'Beta' not in html and 'bbbbbbbbbbbb' not in html and '₹990.00' not in html
        assert 'Payment integration pending' in html and 'Simulation · unpaid' in html
        admin=app.test_client();sign_in(admin,'admin@example.test')
        html=admin.get('/').text
        assert 'FLEET OPERATIONS' in html and 'Beta' in html and '₹997.50' in html

def test_touchscreen_status_is_device_scoped_and_completed_session_allows_next_customer(app):
    from inkora.jobs import PrintJob
    c=app.test_client()
    token=c.post('/api/devices/activate',json={'code':'a'*32}).json['device_token']
    headers={'Authorization':'Bearer '+token}
    c.post('/api/devices/heartbeat',headers=headers,json={'printer_status':'ready','software_version':'test'})
    result=c.post('/api/devices/customer-sessions',headers=headers,json={})
    assert result.status_code==201
    sid=result.json['session_id']
    url='/api/devices/customer-sessions/status'
    assert c.post(url,json={'session_id':sid}).status_code==401
    assert c.post(url,headers=headers,json={'session_id':'0'*32}).status_code==404
    status=c.post(url,headers=headers,json={'session_id':sid}).json
    assert status['state']=='waiting_scan' and 'upload_url' not in status
    with app.app_context():
        db.session.add(PrintJob(id='e'*32,owner_id=2,kiosk_id=1,pages=1,amount_paise=250,
            mode='bw',duplex=False,file_hash='0'*64,state='completed',customer_session_id=sid))
        db.session.commit()
    status=c.post(url,headers=headers,json={'session_id':sid}).json
    assert status['state']=='completed' and status['amount_paise']==250
    next_session=c.post('/api/devices/customer-sessions',headers=headers,json={})
    assert next_session.status_code==201
    cancel='/api/devices/customer-sessions/cancel'
    assert c.post(cancel,headers=headers,json={'session_id':sid}).status_code==409
    next_id=next_session.json['session_id']
    assert c.post(cancel,headers=headers,json={'session_id':next_id}).status_code==200
    assert c.post(url,headers=headers,json={'session_id':next_id}).json['state']=='expired'
    with app.app_context():
        kiosk=db.session.get(Kiosk,2);kiosk.device_hash=digest('b'*64);db.session.commit()
    assert c.post(url,headers={'Authorization':'Bearer '+'b'*64},json={'session_id':sid}).status_code==404

def test_local_touchscreen_never_exposes_device_credentials_and_checks_origin(tmp_path,monkeypatch):
    import json,re
    monkeypatch.syspath_prepend(str(__import__('pathlib').Path(__file__).resolve().parents[1]/'tools'))
    import kiosk_terminal as terminal
    identity=tmp_path/'identity.json'
    credential='private-device-test-token'
    identity.write_text(json.dumps({'url':'https://example.run.app','device_token':credential}))
    identity.chmod(0o600)
    calls=[]
    def fake(base,path,data,token):
        calls.append(path)
        assert token==credential
        return {'session_id':'a'*32,'upload_url':'https://example.run.app/print/private-customer-token','expires_in':180}
    monkeypatch.setattr(terminal.agent,'post',fake)
    c=terminal.create_terminal(identity).test_client()
    page=c.get('/')
    assert 'Tap to Start' in page.text and credential not in page.text
    nonce=re.search(r'name="kiosk-csrf" content="([^"]+)"',page.text).group(1)
    assert c.post('/start').status_code==403
    assert c.post('/start',headers={'Origin':'https://evil.test','X-Kiosk-CSRF':nonce}).status_code==403
    assert c.get('/',base_url='http://evil.test').status_code==403
    result=c.post('/start',headers={'Origin':'http://localhost','X-Kiosk-CSRF':nonce})
    assert result.status_code==200 and credential not in result.text and 'upload_url' not in result.json
    assert calls==['/api/devices/customer-sessions']
    assert c.get('/qr.png').status_code==200

def test_activation_single_use_and_device_identity(app):
    c=app.test_client()
    result=c.post('/api/devices/activate',json={'code':'a'*32})
    assert result.status_code==201
    token=result.json['device_token']
    assert c.post('/api/devices/activate',json={'code':'a'*32}).status_code==409
    result=c.post('/api/devices/heartbeat',headers={'Authorization':'Bearer '+token},json={'kiosk_id':2,'printer_status':'ready','software_version':'sim-0.1'})
    assert result.status_code==200 and result.json['kiosk_id']==1
    with app.app_context():
        assert db.session.get(Kiosk,1).status=='Online'
        assert db.session.get(Kiosk,2).last_seen is None

def test_expired_code_and_invalid_token(app):
    with app.app_context():
        db.session.get(Kiosk,1).activation_expires=time.time()-1; db.session.commit()
    c=app.test_client()
    assert c.post('/api/devices/activate',json={'code':'a'*32}).status_code==409
    assert c.post('/api/devices/heartbeat',headers={'Authorization':'Bearer '+'z'*48},json={'printer_status':'ready','software_version':'test'}).status_code==401

def test_offline_and_invalid_heartbeat(app):
    with app.app_context():
        k=db.session.get(Kiosk,1); k.device_hash=digest('t'*48); k.last_seen=time.time()-91; db.session.commit()
        assert k.status=='Offline'
    assert app.test_client().post('/api/devices/heartbeat',headers={'Authorization':'Bearer '+'t'*48},json={'printer_status':'fake','software_version':'v1'}).status_code==400

def test_lockout_and_logout(app):
    c=app.test_client()
    for _ in range(5): assert c.post('/login',data={'email':'a@example.test','password':'wrong'}).status_code==401
    assert sign_in(c,'a@example.test').status_code==401
    assert sign_in(c,'admin@example.test').status_code==302
    assert c.post('/logout').status_code==302
    assert c.get('/').status_code==302

def test_csrf_and_security_headers(app):
    app.config['WTF_CSRF_ENABLED']=True
    c=app.test_client()
    assert c.post('/login',data={}).status_code==400
    assert c.get('/login').headers['Content-Security-Policy']
    assert c.post('/api/devices/activate',json={'code':'a'*32}).status_code==201

def test_admin_csrf_through_mfa_and_logout(app, monkeypatch):
    import re
    import pyotp
    from cryptography.fernet import Fernet
    from inkora.security import cipher
    app.config.update(WTF_CSRF_ENABLED=True, SECURE_DEPLOYMENT=True,
                      SESSION_COOKIE_SECURE=True, ENCRYPTION_KEY=Fernet.generate_key().decode())
    client=app.test_client()
    def token(page):
        return re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
    page=client.get('/login', base_url='https://staging.example.test')
    page=client.post('/login', base_url='https://staging.example.test',
                     headers={'Referer':'https://staging.example.test/login'},
                     data={'email':'admin@example.test','password':'long-password-example',
                           'csrf_token':token(page)}, follow_redirects=True)
    assert 'Secure your account' in page.text
    with client.session_transaction(base_url='https://staging.example.test') as state:
        encrypted=state['mfa_setup']
    with app.app_context():
        secret=cipher().decrypt(encrypted.encode()).decode()
    page=client.post('/account/mfa', base_url='https://staging.example.test',
                     headers={'Referer':'https://staging.example.test/account/mfa'},
                     data={'code':pyotp.TOTP(secret).now(),'csrf_token':token(page)},
                     follow_redirects=True)
    assert page.status_code==200 and 'Welcome, Admin' in page.text
    result=client.post('/logout', base_url='https://staging.example.test',
                       headers={'Referer':'https://staging.example.test/'},
                       data={'csrf_token':token(page)})
    assert result.status_code==302

def test_production_guards(monkeypatch):
    monkeypatch.setenv('INKORA_ENV','production')
    with pytest.raises(RuntimeError): create_app({'SECRET_KEY':'x'*48,'SQLALCHEMY_DATABASE_URI':'sqlite://'})

def test_admin_onboarding(app):
    c=app.test_client(); sign_in(c,'admin@example.test')
    assert c.post('/owners',data={'name':'New owner','email':'new@example.test','password':'long-password-example'}).status_code==302
    with app.app_context(): owner=db.session.scalar(db.select(User).where(User.email=='new@example.test')); owner_id=owner.id
    assert c.post('/kiosks',data={'owner_id':owner_id,'name':'New kiosk','location':'Bengaluru'}).status_code==302
    with app.app_context(): kiosk=db.session.scalar(db.select(Kiosk).where(Kiosk.name=='New kiosk')); kiosk_id=kiosk.id
    assert c.post(f'/kiosks/{kiosk_id}/activation').status_code==200
    assert c.get('/health/ready').status_code==200


def test_revocation_blocks_old_token_and_reactivation(app):
    c=app.test_client()
    token=c.post('/api/devices/activate',json={'code':'a'*32}).json['device_token']
    sign_in(c,'a@example.test')
    assert c.post('/kiosks/1/revoke').status_code==403
    sign_in(c,'admin@example.test')
    assert c.post('/kiosks/1/revoke').status_code==302
    assert c.post('/api/devices/heartbeat',headers={'Authorization':'Bearer '+token},json={'printer_status':'ready','software_version':'v1'}).status_code==401
    assert c.post('/kiosks/1/activation').status_code==200
    with app.app_context():
        assert db.session.get(Kiosk,1).device_hash is None
        assert db.session.get(Kiosk,1).activation_hash


def test_api_rejects_non_object_json(app):
    c=app.test_client()
    assert c.post('/api/devices/activate',json=['not','object']).status_code==400
    assert c.post('/api/devices/heartbeat',headers={'Authorization':'Bearer '+'t'*48},json=['not','object']).status_code==400


@pytest.mark.skipif(not os.environ.get('INKORA_TEST_DATABASE_URL'), reason='Requires real PostgreSQL concurrency')
def test_parallel_activation_has_one_winner(app):
    def activate(_):
        with app.test_client() as c:
            return c.post('/api/devices/activate',json={'code':'a'*32}).status_code
    with ThreadPoolExecutor(max_workers=8) as executor:
        statuses=list(executor.map(activate,range(8)))
    assert statuses.count(201)==1
    assert statuses.count(409)==7


def test_simulator_over_http(app,tmp_path):
    import threading
    from werkzeug.serving import make_server
    from tools.kiosk_simulator import post, save_identity, endpoint
    server=make_server('127.0.0.1',0,app)
    thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
    base=f'http://127.0.0.1:{server.server_port}'
    try:
        identity=post(endpoint(base),'/api/devices/activate',{'code':'a'*32})
        path=tmp_path/'device.json'
        save_identity(path,identity)
        assert path.stat().st_mode & 0o777 == 0o600
        with pytest.raises(RuntimeError): save_identity(path,identity)
        result=post(base,'/api/devices/heartbeat',{'printer_status':'paper_empty','software_version':'sim-0.2'},identity['device_token'])
        assert result['status']=='Needs attention' and result['kiosk_id']==1
    finally:
        server.shutdown(); thread.join(); server.server_close()


def pdf(pages=1):
    import io
    from pypdf import PdfWriter
    writer=PdfWriter()
    for _ in range(pages): writer.add_blank_page(width=100,height=100)
    output=io.BytesIO(); writer.write(output); output.seek(0); return output


def ready_device(app,c):
    token=c.post('/api/devices/activate',json={'code':'a'*32}).json['device_token']
    headers={'Authorization':'Bearer '+token}
    assert c.post('/api/devices/heartbeat',headers=headers,json={'printer_status':'ready','software_version':'sim'}).status_code==200
    return token,headers


def upload(c,pages=1,kiosk=1,content=None):
    return c.post('/jobs',data={'kiosk_id':kiosk,'file':(content or pdf(pages),'document.pdf'),'mode':'bw','duplex':'single'})


def test_pdf_pipeline_and_scope(app):
    from inkora.jobs import PrintJob
    import hashlib
    c=app.test_client(); _,headers=ready_device(app,c); sign_in(c,'a@example.test')
    assert upload(c,21).status_code==302
    assert upload(c,kiosk=2).status_code==404
    with app.app_context():
        job=db.session.scalar(db.select(PrintJob)); job_id=job.id
        assert job.amount_paise==4725 and job.pages==21
    assert c.get('/jobs').status_code==200
    claim=c.post('/api/devices/jobs/claim',headers=headers,json={}).json
    download=c.get(f'/api/devices/jobs/{job_id}/file',headers={**headers,'X-Claim-Token':claim['claim_token']})
    assert download.status_code==200 and hashlib.sha256(download.data).hexdigest()==claim['file_hash']
    assert c.get(f'/api/devices/jobs/{job_id}/file',headers={**headers,'X-Claim-Token':'wrong'*10}).status_code==404
    endpoint=f'/api/devices/jobs/{job_id}/transition'
    assert c.post(endpoint,headers=headers,json={'claim_token':claim['claim_token'],'state':'completed'}).status_code==409
    for state in ['printing','completed']:
        assert c.post(endpoint,headers=headers,json={'claim_token':claim['claim_token'],'state':state}).status_code==200
    assert c.post(endpoint,headers=headers,json={'claim_token':claim['claim_token'],'state':'completed'}).status_code==409
    assert c.post('/api/devices/jobs/claim',headers=headers,json={}).status_code==204
    sign_in(c,'b@example.test'); assert job_id[:10] not in c.get('/jobs').text


def test_pdf_validation_and_offline(app):
    import io
    c=app.test_client(); sign_in(c,'a@example.test')
    assert upload(c).status_code==409
    ready_device(app,c)
    assert upload(c,content=io.BytesIO(b'%PDF-garbage')).status_code==400
    assert upload(c,pages=251).status_code==400
    from pypdf import PdfWriter
    writer=PdfWriter(); writer.add_blank_page(width=100,height=100); writer.encrypt('secret')
    encrypted=io.BytesIO(); writer.write(encrypted); encrypted.seek(0)
    assert upload(c,content=encrypted).status_code==400
    assert not list(__import__('pathlib').Path(app.config['PRINT_STORAGE']).glob('*.pdf'))


def test_expired_job_never_reprints(app):
    from inkora.jobs import PrintJob
    c=app.test_client(); _,headers=ready_device(app,c); sign_in(c,'a@example.test'); upload(c)
    claim=c.post('/api/devices/jobs/claim',headers=headers,json={}).json
    with app.app_context():
        db.session.get(PrintJob,claim['job_id']).lease_until=time.time()-1; db.session.commit()
    assert c.post('/api/devices/jobs/claim',headers=headers,json={}).status_code==204
    with app.app_context(): assert db.session.get(PrintJob,claim['job_id']).state=='needs_review'
    assert c.post(f"/api/devices/jobs/{claim['job_id']}/transition",headers=headers,json={'claim_token':claim['claim_token'],'state':'printing'}).status_code==409


@pytest.mark.skipif(not os.environ.get('INKORA_TEST_DATABASE_URL'),reason='PostgreSQL job locking')
def test_parallel_job_claim_has_one_winner(app):
    c=app.test_client(); _,headers=ready_device(app,c); sign_in(c,'a@example.test'); upload(c)
    def claim(_):
        with app.test_client() as client:
            return client.post('/api/devices/jobs/claim',headers=headers,json={}).status_code
    with ThreadPoolExecutor(max_workers=8) as pool: results=list(pool.map(claim,range(8)))
    assert results.count(200)==1 and results.count(204)==7


def test_simulator_completes_pdf_over_http(app):
    import threading
    from werkzeug.serving import make_server
    from tools.kiosk_simulator import simulate_job
    from inkora.jobs import PrintJob
    c=app.test_client(); token,_=ready_device(app,c); sign_in(c,'a@example.test'); upload(c)
    server=make_server('127.0.0.1',0,app); thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
    try:
        simulate_job(f'http://127.0.0.1:{server.server_port}',token)
        with app.app_context(): assert db.session.scalar(db.select(PrintJob)).state=='completed'
    finally:
        server.shutdown(); thread.join(); server.server_close()


def test_simulated_jobs_disabled_in_production(app):
    c=app.test_client(); sign_in(c,'admin@example.test'); app.config['PRODUCTION']=True
    assert c.get('/jobs').status_code==404
    assert c.post('/api/devices/jobs/claim',json={}).status_code==404


def test_other_device_cannot_download_or_complete_job(app):
    c=app.test_client(); _,headers=ready_device(app,c); sign_in(c,'a@example.test'); upload(c)
    claim=c.post('/api/devices/jobs/claim',headers=headers,json={}).json
    with app.app_context():
        k=db.session.get(Kiosk,2); k.device_hash=digest('b'*48); k.last_seen=time.time(); k.printer_status='ready'; db.session.commit()
    other={'Authorization':'Bearer '+'b'*48,'X-Claim-Token':claim['claim_token']}
    assert c.get(f"/api/devices/jobs/{claim['job_id']}/file",headers=other).status_code==404
    assert c.post(f"/api/devices/jobs/{claim['job_id']}/transition",headers=other,json={'claim_token':claim['claim_token'],'state':'printing'}).status_code==409
    assert c.post('/api/devices/jobs/claim',headers=other,json={}).status_code==204


def test_kiosk_has_only_one_active_job(app):
    c=app.test_client(); _,headers=ready_device(app,c); sign_in(c,'a@example.test'); upload(c); upload(c)
    assert c.post('/api/devices/jobs/claim',headers=headers,json={}).status_code==200
    assert c.post('/api/devices/jobs/claim',headers=headers,json={}).status_code==204


def test_owner_invitation_and_reset_revoke_session(app):
    from inkora.security import AccountToken
    import re
    admin=app.test_client(); sign_in(admin,'admin@example.test')
    result=admin.post('/owners',data={'name':'Invited','email':'invite@example.test'})
    assert result.status_code==200 and 'No email has been sent' in result.text
    path=re.search(r'/account/reset/[A-Za-z0-9_-]+',result.text).group()
    owner=app.test_client()
    assert owner.post(path,data={'password':'a-new-password-for-owner'}).status_code==302
    assert owner.post(path,data={'password':'another-long-password'}).status_code==410
    assert sign_in(owner,'invite@example.test').status_code==401  # old test password not valid
    assert owner.post('/login',data={'email':'invite@example.test','password':'a-new-password-for-owner'}).status_code==302
    with app.app_context(): user=db.session.scalar(db.select(User).where(User.email=='invite@example.test')); uid=user.id
    link=admin.post(f'/accounts/{uid}/recovery').text
    path=re.search(r'/account/reset/[A-Za-z0-9_-]+',link).group()
    fresh=app.test_client(); assert fresh.post(path,data={'password':'a-second-owner-password'}).status_code==302
    assert owner.get('/').status_code==302
    assert admin.post(f'/accounts/{uid}/disable').status_code==302
    assert fresh.post('/login',data={'email':'invite@example.test','password':'a-second-owner-password'}).status_code==401


def test_mfa_encryption_and_replay(app,monkeypatch):
    import pyotp
    from inkora.security import cipher
    c=app.test_client(); sign_in(c,'admin@example.test'); assert c.get('/account/mfa').status_code==200
    with c.session_transaction() as state: encrypted=state['mfa_setup']
    with app.app_context(): secret=cipher().decrypt(encrypted.encode()).decode()
    assert secret not in encrypted
    now=int(time.time()); code=pyotp.TOTP(secret).at(now)
    assert c.post('/account/mfa',data={'code':code}).status_code==302
    c.post('/logout')
    assert sign_in(c,'admin@example.test').location.endswith('/account/challenge')
    assert c.post('/account/challenge',data={'code':code}).status_code==401
    monkeypatch.setattr(time,'time',lambda:now+31)
    assert c.post('/account/challenge',data={'code':pyotp.TOTP(secret).at(now+31)}).status_code==302
    assert c.get('/').status_code==200


def test_distributed_rate_limit(app):
    from inkora.security import limit
    from werkzeug.exceptions import TooManyRequests
    with app.app_context():
        for _ in range(3): limit('example',3,60)
        with pytest.raises(TooManyRequests): limit('example',3,60)


def test_customer_session_binding_idempotency_and_expiry(app):
    from inkora.customer import CustomerSession
    from inkora.jobs import PrintJob
    device=app.test_client();_,headers=ready_device(app,device)
    result=device.post('/api/devices/customer-sessions',headers=headers,json={})
    assert result.status_code==201
    path=__import__('urllib.parse',fromlist=['urlsplit']).urlsplit(result.json['upload_url']).path
    # Rendering the QR must not bind the kiosk browser as the phone browser.
    assert device.get(path+'/qr.png').status_code==200
    assert device.post('/api/devices/customer-sessions',headers=headers,json={}).status_code==409
    phone=app.test_client();assert phone.get(path).status_code==200
    stranger=app.test_client();assert stranger.get(path).status_code==403
    data=lambda:{'file':(pdf(),'sample.pdf'),'mode':'bw','duplex':'single'}
    assert phone.post(path,data=data()).status_code==200
    assert phone.post(path,data=data()).status_code==200
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count()).select_from(PrintJob))==1
        record=db.session.scalar(db.select(CustomerSession));record.expires=time.time()-86401;db.session.commit()
    assert phone.get(path).status_code==410


def test_review_renewal_and_retention(app):
    from inkora.jobs import PrintJob
    from pathlib import Path
    c=app.test_client();_,headers=ready_device(app,c);sign_in(c,'a@example.test');upload(c)
    claim=c.post('/api/devices/jobs/claim',headers=headers,json={}).json
    job_id=claim['job_id']
    assert c.post(f'/api/devices/jobs/{job_id}/renew',headers=headers,json={'claim_token':claim['claim_token']}).status_code==200
    assert c.post(f'/jobs/{job_id}/review',data={'action':'cancel','reason':'operator reviewed this'}).status_code==403
    with app.app_context(): job=db.session.get(PrintJob,job_id);job.state='needs_review';db.session.commit()
    sign_in(c,'admin@example.test')
    assert c.post(f'/jobs/{job_id}/review',data={'action':'retry','reason':'operator reviewed this'}).status_code==400
    assert c.post(f'/jobs/{job_id}/review',data={'action':'cancel','reason':'operator reviewed this'}).status_code==302
    with app.app_context(): job=db.session.get(PrintJob,job_id);job.created_at=time.time()-90000;db.session.commit()
    result=app.test_cli_runner().invoke(args=['maintenance'])
    assert result.exit_code==0, result.output
    assert not (Path(app.config['PRINT_STORAGE'])/(job_id+'.pdf')).exists()
    with app.app_context():assert db.session.get(PrintJob,job_id).deleted_at
    assert app.test_cli_runner().invoke(args=['maintenance']).exit_code==0


def test_private_cloud_storage_contract(app,tmp_path,monkeypatch):
    from inkora import storage
    calls=[]
    class Blob:
        generation=1;size=5
        def upload_from_filename(self,path,**kwargs):assert kwargs['if_generation_match']==0;calls.append('put')
        def reload(self,**kwargs):pass
        def download_as_bytes(self,**kwargs):assert kwargs['if_generation_match']==1;return b'%PDF-'
        def delete(self,**kwargs):assert kwargs['if_generation_match']==1;calls.append('delete')
    class Bucket:
        def blob(self,name):assert name=='jobs/example.pdf';return Blob()
    monkeypatch.setattr(storage,'bucket',lambda:Bucket())
    app.config['GCS_BUCKET']='isolated-test-bucket'
    sample=tmp_path/'sample.pdf';sample.write_bytes(b'%PDF-')
    with app.test_request_context():
        storage.put('example',sample)
        response=storage.response('example');assert response.mimetype=='application/pdf'
        storage.delete('example')
    assert calls==['put','delete']


def test_staging_requires_cloud_configuration(monkeypatch):
    monkeypatch.setenv('INKORA_ENV','staging')
    from cryptography.fernet import Fernet
    with pytest.raises(RuntimeError,match='private GCS'):
        create_app({'SECRET_KEY':'x'*48,'ENCRYPTION_KEY':Fernet.generate_key().decode(),'SQLALCHEMY_DATABASE_URI':'postgresql+psycopg://postgres@localhost/inkora'})


def test_device_rotation_is_retry_safe(app,monkeypatch):
    c=app.test_client();_,headers=ready_device(app,c);replacement='r'*64
    assert c.post('/api/devices/rotate',headers=headers,json={'new_token':replacement}).status_code==200
    assert c.post('/api/devices/rotate',headers=headers,json={'new_token':replacement}).status_code==200
    new={'Authorization':'Bearer '+replacement}
    assert c.post('/api/devices/heartbeat',headers=new,json={'printer_status':'ready','software_version':'v2'}).status_code==200
    with app.app_context(): kiosk=db.session.get(Kiosk,1);kiosk.previous_device_until=time.time()-1;db.session.commit()
    assert c.post('/api/devices/heartbeat',headers=headers,json={'printer_status':'ready','software_version':'v2'}).status_code==401


def test_release_signature_and_checksum_fail_closed(tmp_path):
    import json,hashlib
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat
    from cryptography.exceptions import InvalidSignature
    from tools.verify_release import verify
    key=Ed25519PrivateKey.generate();artifact=tmp_path/'agent.tar';artifact.write_bytes(b'example-release')
    manifest=tmp_path/'manifest.json';manifest.write_text(json.dumps({'version':'0.4','sha256':hashlib.sha256(artifact.read_bytes()).hexdigest(),'size':artifact.stat().st_size}))
    signature=tmp_path/'manifest.sig';signature.write_bytes(key.sign(manifest.read_bytes()))
    public=tmp_path/'trusted.pem';public.write_bytes(key.public_key().public_bytes(Encoding.PEM,PublicFormat.SubjectPublicKeyInfo))
    assert verify(manifest,signature,artifact,public)=='0.4'
    artifact.write_bytes(b'modified-release');
    with pytest.raises(ValueError):verify(manifest,signature,artifact,public)
    manifest.write_bytes(manifest.read_bytes()+b' ')
    with pytest.raises(InvalidSignature):verify(manifest,signature,artifact,public)


def test_interrupted_simulator_journal_blocks_replay(tmp_path):
    from tools.spool import Journal
    from tools.kiosk_simulator import simulate_job
    path=tmp_path/'spool.db';journal=Journal(path);journal.record({'job_id':'example','claim_token':'private'},'printing_requested');journal.close()
    reopened=Journal(path)
    simulate_job('http://127.0.0.1:1','unreachable',reopened)  # No network request or second claim occurs.
    assert reopened.pending()[0][1]=='printing_requested'
    reopened.close()


def test_outage_is_fail_closed(app,monkeypatch):
    def unavailable(*args,**kwargs):raise RuntimeError('simulated database unavailable')
    monkeypatch.setattr(db.session,'execute',unavailable)
    c=app.test_client()
    assert c.get('/health/ready').status_code==503
    assert c.get('/health/live').status_code==200
    response=c.post('/api/devices/heartbeat',json={})
    assert response.status_code==503 and response.json['error']=='Service temporarily unavailable'


def test_production_admin_mfa_gate_and_owner_disable(app):
    admin=app.test_client();sign_in(admin,'admin@example.test');app.config['PRODUCTION']=True
    assert admin.get('/').location.endswith('/account/mfa')
    app.config['PRODUCTION']=False
    c=app.test_client();_,headers=ready_device(app,c)
    assert admin.post('/accounts/2/disable').status_code==302
    assert c.post('/api/devices/heartbeat',headers=headers,json={'printer_status':'ready','software_version':'sim'}).status_code==401
    assert c.post('/api/devices/jobs/claim',headers=headers,json={}).status_code==401
    with app.app_context():assert db.session.get(Kiosk,1).status=='Disabled'


def test_reenable_requires_new_owner_password_and_device_activation(app):
    import re
    c=app.test_client();_,headers=ready_device(app,c);sign_in(c,'admin@example.test')
    assert c.post('/accounts/2/disable').status_code==302
    result=c.post('/accounts/2/enable');assert result.status_code==200
    path=re.search(r'/account/reset/[A-Za-z0-9_-]+',result.text).group()
    owner=app.test_client();assert sign_in(owner,'a@example.test').status_code==401
    assert owner.post(path,data={'password':'reactivated-owner-password'}).status_code==302
    assert owner.post('/login',data={'email':'a@example.test','password':'reactivated-owner-password'}).status_code==302
    assert c.post('/api/devices/heartbeat',headers=headers,json={'printer_status':'ready','software_version':'sim'}).status_code==401
    with app.app_context():assert db.session.get(Kiosk,1).status=='Not activated'


def test_per_kiosk_prices_preserve_existing_quotes(app):
    from inkora.jobs import PrintJob
    c=app.test_client();_,headers=ready_device(app,c);sign_in(c,'a@example.test');upload(c)
    assert c.get('/kiosks/1/settings').status_code==403
    sign_in(c,'admin@example.test')
    rates={'bw_single':'3.00','bw_duplex':'3.50','color_single':'12','color_duplex':'18','art_single':'45','art_duplex':'75'}
    assert c.post('/kiosks/1/settings',data=rates).status_code==302
    assert c.post('/kiosks/1/settings',data={**rates,'bw_single':'0.001'}).status_code==400
    assert upload(c).status_code==302
    with app.app_context():
        jobs=db.session.scalars(db.select(PrintJob).order_by(PrintJob.created_at)).all()
        assert jobs[0].amount_paise==250 and jobs[0].quote_snapshot['rate_paise']==250
        assert jobs[1].amount_paise==300 and jobs[1].pricing_version=='kiosk-v2'
        assert db.session.get(Kiosk,2).prices['bw_single']==250
    config=c.get('/api/devices/config',headers=headers)
    assert config.status_code==200 and config.json['prices_paise']['bw_single']==300
    assert config.json['kiosk_id']==1 and config.json['configuration_version']==2


def test_original_kiosk_cloud_binding_is_rejected():
    with pytest.raises(RuntimeError,match='original kiosk'):
        create_app({'SECRET_KEY':'x'*48,'GCS_BUCKET':'print-kiosk-64820.firebasestorage.app'})
