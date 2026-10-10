"""Inspect staging schema using a temporary read-only Cloud Run job; no migration."""
import json
import subprocess
import shlex

PROJECT = 'inkora-510915'
REGION = 'asia-south1'
SERVICE = 'inkora-staging'
JOB = 'inkora-staging-maintenance-preflight'
ACCOUNT = 'nithingowdaniluvani@gmail.com'
SA = 'inkora-staging@'+PROJECT+'.iam.gserviceaccount.com'
OLD_IMAGE = 'asia-south1-docker.pkg.dev/'+PROJECT+'/inkora/web@sha256:0caf0f4ac3cd2511af9129ad92881365292819f1ab3e0a17ebdfacab89fb048f'
NEW_IMAGE = 'asia-south1-docker.pkg.dev/'+PROJECT+'/inkora/web@sha256:bc7b8fcb810bf2173333d59e8833df8cbf250338c083d9249c2aae2390f83295'

CODE = '''
import json, secrets, sys
from inkora import create_app, db
try:
    app = create_app({'SECRET_KEY': secrets.token_urlsafe(48)})
    with app.app_context():
        db.session.execute(db.text('SET TRANSACTION READ ONLY'))
        role, database = db.session.execute(db.text('SELECT current_user, current_database()')).one()
        if role != 'inkora_app' or database != 'inkora':
            raise RuntimeError('Unexpected database identity')
        head = db.session.execute(db.text('SELECT version_num FROM public.alembic_version')).scalar_one()
        if head not in ('a4d68bedf731', 'e7b01486cf20'):
            raise RuntimeError('Unexpected migration head')
        result = {'migration_head': head, 'application_role': role, 'database': database}
        for table in ('service_request', 'service_event'):
            exists = db.session.execute(db.text('SELECT to_regclass(:name) IS NOT NULL'), {'name':'public.'+table}).scalar_one()
            permissions = False
            if exists:
                permissions = db.session.execute(db.text("SELECT has_table_privilege(current_user, :name, 'SELECT') AND has_table_privilege(current_user, :name, 'INSERT') AND has_table_privilege(current_user, :name, 'UPDATE') AND has_table_privilege(current_user, :name, 'DELETE')"), {'name':'public.'+table}).scalar_one()
            result[table] = {'exists':exists, 'dml_permissions':permissions}
        result['event_sequence_permissions'] = False
        sequence = db.session.execute(db.text("SELECT to_regclass('public.service_event_id_seq') IS NOT NULL")).scalar_one()
        if sequence:
            result['event_sequence_permissions'] = db.session.execute(db.text("SELECT has_sequence_privilege(current_user, 'public.service_event_id_seq', 'USAGE') AND has_sequence_privilege(current_user, 'public.service_event_id_seq', 'SELECT')")).scalar_one()
        print(json.dumps(result), flush=True)
except Exception as error:
    print(json.dumps({'preflight':'failed','error_class':type(error).__name__}), flush=True)
    sys.exit(1)
'''


def command(*args):
    result = subprocess.run(['gcloud', *args, '--project='+PROJECT, '--account='+ACCOUNT],
                            capture_output=True, check=True, timeout=240)
    return result.stdout


def service_binding(service):
    spec = service['spec']['template']['spec']
    container = spec['containers'][0]
    if container['image'] != OLD_IMAGE or spec['serviceAccountName'] != SA:
        raise ValueError('Unexpected deployment. Inspect before proceeding.')
    env = {entry['name']:entry for entry in container.get('env', [])}
    if env['INKORA_ENV'].get('value') != 'staging' or env['INKORA_CLOUD_PROJECT'].get('value') != PROJECT:
        raise ValueError('Unexpected staging environment.')
    ref = env['DATABASE_URL']['valueFrom']['secretKeyRef']
    if ref['name'] != 'inkora-staging-database-url' or not str(ref['key']).isdigit():
        raise ValueError('Unexpected database binding.')
    return ref['name']+':'+str(ref['key'])


def main():
    service = json.loads(command('run','services','describe',SERVICE,'--region='+REGION,'--format=json'))
    binding = service_binding(service)
    jobs = json.loads(command('run','jobs','list','--region='+REGION,'--format=json'))
    if any(j.get('metadata',{}).get('name') == JOB for j in jobs):
        raise SystemExit('Preflight job already exists. Inspect its execution; do not recreate it blindly.')
    command('run','jobs','create',JOB,'--region='+REGION,'--image='+NEW_IMAGE,
            '--service-account='+SA,'--set-secrets=DATABASE_URL='+binding,
            '--set-env-vars=INKORA_ENV=development', '--command=python','--args=^~^-c~'+CODE,
            '--tasks=1','--parallelism=1','--max-retries=0','--task-timeout=120s',
            '--cpu=1','--memory=512Mi','--labels=inkora-purpose=support-preflight')
    print('Read-only schema preflight job created. No web deployment or migration.',flush=True)
    execution = json.loads(command('run','jobs','execute',JOB,'--region='+REGION,'--wait','--format=json'))
    name = execution['metadata']['name']
    print('Preflight execution completed: '+name,flush=True)
    # Logs can arrive after execution finishes. Let the user retrieve them in one
    # precise command rather than polling or rerunning an existing operation.
    print('Read stdout for this execution with:')
    print('gcloud logging read '+shlex.quote('resource.type="cloud_run_job" AND resource.labels.job_name="'+JOB+'" AND labels."run.googleapis.com/execution_name"="'+name+'" AND log_id("run.googleapis.com/stdout")')+' --project='+PROJECT+' --freshness=1h --limit=10 --format="value(textPayload)"')


if __name__ == '__main__':
    try:
        main()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        raise SystemExit('Cloud preflight operation failed; details withheld. Inspect the temporary job, not creation commands.')
    except (KeyError,ValueError):
        raise SystemExit('Unexpected service or execution metadata. Stop; values withheld.')
