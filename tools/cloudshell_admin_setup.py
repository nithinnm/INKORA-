"""Run in authenticated Cloud Shell; never prints credentials or writes them to files."""
import getpass
import json
from pathlib import Path
import subprocess
import uuid

PROJECT = 'inkora-510915'
REGION = 'asia-south1'
JOB = 'inkora-staging-admin-bootstrap'
EXPECTED_IMAGE = 'asia-south1-docker.pkg.dev/inkora-510915/inkora/web@sha256:f1f85033dd99cb7c6096c970d4987c9f779aff45a72f0b6eb92a936095208cef'


def command(*args, capture=False, data=None):
    result = subprocess.run(['gcloud', *args, '--project='+PROJECT],
                            input=data, capture_output=capture, check=True)
    return result.stdout if capture else None


def main():
    service = json.loads(command('run', 'services', 'describe', 'inkora-staging',
                                 '--region='+REGION, '--format=json', capture=True))
    spec = service['spec']['template']['spec']
    container = spec['containers'][0]
    if container['image'] != EXPECTED_IMAGE:
        raise SystemExit('Stop: deployed image differs from the agreed immutable image.')
    account = spec['serviceAccountName']
    if account != 'inkora-staging@inkora-510915.iam.gserviceaccount.com':
        raise SystemExit('Stop: unexpected runtime identity.')
    env = {entry['name']: entry for entry in container.get('env', [])}
    settings = {}
    for name in ('INKORA_ENV', 'INKORA_CLOUD_PROJECT', 'INKORA_GCS_BUCKET', 'INKORA_PUBLIC_URL'):
        settings[name] = env[name]['value']
    if settings['INKORA_ENV'] != 'staging' or settings['INKORA_CLOUD_PROJECT'] != PROJECT:
        raise SystemExit('Stop: service is not the intended staging configuration.')
    bindings = []
    for name in ('DATABASE_URL', 'INKORA_SESSION_SECRET', 'INKORA_ENCRYPTION_KEY'):
        ref = env[name]['valueFrom']['secretKeyRef']
        if not str(ref['key']).isdigit():
            raise SystemExit('Stop: application secret versions must be pinned numerically.')
        bindings.append(name+'='+ref['name']+':'+str(ref['key']))
    with open('/dev/tty', 'r') as terminal_in, open('/dev/tty', 'w') as terminal_out:
        terminal_out.write('Admin email: ')
        terminal_out.flush()
        email = terminal_in.readline().strip()
        password = getpass.getpass('New admin password (at least 14 characters): ', stream=terminal_out)
        confirmation = getpass.getpass('Confirm password: ', stream=terminal_out)
    if ('@' not in email or len(email) > 254 or any(c in email for c in '\r\n|')
            or len(password) < 14 or password != confirmation or any(c in password for c in '\r\n')):
        raise SystemExit('Stop: invalid email/password or confirmation mismatch.')
    secret = 'inkora-bootstrap-'+uuid.uuid4().hex[:12]
    command('secrets', 'create', secret, '--replication-policy=automatic',
            '--data-file=-', data=password.encode())
    print('Temporary secret name (not its value): '+secret, flush=True)
    command('secrets', 'add-iam-policy-binding', secret,
            '--member=serviceAccount:'+account, '--role=roles/secretmanager.secretAccessor')
    settings.update(INKORA_BOOTSTRAP_EMAIL=email, INKORA_BOOTSTRAP_NAME='INKORA Admin')
    if any('|' in value for value in settings.values()):
        raise SystemExit('Stop: unsupported environment-value delimiter.')
    bindings.append('INKORA_BOOTSTRAP_PASSWORD='+secret+':1')
    code = Path(__file__).with_name('bootstrap_admin.py').read_text()
    command('run', 'jobs', 'create', JOB, '--region='+REGION,
            '--image='+EXPECTED_IMAGE, '--service-account='+account,
            '--set-env-vars=^|^'+'|'.join(k+'='+v for k,v in settings.items()),
            '--set-secrets='+','.join(bindings), '--command=python', '--args=^~^-c~'+code,
            '--tasks=1', '--parallelism=1', '--max-retries=0', '--task-timeout=120s',
            '--cpu=1', '--memory=512Mi')
    command('run', 'jobs', 'execute', JOB, '--region='+REGION, '--wait')
    print('Bootstrap execution succeeded. Next: verify the admin record and enroll MFA.', flush=True)
    print('Temporary job/secret are retained for inspection; remove them after verification.', flush=True)


if __name__ == '__main__':
    try:
        main()
    except subprocess.CalledProcessError:
        raise SystemExit('Cloud operation failed. Stop and share the gcloud error, without credentials.')
    except (KeyError, ValueError):
        raise SystemExit('Stop: service configuration format was unexpected; values withheld.')
