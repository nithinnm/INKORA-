"""Hardware-free INKORA agent. Credentials never appear in CLI args or output."""
import argparse
import getpass
import hashlib
import json
import os
import subprocess
from pathlib import Path
import time
import urllib.error
import urllib.parse
import urllib.request

cloud_run_auth = False


def cloud_run_headers(base):
    if not cloud_run_auth:
        return {}
    url = urllib.parse.urlsplit(base)
    if url.scheme != 'https' or not (url.hostname or '').endswith('.run.app'):
        raise SystemExit('--gcloud-auth is restricted to HTTPS Cloud Run endpoints.')
    try:
        result = subprocess.run(['gcloud', 'auth', 'print-identity-token'],
                                capture_output=True, text=True, check=True, timeout=20)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        raise SystemExit('Google Cloud authentication unavailable; token/error output withheld.')
    token = result.stdout.strip()
    if not token or any(c in token for c in '\r\n'):
        raise SystemExit('Invalid Google identity-token response; value withheld.')
    return {'X-Serverless-Authorization': 'Bearer ' + token}


def endpoint(value):
    url = urllib.parse.urlsplit(value)
    if url.username or url.password or url.query or url.fragment or url.path not in ('', '/'):
        raise argparse.ArgumentTypeError('Use a base URL without credentials, path or query.')
    if url.scheme != 'https' and not (url.scheme == 'http' and url.hostname in ('localhost', '127.0.0.1', '::1')):
        raise argparse.ArgumentTypeError('HTTPS required except on loopback.')
    return value.rstrip('/')


def post(base, path, data, token=None):
    headers = {'Content-Type': 'application/json', **cloud_run_headers(base)}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    req = urllib.request.Request(base + path, data=json.dumps(data).encode(), headers=headers, method='POST')
    # No redirects: never forward device credentials to a new destination.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    opener = urllib.request.build_opener(NoRedirect())
    with opener.open(req, timeout=10) as response:
        return json.load(response)


def save_identity(path, identity):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise RuntimeError('Identity already exists. Preserve it; revoke before re-enrolling.')
    # Exclusive creation prevents overwriting an existing credential or following a symlink.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(identity, f)


def simulate_job(base, token, journal=None):
    if journal and journal.pending():
        print('An interrupted job exists in the local journal. Operator review is required; no new job was claimed.')
        return
    # A claim is not completed until the private download checksum has been verified.
    headers = {'Authorization': 'Bearer ' + token, **cloud_run_headers(base)}
    req = urllib.request.Request(base + '/api/devices/jobs/claim', data=b'{}',
        headers={**headers, 'Content-Type':'application/json'}, method='POST')
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    opener = urllib.request.build_opener(NoRedirect())
    with opener.open(req, timeout=10) as response:
        if response.status == 204:
            print('No queued simulated jobs.')
            return
        job = json.load(response)
    if journal:
        journal.record(job,'claimed')
    url = base + '/api/devices/jobs/' + job['job_id']
    request_file = urllib.request.Request(url + '/file', headers={**headers, 'X-Claim-Token':job['claim_token']})
    with opener.open(request_file, timeout=10) as response:
        content = response.read(10 * 1024 * 1024 + 1)
    if len(content) > 10 * 1024 * 1024 or hashlib.sha256(content).hexdigest() != job['file_hash']:
        post(base, '/api/devices/jobs/' + job['job_id'] + '/transition',
             {'claim_token':job['claim_token'], 'state':'needs_review'}, token)
        raise RuntimeError('Document integrity failed; job marked for review.')
    if journal:
        journal.record(job,'download_verified')
    for state in ('printing', 'completed'):
        if journal:
            journal.record(job,state+'_requested')
        post(base, '/api/devices/jobs/' + job['job_id'] + '/transition',
             {'claim_token':job['claim_token'], 'state':state}, token)
    if journal:
        journal.record(job,'done')
    print('SIMULATED completion: ' + job['job_id'] + '. No physical printing performed.')


def main():
    global cloud_run_auth
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True, type=endpoint)
    parser.add_argument('--identity', required=True, type=Path, help='Private identity file outside source control')
    parser.add_argument('--activate', action='store_true')
    parser.add_argument('--gcloud-auth', action='store_true', help='Use Cloud Shell login for private Cloud Run IAM, separately from the device token')
    parser.add_argument('--status', choices=['ready', 'busy', 'error', 'paper_empty', 'unknown'], default='ready')
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--customer-qr',type=Path,help='Write a short-lived customer QR to a private PNG file')
    parser.add_argument('--journal',type=Path,help='Private durable simulator journal, required for --jobs')
    parser.add_argument('--jobs', action='store_true', help='Verify and complete one simulated job per heartbeat')
    args = parser.parse_args()
    cloud_run_auth = args.gcloud_auth
    if args.activate:
        if args.identity.exists():
            parser.error('Identity already exists; refusing to consume an activation code.')
        code = getpass.getpass('Activation code: ')
        identity = post(args.url, '/api/devices/activate', {'code': code})
        identity['url'] = args.url
        save_identity(args.identity, identity)
        print('Device activated. Identity saved with owner-only permissions.')
    with args.identity.open() as f:
        identity = json.load(f)
    if identity['url'] != args.url:
        parser.error('Server differs from the enrolled identity.')
    if args.jobs and not args.journal:
        parser.error('--jobs requires --journal outside source control')
    journal=None
    if args.journal:
        from spool import Journal
        journal=Journal(args.journal)
    while True:
        try:
            result = post(args.url, '/api/devices/heartbeat', {'printer_status':args.status, 'software_version':'sim-0.2'}, identity['device_token'])
            print(f"Kiosk {result['kiosk_id']}: {result['status']}")
            if args.customer_qr:
                import qrcode
                result=post(args.url,'/api/devices/customer-sessions',{},identity['device_token'])
                fd=os.open(args.customer_qr,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                with os.fdopen(fd,'wb') as output:
                    qrcode.make(result['upload_url']).save(output,format='PNG')
                print('Customer QR saved. It expires in three minutes; no URL or token was logged.')
                args.customer_qr=None
            if args.jobs and args.status == 'ready':
                simulate_job(args.url, identity['device_token'], journal)
        except urllib.error.HTTPError as error:
            print(f'Server rejected heartbeat (HTTP {error.code}).')
            if error.code in (401,403) or args.once:
                raise SystemExit(1)
        except (urllib.error.URLError, TimeoutError):
            print('Connection unavailable; no printing is performed. Retrying in 30 seconds.')
            if args.once:
                raise SystemExit(1)
        if args.once:
            break
        time.sleep(30)
    if journal:
        journal.close()


if __name__ == '__main__':
    main()
