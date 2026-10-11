import json
import logging
from types import SimpleNamespace
import pytest
from test_fleet import app
from inkora import db,User,Kiosk
from inkora.security import AccountToken
from tools.cloudshell_admin_password_reset import CODE
from werkzeug.security import check_password_hash


def run_recovery(app,monkeypatch,email='admin@example.test'):
    import inkora
    monkeypatch.setattr(inkora,'create_app',lambda:app)
    monkeypatch.setenv('INKORA_RECOVERY_EMAIL',email)
    monkeypatch.setenv('INKORA_RECOVERY_PASSWORD','new-hidden-admin-password')
    execute=db.session.execute
    def checked(statement,*args,**kwargs):
        if str(statement)=='SELECT current_user':return SimpleNamespace(scalar_one=lambda:'inkora_app')
        return execute(statement,*args,**kwargs)
    monkeypatch.setattr(db.session,'execute',checked)
    level=logging.root.manager.disable
    try:exec(CODE,{'__name__':'test_worker'})
    finally:logging.disable(level)


def test_existing_admin_reset_preserves_mfa_owner_and_kiosk(app,monkeypatch,capsys):
    with app.app_context():
        admin=db.session.get(User,1);admin.totp_encrypted='preserved-ciphertext';admin.totp_counter=123
        admin.failed_attempts=3;admin.locked_until=100000;admin.session_version=4
        owner_hash=db.session.get(User,2).password_hash
        db.session.add(AccountToken(token_hash='a'*64,user_id=1,expires=9999999999))
        db.session.commit()
    run_recovery(app,monkeypatch)
    output=capsys.readouterr().out
    assert json.loads(output)['mfa']=='preserved' and 'new-hidden-admin-password' not in output
    with app.app_context():
        admin=db.session.get(User,1)
        assert check_password_hash(admin.password_hash,'new-hidden-admin-password')
        assert admin.totp_encrypted=='preserved-ciphertext' and admin.totp_counter==123
        assert admin.session_version==5 and admin.locked_until==0 and admin.failed_attempts==0
        assert db.session.get(User,2).password_hash==owner_hash
        assert db.session.scalar(db.select(db.func.count(Kiosk.id)))==2
        assert db.session.scalar(db.select(db.func.count(AccountToken.token_hash)))==0


@pytest.mark.parametrize('email',['a@example.test','missing@example.test'])
def test_recovery_never_creates_admin_or_changes_owner(app,monkeypatch,email,capsys):
    with app.app_context():original=db.session.get(User,2).password_hash
    with pytest.raises(SystemExit):run_recovery(app,monkeypatch,email)
    assert json.loads(capsys.readouterr().out)['admin_password']=='failed'
    with app.app_context():
        assert db.session.get(User,2).password_hash==original
        assert db.session.scalar(db.select(db.func.count(User.id)))==3


def test_disabled_admin_is_not_reenabled(app,monkeypatch,capsys):
    with app.app_context():
        admin=db.session.get(User,1);admin.disabled=True;original=admin.password_hash;db.session.commit()
    with pytest.raises(SystemExit):run_recovery(app,monkeypatch)
    assert json.loads(capsys.readouterr().out)['admin_password']=='failed'
    with app.app_context():
        admin=db.session.get(User,1)
        assert admin.disabled and admin.password_hash==original
