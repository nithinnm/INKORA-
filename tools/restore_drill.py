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
query='''SELECT json_build_object('users',(SELECT count(*) FROM "user"),'kiosks',(SELECT count(*) FROM kiosk),'jobs',(SELECT count(*) FROM print_job),'audit',(SELECT count(*) FROM audit),'revision',(SELECT version_num FROM alembic_version));'''
before=json.loads(command('psql','-U','postgres','-d',source,'-Atc',query))
after=json.loads(command('psql','-U','postgres','-d',target,'-Atc',query))
if before!=after or before['users']==0:
    raise SystemExit('Restore verification failed')
print(json.dumps({'result':'restored synthetic database','source_counts':before,'restored_counts':after}))
