"""Job polling stays responsive without flooding heartbeats or retrying outages."""
import json
import sys
from types import SimpleNamespace
import urllib.error

import pytest
from tools import kiosk_simulator as simulator


def setup_loop(monkeypatch, tmp_path, *, once=False):
    identity = tmp_path / 'identity.json'
    identity.write_text(json.dumps({'url': 'https://inkora.example', 'device_token': 'local-test-device'}))
    argv = ['simulator', '--url=https://inkora.example', '--identity='+str(identity),
            '--journal='+str(tmp_path/'journal.db'), '--jobs']
    if once:
        argv.append('--once')
    monkeypatch.setattr(sys, 'argv', argv)
    journal = SimpleNamespace(close=lambda: None)
    monkeypatch.setitem(sys.modules, 'spool', SimpleNamespace(Journal=lambda _: journal))
    clock = [0]
    heartbeats = []
    monkeypatch.setattr(simulator.time, 'monotonic', lambda: clock[0])
    def heartbeat(*args, **kwargs):
        heartbeats.append(clock[0])
        return {'kiosk_id': 1, 'status': 'Online'}
    monkeypatch.setattr(simulator, 'post', heartbeat)
    return clock, heartbeats


def test_fast_jobs_keep_heartbeat_at_thirty_seconds(monkeypatch, tmp_path):
    clock, heartbeats = setup_loop(monkeypatch, tmp_path)
    polls, sleeps = [], []
    monkeypatch.setattr(simulator, 'simulate_job', lambda *a, **kw: polls.append(clock[0]))
    def sleep(delay):
        sleeps.append(delay)
        clock[0] += delay
        if clock[0] == 32:
            raise KeyboardInterrupt
    monkeypatch.setattr(simulator.time, 'sleep', sleep)
    with pytest.raises(KeyboardInterrupt):
        simulator.main()
    assert polls == list(range(32))
    assert heartbeats == [0, 30]
    assert sleeps == [1]*32


def test_outage_backs_off_then_resumes_fast_polling(monkeypatch, tmp_path):
    clock, _ = setup_loop(monkeypatch, tmp_path)
    polls, sleeps = [], []
    def poll(*args, **kwargs):
        polls.append(clock[0])
        if len(polls) == 1:
            raise urllib.error.URLError('test outage')
    monkeypatch.setattr(simulator, 'simulate_job', poll)
    def sleep(delay):
        sleeps.append(delay)
        clock[0] += delay
        if len(sleeps) == 2:
            raise KeyboardInterrupt
    monkeypatch.setattr(simulator.time, 'sleep', sleep)
    with pytest.raises(KeyboardInterrupt):
        simulator.main()
    assert polls == [0, 30]
    assert sleeps == [30, 1]


def test_once_does_not_enter_polling_loop(monkeypatch, tmp_path):
    _, heartbeats = setup_loop(monkeypatch, tmp_path, once=True)
    polls = []
    monkeypatch.setattr(simulator, 'simulate_job', lambda *a, **kw: polls.append(kw['quiet']))
    monkeypatch.setattr(simulator.time, 'sleep', lambda _: pytest.fail('one-shot must not sleep'))
    simulator.main()
    assert heartbeats == [0]
    assert polls == [False]
