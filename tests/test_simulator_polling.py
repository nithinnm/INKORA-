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
    monkeypatch.setattr(simulator, 'read_activity', lambda _: True)
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


def test_idle_touchscreen_does_not_claim_jobs(monkeypatch, tmp_path):
    clock, heartbeats = setup_loop(monkeypatch, tmp_path)
    monkeypatch.setattr(simulator, 'read_activity', lambda _: False)
    monkeypatch.setattr(simulator, 'simulate_job', lambda *a, **kw: pytest.fail('idle screen must not claim'))
    def sleep(delay):
        clock[0] += delay
        if clock[0] == 32:
            raise KeyboardInterrupt
    monkeypatch.setattr(simulator.time, 'sleep', sleep)
    with pytest.raises(KeyboardInterrupt):
        simulator.main()
    assert heartbeats == [0, 30]


def test_missing_touchscreen_uses_slow_fallback(monkeypatch, tmp_path):
    clock, _ = setup_loop(monkeypatch, tmp_path)
    monkeypatch.setattr(simulator, 'read_activity', lambda _: None)
    polls = []
    monkeypatch.setattr(simulator, 'simulate_job', lambda *a, **kw: polls.append(clock[0]))
    def sleep(delay):
        clock[0] += delay
        if clock[0] == 32:
            raise KeyboardInterrupt
    monkeypatch.setattr(simulator.time, 'sleep', sleep)
    with pytest.raises(KeyboardInterrupt):
        simulator.main()
    assert polls == [0, 30]


def test_local_hint_is_private_and_expires(monkeypatch, tmp_path):
    from tools import kiosk_activity as activity
    clock = [100]
    monkeypatch.setattr(activity.time, 'monotonic', lambda: clock[0])
    identity = tmp_path/'identity.json'
    activity.publish_activity(identity, 'waiting_upload')
    assert activity.read_activity(identity) is False
    activity.publish_activity(identity, 'queued')
    assert activity.read_activity(identity) is True
    assert activity.activity_path(identity).stat().st_mode & 0o077 == 0
    clock[0] += activity.TTL + 1
    assert activity.read_activity(identity) is None
    activity.activity_path(identity).unlink()
    activity.activity_path(identity).symlink_to(tmp_path/'missing')
    assert activity.read_activity(identity) is None


def test_touchscreen_wakes_listener_after_queue_and_clears_after_completion(monkeypatch, tmp_path):
    import re
    from pathlib import Path
    from tools.kiosk_activity import read_activity
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'tools'))
    import kiosk_terminal as terminal
    identity = tmp_path/'identity.json'
    identity.write_text(json.dumps({'url': 'https://inkora.example', 'device_token': 'test-device'}))
    identity.chmod(0o600)
    states = iter(['waiting_upload', 'queued', 'completed'])
    def post(base, path, payload, token):
        if path.endswith('/status'):
            return {'state': next(states)}
        return {'session_id': 'test-session', 'upload_url': 'https://inkora.example/print/test', 'expires_in': 180}
    monkeypatch.setattr(terminal.agent, 'post', post)
    client = terminal.create_terminal(identity).test_client()
    page = client.get('/')
    nonce = re.search(r'name="kiosk-csrf" content="([^"]+)"', page.text).group(1)
    headers = {'Origin': 'http://localhost', 'X-Kiosk-CSRF': nonce}
    assert client.post('/start', headers=headers).status_code == 200
    assert read_activity(identity) is False
    assert client.post('/status', headers=headers).json['state'] == 'waiting_upload'
    assert read_activity(identity) is False
    assert client.post('/status', headers=headers).json['state'] == 'queued'
    assert read_activity(identity) is True
    assert client.post('/status', headers=headers).json['state'] == 'completed'
    assert read_activity(identity) is False
