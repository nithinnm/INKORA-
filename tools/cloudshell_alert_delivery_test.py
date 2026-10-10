"""Create or remove one isolated staging email-delivery test policy."""
import argparse
import json
import subprocess
import urllib.request
import urllib.error

from cloudshell_alerts_setup import ACCOUNT, CHANNEL, ROOT

NAME = 'INKORA staging - temporary email delivery test'
MARKER = 'INKORA_NOTIFICATION_DELIVERY_TEST_V1'


def main(cleanup=False):
    token = subprocess.run(['gcloud', 'auth', 'print-access-token', '--account='+ACCOUNT],
                           capture_output=True, text=True, check=True, timeout=30).stdout.strip()
    if not token or any(c in token for c in '\r\n'):
        raise SystemExit('Invalid token response; value withheld.')

    def api(url, method='GET', body=None):
        request = urllib.request.Request(url, method=method,
            headers={'Authorization': 'Bearer '+token, 'Content-Type': 'application/json'},
            data=json.dumps(body).encode() if body is not None else None)
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read()
            return json.loads(raw) if raw else {}

    existing = []
    url = ROOT+'/alertPolicies'
    while url:
        page = api(url)
        existing.extend(page.get('alertPolicies', []))
        from urllib.parse import quote
        cursor = page.get('nextPageToken')
        url = ROOT+'/alertPolicies?pageToken='+quote(cursor, safe='') if cursor else None
    matches = [p for p in existing if p.get('displayName') == NAME]
    if len(matches) > 1:
        raise SystemExit('Duplicate test policies found; stopped without changes.')
    if matches and matches[0].get('documentation', {}).get('content') != MARKER:
        raise SystemExit('Existing policy is not this helper’s test; stopped without changes.')
    if cleanup:
        if matches:
            api('https://monitoring.googleapis.com/v3/'+matches[0]['name'], method='DELETE')
            print('Temporary test policy removed. Existing operational alerts preserved.')
        else:
            print('Temporary test policy is already absent.')
        return
    channel = api(ROOT+'/notificationChannels/'+CHANNEL.rsplit('/', 1)[-1])
    if channel.get('type') != 'email' or not channel.get('enabled'):
        raise SystemExit('Expected enabled email channel is unavailable.')
    if not matches:
        api(ROOT+'/alertPolicies', method='POST', body={
            'displayName': NAME, 'enabled': True, 'combiner': 'OR',
            'documentation': {'content': MARKER, 'mimeType': 'text/markdown'},
            'notificationChannels': [CHANNEL],
            'alertStrategy': {'autoClose': '1800s'},
            'conditions': [{'displayName': 'TEST ONLY - successful staging request',
                'conditionThreshold': {
                    'filter': 'metric.type="run.googleapis.com/request_count" AND resource.type="cloud_run_revision" AND resource.labels.service_name="inkora-staging" AND resource.labels.location="asia-south1" AND metric.labels.response_code_class="2xx"',
                    'comparison': 'COMPARISON_GT', 'thresholdValue': 0, 'duration': '0s',
                    'aggregations': [{'alignmentPeriod': '300s', 'perSeriesAligner': 'ALIGN_SUM', 'crossSeriesReducer': 'REDUCE_SUM'}],
                    'trigger': {'count': 1}}}]})
        print('Temporary email test policy created; operational policies unchanged.')
    else:
        print('Existing temporary test policy reused.')
    with urllib.request.urlopen('https://inkora-staging-433513064052.asia-south1.run.app/health/ready', timeout=30) as response:
        ready = json.load(response)
    if ready.get('status') != 'ready':
        raise SystemExit('Readiness did not pass. Test policy remains for inspection.')
    print('Successful readiness request generated. Allow up to 10 minutes for the TEST incident email; check spam too.')
    print('After confirming delivery, run: python3 tools/cloudshell_alert_delivery_test.py --cleanup')
    print('If no email arrives, report that outcome; do not change operational thresholds.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cleanup', action='store_true')
    try:
        main(parser.parse_args().cleanup)
    except urllib.error.HTTPError as error:
        raise SystemExit('HTTP '+str(error.code)+'. Stop and report this status; credentials withheld.')
    except (urllib.error.URLError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        raise SystemExit('Connection or authentication unavailable. Stop; credentials withheld.')
