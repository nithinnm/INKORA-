"""Recover an existing staging admin password through a temporary Cloud Run job."""
import getpass
import json
import subprocess
import sys
import uuid
import warnings

if __package__:
    from .cloudshell_support_preflight import PROJECT, REGION, ACCOUNT, SERVICE, NEW_IMAGE, SA, command
else:
    from cloudshell_support_preflight import PROJECT, REGION, ACCOUNT, SERVICE, NEW_IMAGE, SA, command

JOB = 'inkora-staging-admin-password-recovery'
CODE = '''
import json, logging, os, sys
from sqlalchemy import select
from werkzeug.security import generate_password_hash, check_password_hash
from inkora import create_app, db, User, Audit
from inkora.security import AccountToken
logging.disable(logging.CRITICAL)
try:
    password = os.environ.get('INKORA_RECOVERY_PASSWORD','')
    email = os.environ.get('INKORA_RECOVERY_EMAIL','').lower().strip()
    if not 14 <= len(password) <= 256 or any(c in password for c in '\\r\\n'):
        raise ValueError('Invalid password')
    app = create_app()
    with app.app_context():
        if db.session.execute(db.text('SELECT current_user')).scalar_one() != 'inkora_app':
            raise RuntimeError('Unexpected application role')
        user = db.session.scalar(select(User).where(User.email==email,User.role=='admin').with_for_update())
        if not user or user.disabled:
            raise RuntimeError('Enabled admin not found')
        user.password_hash = generate_password_hash(password)
        user.session_version += 1
        user.failed_attempts = 0
        user.locked_until = 0
        db.session.execute(db.delete(AccountToken).where(AccountToken.user_id==user.id))
        db.session.add(Audit(actor_id=user.id,action='admin_password_reset_console'))
        if not check_password_hash(user.password_hash,password):
            raise RuntimeError('Password hash verification failed')
        db.session.commit()
        print(json.dumps({'admin_password':'reset','sessions':'revoked','mfa':'preserved'}),flush=True)
except Exception as error:
    print(json.dumps({'admin_password':'failed','error_class':type(error).__name__}),flush=True)
    sys.exit(1)
'''


def configuration(service):
    spec=service['spec']['template']['spec']
    container=spec['containers'][0]
    if container['image']!=NEW_IMAGE or spec['serviceAccountName']!=SA:
        raise ValueError('Unexpected deployment.')
    env={entry['name']:entry for entry in container.get('env',[])}
    settings={name:env[name]['value'] for name in ('INKORA_ENV','INKORA_CLOUD_PROJECT','INKORA_GCS_BUCKET','INKORA_PUBLIC_URL')}
    if settings['INKORA_ENV']!='staging' or settings['INKORA_CLOUD_PROJECT']!=PROJECT:
        raise ValueError('Wrong staging environment.')
    refs=[]
    expected={'DATABASE_URL':'inkora-staging-database-url','INKORA_SESSION_SECRET':'inkora-staging-session-secret','INKORA_ENCRYPTION_KEY':'inkora-staging-encryption-key'}
    for name,secret in expected.items():
        ref=env[name]['valueFrom']['secretKeyRef']
        if ref['name']!=secret or not str(ref['key']).isdigit():
            raise ValueError('Unexpected secret binding.')
        refs.append(name+'='+ref['name']+':'+str(ref['key']))
    return settings,refs


def main():
    settings,refs=configuration(json.loads(command('run','services','describe',SERVICE,'--region='+REGION,'--format=json')))
    jobs=json.loads(command('run','jobs','list','--region='+REGION,'--format=json'))
    if any(j.get('metadata',{}).get('name')==JOB for j in jobs):
        raise SystemExit('Recovery job already exists. Inspect it rather than resetting twice.')
    if not sys.stdin.isatty():
        raise SystemExit('Hidden input requires a terminal; do not pipe passwords.')
    email=input('Existing admin email: ').strip().lower()
    if '@' not in email or len(email)>254 or any(c in email for c in '|\r\n'):
        raise ValueError('Invalid email.')
    with warnings.catch_warnings():
        warnings.simplefilter('error',getpass.GetPassWarning)
        password=getpass.getpass('New admin password (14–256 characters, hidden): ')
        confirmation=getpass.getpass('Confirm new password (hidden): ')
    if password!=confirmation or not 14<=len(password)<=256 or any(c in password for c in '\r\n'):
        raise ValueError('Invalid password or confirmation mismatch.')
    secret='inkora-admin-recovery-'+uuid.uuid4().hex[:12]
    subprocess.run(['gcloud','secrets','create',secret,'--project='+PROJECT,'--account='+ACCOUNT,
        '--replication-policy=automatic','--data-file=-'],input=password.encode(),capture_output=True,check=True,timeout=60)
    print('Temporary recovery secret created: '+secret,flush=True)
    command('secrets','add-iam-policy-binding',secret,'--member=serviceAccount:'+SA,'--role=roles/secretmanager.secretAccessor')
    settings['INKORA_RECOVERY_EMAIL']=email
    if any('|' in value for value in settings.values()):
        raise ValueError('Unexpected environment delimiter.')
    refs.append('INKORA_RECOVERY_PASSWORD='+secret+':1')
    command('run','jobs','create',JOB,'--region='+REGION,'--image='+NEW_IMAGE,
        '--service-account='+SA,'--set-env-vars=^|^'+'|'.join(k+'='+v for k,v in settings.items()),
        '--set-secrets='+','.join(refs),'--command=python','--args=^~^-c~'+CODE,
        '--tasks=1','--parallelism=1','--max-retries=0','--task-timeout=120s','--cpu=1','--memory=512Mi')
    print('Resetting only the existing admin password. MFA and kiosk data are preserved.',flush=True)
    execution=json.loads(command('run','jobs','execute',JOB,'--region='+REGION,'--wait','--format=json'))
    print('Recovery execution succeeded: '+execution['metadata']['name'],flush=True)
    command('run','jobs','delete',JOB,'--region='+REGION,'--quiet')
    command('secrets','delete',secret,'--quiet')
    print('Temporary recovery job and secret removed. Sign in using the new password and your existing authenticator.',flush=True)


if __name__=='__main__':
    try:
        main()
    except (subprocess.CalledProcessError,subprocess.TimeoutExpired):
        raise SystemExit('Cloud recovery operation failed. Stop and inspect the named job/secret; credentials withheld.')
    except (KeyError,ValueError):
        raise SystemExit('Unexpected configuration or invalid input. No values printed; stop for diagnosis.')
    except getpass.GetPassWarning:
        raise SystemExit('Hidden input unavailable. Stopped before accepting a password.')
