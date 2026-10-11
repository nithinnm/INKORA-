"""Apply only the additive support migration using a temporary, separate identity."""
import getpass
import json
import subprocess
import sys
import uuid
import warnings
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

if __package__:
    from .cloudshell_support_preflight import (PROJECT, REGION, ACCOUNT, SERVICE, NEW_IMAGE,
                                              command, service_binding)
else:
    from cloudshell_support_preflight import (PROJECT, REGION, ACCOUNT, SERVICE, NEW_IMAGE,
                                             command, service_binding)

JOB = 'inkora-staging-maintenance-migration'
MIGRATOR = 'inkora-staging-migrator@'+PROJECT+'.iam.gserviceaccount.com'

CODE = '''
import json, logging, secrets, sys
from flask_migrate import upgrade
from inkora import create_app, db
logging.disable(logging.CRITICAL)
try:
    app = create_app({'SECRET_KEY':secrets.token_urlsafe(48)})
    with app.app_context():
        role, database = db.session.execute(db.text('SELECT current_user, current_database()')).one()
        if role != 'neondb_owner' or database != 'inkora':
            raise RuntimeError('Unexpected migration identity')
        head = db.session.execute(db.text('SELECT version_num FROM public.alembic_version')).scalar_one()
        if head not in ('a4d68bedf731','e7b01486cf20'):
            raise RuntimeError('Unexpected schema revision')
        db.session.rollback()
        if head == 'a4d68bedf731':
            upgrade(revision='e7b01486cf20')
        db.session.execute(db.text('GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.service_request, public.service_event TO inkora_app'))
        db.session.execute(db.text('GRANT USAGE, SELECT ON SEQUENCE public.service_event_id_seq TO inkora_app'))
        db.session.commit()
        head = db.session.execute(db.text('SELECT version_num FROM public.alembic_version')).scalar_one()
        if head != 'e7b01486cf20':
            raise RuntimeError('Migration verification failed')
        for table in ('service_request','service_event'):
            granted = db.session.execute(db.text("SELECT has_table_privilege('inkora_app', :name, 'SELECT') AND has_table_privilege('inkora_app', :name, 'INSERT') AND has_table_privilege('inkora_app', :name, 'UPDATE') AND has_table_privilege('inkora_app', :name, 'DELETE')"), {'name':'public.'+table}).scalar_one()
            if not granted:
                raise RuntimeError('Application grants verification failed')
        granted = db.session.execute(db.text("SELECT has_sequence_privilege('inkora_app', 'public.service_event_id_seq', 'USAGE') AND has_sequence_privilege('inkora_app', 'public.service_event_id_seq', 'SELECT')")).scalar_one()
        if not granted:
            raise RuntimeError('Sequence grants verification failed')
        print(json.dumps({'migration':'passed','migration_head':head,'application_grants':'passed'}), flush=True)
except Exception as error:
    print(json.dumps({'migration':'failed','error_class':type(error).__name__}), flush=True)
    sys.exit(1)
'''


def migration_url(owner_value, app_value):
    """Require the exact staging endpoint/DB and owner role before transforming."""
    app = urlsplit(app_value)
    owner = urlsplit(owner_value)
    allowed = ('postgres', 'postgresql', 'postgresql+psycopg')
    if app.scheme != 'postgresql+psycopg' or owner.scheme not in allowed:
        raise ValueError('Unexpected PostgreSQL driver format.')
    host = owner.hostname or ''
    if (not host.endswith('.neon.tech') or '-pooler.' in host
            or host != (app.hostname or '').replace('-pooler.', '.')
            or unquote(owner.path) != '/inkora' or unquote(app.path) != '/inkora'
            or unquote(owner.username or '') != 'neondb_owner'
            or unquote(app.username or '') != 'inkora_app'
            or not owner.password or owner.fragment
            or owner.port not in (None, 5432) or app.port not in (None, 5432)
            or any(c in owner_value for c in '\r\n')):
        raise ValueError('Wrong staging endpoint, database, role or pooled owner URL.')
    options = [(key,value) for key,value in parse_qsl(owner.query, keep_blank_values=True)
               if key not in ('sslmode','sslrootcert')]
    if any(key not in ('channel_binding','connect_timeout','application_name') for key,value in options):
        raise ValueError('Unexpected connection option; stop for review.')
    options += [('sslmode','verify-full'),('sslrootcert','/etc/ssl/certs/ca-certificates.crt')]
    return urlunsplit(('postgresql+psycopg',owner.netloc,owner.path,urlencode(options),''))


def main():
    service = json.loads(command('run','services','describe',SERVICE,'--region='+REGION,'--format=json'))
    binding = service_binding(service)
    jobs = json.loads(command('run','jobs','list','--region='+REGION,'--format=json'))
    if any(j.get('metadata',{}).get('name') == JOB for j in jobs):
        raise SystemExit('Migration job already exists. Inspect its execution; do not recreate or rerun it blindly.')
    # Existing application URL is read only into process memory for endpoint
    # comparison; it is never printed, written to disk or passed as a CLI argument.
    secret, version = binding.rsplit(':',1)
    app_value = command('secrets','versions','access',version,'--secret='+secret).decode().strip()
    print('Use Neon project inkora-staging, branch production, database inkora, role neondb_owner.',flush=True)
    print('Turn connection pooling OFF. Paste the connection URL only at the hidden prompt below, never in chat.',flush=True)
    if not sys.stdin.isatty():
        raise SystemExit('A terminal is required for hidden input. Do not pipe connection details.')
    with warnings.catch_warnings():
        warnings.simplefilter('error', getpass.GetPassWarning)
        value = migration_url(getpass.getpass('Migration-owner connection URL (hidden): ').strip(), app_value)
    accounts = json.loads(command('iam','service-accounts','list','--format=json'))
    if not any(a.get('email') == MIGRATOR for a in accounts):
        command('iam','service-accounts','create','inkora-staging-migrator',
                '--display-name=INKORA staging migration job')
    temporary = 'inkora-support-migration-'+uuid.uuid4().hex[:12]
    subprocess.run(['gcloud','secrets','create',temporary,'--project='+PROJECT,
        '--account='+ACCOUNT,'--replication-policy=automatic','--data-file=-'],
        input=value.encode(),capture_output=True,check=True,timeout=60)
    print('Temporary migration secret created: '+temporary,flush=True)
    command('secrets','add-iam-policy-binding',temporary,
            '--member=serviceAccount:'+MIGRATOR,'--role=roles/secretmanager.secretAccessor')
    command('run','jobs','create',JOB,'--region='+REGION,'--image='+NEW_IMAGE,
            '--service-account='+MIGRATOR,'--set-secrets=DATABASE_URL='+temporary+':1',
            '--set-env-vars=INKORA_ENV=development','--command=python','--args=^~^-c~'+CODE,
            '--tasks=1','--parallelism=1','--max-retries=0','--task-timeout=180s',
            '--cpu=1','--memory=512Mi','--labels=inkora-purpose=support-migration')
    print('Running the new migration and narrow grants. No web redeployment.',flush=True)
    execution = json.loads(command('run','jobs','execute',JOB,'--region='+REGION,'--wait','--format=json'))
    name = execution['metadata']['name']
    print('Migration execution completed: '+name,flush=True)
    # --wait succeeds only after successful execution. Remove this helper's own
    # temporary job and privileged secret, not any application secret or table.
    command('run','jobs','delete',JOB,'--region='+REGION,'--quiet')
    command('secrets','delete',temporary,'--quiet')
    print('Temporary migration job and privileged secret removed. Runtime secrets preserved.',flush=True)
    print('Database head is now e7b01486cf20. Deploy the already-built new digest next; old /health/ready may report unavailable until then.',flush=True)


if __name__ == '__main__':
    try:
        main()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        raise SystemExit('Cloud migration operation failed; credentials and API details withheld. Stop and inspect the named job/secret; do not rerun creation blindly.')
    except (KeyError,ValueError):
        raise SystemExit('Unexpected staging configuration or connection details. Stop; values withheld.')
    except getpass.GetPassWarning:
        raise SystemExit('Hidden terminal input is unavailable. Stopped before reading credentials.')
