from urllib.parse import parse_qs, urlsplit
import pytest
from tools.cloudshell_support_migrate import migration_url

APP='postgresql+psycopg://inkora_app:synthetic@ep-test-pooler.region.neon.tech/inkora?sslmode=verify-full'
OWNER='postgresql://neondb_owner:synthetic%40password@ep-test.region.neon.tech/inkora?sslmode=require&channel_binding=require'


def test_owner_connection_transformed_for_existing_driver_and_verified_tls():
    result=urlsplit(migration_url(OWNER,APP))
    assert result.scheme=='postgresql+psycopg'
    assert result.password=='synthetic%40password'
    options=parse_qs(result.query)
    assert options['sslmode']==['verify-full']
    assert options['sslrootcert']==['/etc/ssl/certs/ca-certificates.crt']
    assert options['channel_binding']==['require']


@pytest.mark.parametrize('changed', [
    OWNER.replace('ep-test.region','ep-other.region'),
    OWNER.replace('ep-test.region','ep-test-pooler.region'),
    OWNER.replace('/inkora?','/other?'),
    OWNER.replace('neondb_owner:','inkora_app:'),
    OWNER.replace('neon.tech','example.test'),
    OWNER.replace('postgresql://','mysql://'),
    OWNER+'#fragment', OWNER+'\n', OWNER+'&options=unsafe',
    OWNER.replace('.tech/', '.tech:1234/'),
])
def test_other_endpoint_role_database_or_options_rejected(changed):
    with pytest.raises(ValueError):migration_url(changed,APP)


def test_existing_ca_and_disabled_ssl_settings_are_replaced_without_downgrade():
    result=migration_url(OWNER.replace('sslmode=require','sslmode=disable&sslrootcert=wrong'),APP)
    options=parse_qs(urlsplit(result).query)
    assert options['sslmode']==['verify-full'] and len(options['sslrootcert'])==1


def test_migration_secret_uses_stdin_and_separate_identity_then_is_removed(monkeypatch,capsys):
    import json
    from types import SimpleNamespace
    from tools import cloudshell_support_migrate as helper
    from tools import cloudshell_support_preflight as preflight
    deployment={'spec':{'template':{'spec':{'serviceAccountName':preflight.SA,'containers':[{
        'image':preflight.OLD_IMAGE,'env':[
            {'name':'INKORA_ENV','value':'staging'},
            {'name':'INKORA_CLOUD_PROJECT','value':preflight.PROJECT},
            {'name':'DATABASE_URL','valueFrom':{'secretKeyRef':{'name':'inkora-staging-database-url','key':'2'}}}]}]}}}}
    calls=[]
    def command(*args):
        calls.append(args)
        if args[:3]==('run','services','describe'):return json.dumps(deployment).encode()
        if args[:3]==('run','jobs','list'):return b'[]'
        if args[:3]==('secrets','versions','access'):return APP.encode()
        if args[:3]==('iam','service-accounts','list'):return json.dumps([{'email':helper.MIGRATOR}]).encode()
        if args[:3]==('run','jobs','execute'):return b'{"metadata":{"name":"migration-test"}}'
        return b''
    def run(args,**kwargs):
        assert args[:3]==['gcloud','secrets','create']
        assert '--data-file=-' in args and '--replication-policy=automatic' in args
        assert kwargs['input']==helper.migration_url(OWNER,APP).encode()
        assert not any('synthetic' in arg for arg in args)
        return SimpleNamespace(stdout=b'')
    monkeypatch.setattr(helper,'command',command)
    monkeypatch.setattr(helper.subprocess,'run',run)
    monkeypatch.setattr(helper.getpass,'getpass',lambda *a,**k: OWNER)
    monkeypatch.setattr(helper.sys,'stdin',SimpleNamespace(isatty=lambda:True))
    helper.main()
    create=next(c for c in calls if c[:3]==('run','jobs','create'))
    assert '--service-account='+helper.MIGRATOR in create
    assert '--image='+helper.NEW_IMAGE in create
    assert '--max-retries=0' in create
    assert '--set-secrets=DATABASE_URL=inkora-support-migration-' in '\n'.join(create)
    assert any(c[:3]==('run','jobs','delete') for c in calls)
    deletion=next(c for c in calls if c[:2]==('secrets','delete'))
    assert deletion[2].startswith('inkora-support-migration-')
    assert not any(c[:3]==('run','services','update') for c in calls)
    assert 'synthetic' not in capsys.readouterr().out
