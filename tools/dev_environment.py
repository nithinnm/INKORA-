"""Prepare private LOCAL-only bindings without overwriting an existing file."""
import os
from pathlib import Path
import secrets
from cryptography.fernet import Fernet


def prepare(path):
    path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists():
        if path.is_symlink() or path.stat().st_mode & 0o077:
            raise RuntimeError('Local binding file must be private and must not be a symlink.')
        return False
    values={}
    if not os.environ.get('INKORA_SESSION_SECRET'):
        values['INKORA_SESSION_SECRET']=secrets.token_urlsafe(48)
    if not os.environ.get('INKORA_ENCRYPTION_KEY'):
        values['INKORA_ENCRYPTION_KEY']=Fernet.generate_key().decode()
    # Existing injected bindings remain in the process; their values are not copied.
    descriptor=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(descriptor,'w') as file:
        for name,value in values.items():file.write(name+'='+value+'\n')
    return True


if __name__=='__main__':
    created=prepare(Path('.build/compose.env'))
    print('Private local bindings created.' if created else 'Existing private local bindings preserved.')
