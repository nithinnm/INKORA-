"""Restore drill for the isolated LOCAL synthetic test database only."""
import json
from pathlib import Path
import os
import subprocess
import time

container='inkora-dev-postgres'
source='inkora_test'
target='inkora_restore_test_'+str(int(time.time()))
backup=Path('.build/'+target+'.dump')
backup.parent.mkdir(exist_ok=True)


def command(*args,input=None):
    return subprocess.run(['docker','exec','-i',container,*args],input=input,capture_output=True,check=True).stdout


exists=command('psql','-U','postgres','-d','postgres','-Atc',f"SELECT 1 FROM pg_database WHERE datname='{target}'").strip()
if exists:
    raise SystemExit('Restore target already exists; preserve or independently inspect it before another drill.')
command('createdb','-U','postgres',target)
fd=os.open(backup,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
with os.fdopen(fd,'wb') as output:
    result=subprocess.run(['docker','exec',container,'pg_dump','-U','postgres','-Fc',source],stdout=output,stderr=subprocess.PIPE,check=True)
command('pg_restore','-U','postgres','-d',target,'--no-owner','--no-acl',input=backup.read_bytes())
tables=('user','kiosk','print_job','audit','account_token','customer_session',
        'rate_bucket','job_review','service_request','service_event','alembic_version')
parts=[]
for table in tables:
    # All identifiers are fixed above. Compare canonical row fingerprints as
    # well as counts, without printing account records or password hashes.
    parts.append("'"+table+"', (SELECT json_build_object('count', count(*), 'fingerprint', "
                 "md5(coalesce(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text)::text,'[]'))) "
                 'FROM "'+table+'" t)')
query='SELECT json_build_object('+','.join(parts)+');'
before=json.loads(command('psql','-U','postgres','-d',source,'-Atc',query))
after=json.loads(command('psql','-U','postgres','-d',target,'-Atc',query))
if before!=after or before['user']['count']==0:
    raise SystemExit('Restore verification failed')
print(json.dumps({'result':'restored synthetic database',
                  'tables_verified':len(tables),
                  'row_counts':{table:data['count'] for table,data in after.items()},
                  'row_fingerprints_match':True,
                  'scope':'local synthetic data only; no Neon recovery tested'}))
