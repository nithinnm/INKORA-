"""Durable simulator journal. It prevents silent replay after an interrupted run."""
import json
import os
from pathlib import Path
import sqlite3


class Journal:
    def __init__(self,path):
        path=Path(path);path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        # Create exclusively or reject symlinks; never replace a user's file.
        fd=os.open(path,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600);os.close(fd)
        if path.stat().st_mode & 0o077:
            raise RuntimeError('Journal must have owner-only permissions')
        self.connection=sqlite3.connect(path)
        self.connection.execute('PRAGMA synchronous=FULL')
        self.connection.execute('CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, payload TEXT NOT NULL, state TEXT NOT NULL)')
        self.connection.commit()

    def pending(self):
        return [(json.loads(data),state) for data,state in self.connection.execute("SELECT payload,state FROM jobs WHERE state != 'done'")]

    def record(self,job,state):
        self.connection.execute('INSERT INTO jobs(id,payload,state) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,state=excluded.state',
            (job['job_id'],json.dumps(job),state))
        self.connection.commit()

    def close(self):
        self.connection.close()
