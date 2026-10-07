"""One-off Cloud Run job; password supplied only through a secret binding."""
import os
import sys

from click.testing import CliRunner
from sqlalchemy import select
from inkora import create_app, db, User


def main():
    email = os.environ.get('INKORA_BOOTSTRAP_EMAIL', '').strip()
    name = os.environ.get('INKORA_BOOTSTRAP_NAME', '').strip()
    password = os.environ.get('INKORA_BOOTSTRAP_PASSWORD', '')
    if ('@' not in email or len(email) > 254 or not 1 <= len(name) <= 120
            or len(password) < 14 or any(c in email + name + password for c in '\r\n')):
        print('Invalid bootstrap inputs; values withheld.', flush=True)
        return 1
    app = create_app()
    with app.app_context():
        if db.session.execute(select(User.id).where(User.role == 'admin').limit(1)).first():
            print('An admin already exists; no account was changed.', flush=True)
            return 1
        if db.session.execute(select(User.id).where(User.email == email.lower()).limit(1)).first():
            print('Email already registered; no account was changed.', flush=True)
            return 1
        result = CliRunner().invoke(app.cli.commands['create-admin'],
                                    ['--email', email, '--name', name],
                                    input=password + '\n' + password + '\n')
        if result.exit_code:
            print('Admin creation failed; error class: ' + type(result.exception).__name__, flush=True)
            return 1
    print('Admin created. Enroll MFA at first login.', flush=True)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        print('Bootstrap failed; error class: ' + type(exc).__name__, flush=True)
        sys.exit(1)
