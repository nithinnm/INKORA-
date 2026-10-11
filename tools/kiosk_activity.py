"""Credential-free, local-only hint; cloud claims remain the authority."""
import json
import os
from pathlib import Path
import secrets
import stat
import time

TTL = 15


def activity_path(identity):
    return Path(str(identity) + '.activity')


def read_activity(identity):
    path = activity_path(identity)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd) as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.getuid():
                return None
            data = json.loads(source.read(1024))
        age = time.monotonic() - data['updated']
        if not 0 <= age <= TTL or type(data['queued']) is not bool:
            return None
        return data['queued']
    except (OSError, ValueError, KeyError, TypeError):
        return None


def publish_activity(identity, state):
    path = activity_path(identity)
    # No device credentials, session IDs, customer URLs, or job contents in IPC.
    payload = {'queued': state == 'queued', 'updated': time.monotonic()}
    temporary = path.with_name(path.name + '.' + secrets.token_hex(8))
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as output:
            json.dump(payload, output)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
