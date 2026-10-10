import json
import pytest
from tools import cloudshell_support_preflight as helper


def service():
    return {'spec':{'template':{'spec':{'serviceAccountName':helper.SA,'containers':[{
        'image':helper.OLD_IMAGE,'env':[
            {'name':'INKORA_ENV','value':'staging'},
            {'name':'INKORA_CLOUD_PROJECT','value':helper.PROJECT},
            {'name':'DATABASE_URL','valueFrom':{'secretKeyRef':{'name':'inkora-staging-database-url','key':'2'}}}]}]}}}}


def test_preflight_preserves_current_pinned_binding():
    assert helper.service_binding(service())=='inkora-staging-database-url:2'


@pytest.mark.parametrize('field,value', [('image',helper.NEW_IMAGE),('account','other'),('environment','production'),('version','latest')])
def test_unexpected_deployment_stops_before_job_creation(field,value):
    data=service();spec=data['spec']['template']['spec'];container=spec['containers'][0]
    if field=='image':container['image']=value
    elif field=='account':spec['serviceAccountName']=value
    elif field=='environment':container['env'][0]['value']=value
    else:container['env'][2]['valueFrom']['secretKeyRef']['key']=value
    with pytest.raises(ValueError):helper.service_binding(data)


def test_preflight_reuses_secret_ref_and_never_redeploys_or_reads_secret_value(monkeypatch,capsys):
    calls=[]
    def command(*args):
        calls.append(args)
        if args[:3]==('run','services','describe'):return json.dumps(service()).encode()
        if args[:3]==('run','jobs','list'):return b'[]'
        if args[:3]==('run','jobs','execute'):return b'{"metadata":{"name":"preflight-test"}}'
        return b''
    monkeypatch.setattr(helper,'command',command)
    helper.main()
    create=next(c for c in calls if c[:3]==('run','jobs','create'))
    assert '--set-secrets=DATABASE_URL=inkora-staging-database-url:2' in create
    assert '--image='+helper.NEW_IMAGE in create
    assert '--max-retries=0' in create
    assert not any(c[0]=='secrets' or c[:3]==('run','services','update') for c in calls)
    assert 'SET TRANSACTION READ ONLY' in helper.CODE
    assert 'No web deployment or migration' in capsys.readouterr().out


def test_existing_job_is_not_recreated_or_reexecuted(monkeypatch):
    calls=[]
    def command(*args):
        calls.append(args)
        if args[:3]==('run','services','describe'):return json.dumps(service()).encode()
        return json.dumps([{'metadata':{'name':helper.JOB}}]).encode()
    monkeypatch.setattr(helper,'command',command)
    with pytest.raises(SystemExit,match='already exists'):helper.main()
    assert len(calls)==2
