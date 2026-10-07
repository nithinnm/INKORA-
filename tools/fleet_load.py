"""100-device LOCAL protocol smoke test. Not a cloud capacity or cost guarantee."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import secrets
import socket
import statistics
import subprocess
import sys
import time
import urllib.request
from flask_migrate import upgrade
from werkzeug.security import generate_password_hash

# Refuse any arbitrary external database; create a distinct isolated local target.
name='inkora_load_test_'+str(int(time.time()))
subprocess.run(['docker','exec','inkora-dev-postgres','createdb','-U','postgres',name],check=True)
base=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(base))
from inkora import create_app,db,User,Kiosk,digest
url='postgresql+psycopg://postgres@127.0.0.1:5432/'+name
secret=secrets.token_urlsafe(48)
app=create_app({'SECRET_KEY':secret,'SQLALCHEMY_DATABASE_URI':url,'WTF_CSRF_ENABLED':False})
credentials=[secrets.token_urlsafe(48) for _ in range(100)]
with app.app_context():
    upgrade(directory=str(base/'migrations'))
    owner=User(name='Synthetic fleet',email='synthetic@example.test',role='owner',password_hash=generate_password_hash(secrets.token_urlsafe(48)))
    db.session.add(owner);db.session.flush()
    for index,token in enumerate(credentials):
        db.session.add(Kiosk(owner_id=owner.id,name='Synthetic '+str(index+1),location='Load test',device_hash=digest(token)))
    db.session.commit()
with socket.socket() as sock:
    sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
environ=dict(os.environ,DATABASE_URL=url,INKORA_SESSION_SECRET=secret,INKORA_ENV='development')
server=subprocess.Popen([str(base/'.venv/bin/gunicorn'),'--config',str(base/'deploy/gunicorn.conf.py'),'--bind',f'127.0.0.1:{port}','inkora:create_app()'],cwd=base,env=environ,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
endpoint=f'http://127.0.0.1:{port}'
try:
    deadline=time.monotonic()+15
    while True:
        try:
            with urllib.request.urlopen(endpoint+'/health/ready',timeout=2) as response:
                if response.status==200:break
        except Exception:
            if time.monotonic()>deadline:raise RuntimeError('Server did not become ready')
            time.sleep(0.1)
    def heartbeat(index):
        body=json.dumps({'printer_status':'ready','software_version':'synthetic-0.4'}).encode()
        req=urllib.request.Request(endpoint+'/api/devices/heartbeat',data=body,
            headers={'Authorization':'Bearer '+credentials[index],'Content-Type':'application/json'},method='POST')
        start=time.monotonic()
        with urllib.request.urlopen(req,timeout=10) as response:
            result=json.load(response)
            if response.status!=200 or result['status']!='Online':raise RuntimeError('Heartbeat failed')
        return (time.monotonic()-start)*1000
    start=time.monotonic()
    with ThreadPoolExecutor(max_workers=20) as executor:
        elapsed=list(executor.map(heartbeat,[i for _ in range(3) for i in range(100)]))
    elapsed.sort()
    with app.app_context():
        online=sum(k.status=='Online' for k in db.session.scalars(db.select(Kiosk)).all())
    report={'scope':'local two-worker HTTP protocol smoke test','devices':100,'requests':len(elapsed),'errors':0,
        'online':online,'elapsed_seconds':round(time.monotonic()-start,3),'p50_ms':round(statistics.median(elapsed),2),'p95_ms':round(elapsed[int(len(elapsed)*.95)-1],2)}
    if online!=100 or report['p95_ms']>2000:raise RuntimeError('Local smoke acceptance failed')
    print(json.dumps(report))
finally:
    server.terminate();server.wait(timeout=10)
