"""Configure Neon staging retention in authenticated Cloud Shell, without secrets in files."""
import json
import subprocess
from cloudshell_admin_setup import command, PROJECT, REGION, EXPECTED_IMAGE

JOB = 'inkora-staging-maintenance'
SCHEDULER = 'inkora-staging-maintenance'
SCHEDULER_ACCOUNT = 'inkora-scheduler@'+PROJECT+'.iam.gserviceaccount.com'


def main():
    service = json.loads(command('run', 'services', 'describe', 'inkora-staging',
                                 '--region='+REGION, '--format=json', capture=True))
    spec = service['spec']['template']['spec']
    container = spec['containers'][0]
    if container['image'] != EXPECTED_IMAGE:
        raise SystemExit('Stop: service image changed; review before configuring maintenance.')
    env = {v['name']: v for v in container.get('env', [])}
    names = ('INKORA_ENV', 'INKORA_CLOUD_PROJECT', 'INKORA_GCS_BUCKET', 'INKORA_PUBLIC_URL')
    settings = {name: env[name]['value'] for name in names}
    if settings['INKORA_ENV'] != 'staging' or settings['INKORA_CLOUD_PROJECT'] != PROJECT:
        raise SystemExit('Stop: configuration is not the intended staging deployment.')
    refs = {}
    for name in ('DATABASE_URL', 'INKORA_SESSION_SECRET', 'INKORA_ENCRYPTION_KEY'):
        refs[name] = env[name]['valueFrom']['secretKeyRef']
        if not str(refs[name]['key']).isdigit():
            raise SystemExit('Stop: secret versions are not pinned numerically.')
    jobs = json.loads(command('run', 'jobs', 'list', '--region='+REGION, '--format=json', capture=True))
    existing = [j for j in jobs if j.get('metadata', {}).get('name') == JOB]
    if existing:
        print('Maintenance job already exists; no configuration changed. Inspect it before proceeding.')
        return
    if any('|' in value for value in settings.values()):
        raise SystemExit('Stop: unsupported environment delimiter.')
    command('run', 'jobs', 'create', JOB, '--region='+REGION,
            '--image='+EXPECTED_IMAGE, '--service-account='+spec['serviceAccountName'],
            '--set-env-vars=^|^'+'|'.join(k+'='+v for k,v in settings.items()),
            '--set-secrets='+','.join(k+'='+v['name']+':'+str(v['key']) for k,v in refs.items()),
            '--command=flask', '--args=--app,inkora:create_app,maintenance,--retention-hours,24',
            '--tasks=1', '--parallelism=1', '--max-retries=0', '--task-timeout=600s',
            '--cpu=1', '--memory=1Gi')
    command('run', 'jobs', 'execute', JOB, '--region='+REGION, '--wait')
    accounts = command('iam', 'service-accounts', 'list', '--format=value(email)', capture=True).decode().splitlines()
    if SCHEDULER_ACCOUNT not in accounts:
        command('iam', 'service-accounts', 'create', 'inkora-scheduler',
                '--display-name=INKORA maintenance scheduler')
    command('run', 'jobs', 'add-iam-policy-binding', JOB, '--region='+REGION,
            '--member=serviceAccount:'+SCHEDULER_ACCOUNT, '--role=roles/run.invoker')
    scheduled = json.loads(command('scheduler', 'jobs', 'list', '--location='+REGION,
                                   '--format=json', capture=True))
    if any(j.get('name', '').rsplit('/',1)[-1] == SCHEDULER for j in scheduled):
        print('Scheduler already exists; no scheduler configuration changed. Inspect its target.')
        return
    command('scheduler', 'jobs', 'create', 'http', SCHEDULER, '--location='+REGION,
            '--schedule=*/15 * * * *', '--time-zone=Asia/Kolkata',
            '--uri=https://run.googleapis.com/v2/projects/'+PROJECT+'/locations/'+REGION+'/jobs/'+JOB+':run',
            '--http-method=POST', '--oauth-service-account-email='+SCHEDULER_ACCOUNT,
            '--oauth-token-scope=https://www.googleapis.com/auth/cloud-platform',
            '--headers=Content-Type=application/json', '--message-body={}', '--max-retry-attempts=0')
    print('Maintenance executed successfully; scheduled every 15 minutes. No Cloud SQL or web redeploy.')
    print('Verify the first scheduled execution and configure failure notifications before MVP acceptance.')


if __name__ == '__main__':
    try:
        main()
    except subprocess.CalledProcessError:
        raise SystemExit('Cloud operation failed. Stop; do not rerun creation commands blindly.')
    except (KeyError, ValueError):
        raise SystemExit('Stop: unexpected cloud metadata; configuration values withheld.')
