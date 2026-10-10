"""Create missing INKORA staging alerts using Cloud Shell login; preserve existing policies."""
import json
import argparse
import subprocess
import urllib.error
import urllib.parse
import urllib.request

PROJECT = 'inkora-510915'
ACCOUNT = 'nithingowdaniluvani@gmail.com'
CHANNEL = 'projects/inkora-510915/notificationChannels/2115294414842539147'
ROOT = 'https://monitoring.googleapis.com/v3/projects/'+PROJECT


def policies():
    common = {'combiner':'OR', 'enabled':True, 'notificationChannels':[CHANNEL],
              'alertStrategy':{'autoClose':'1800s'}}
    specs = [
        ('INKORA staging - repeated web errors', 'run.googleapis.com/request_count',
         'resource.type="cloud_run_revision" AND resource.labels.service_name="inkora-staging" '
         'AND resource.labels.location="asia-south1" AND metric.labels.response_code_class="5xx"',
         2, 'At least three server-error responses in five minutes across the staging service. '
         'Inspect safe application error logs, /health/ready, Neon availability and the current revision. '
         'Never paste database URLs or customer capabilities into incident notes.'),
        ('INKORA staging - maintenance execution failed', 'run.googleapis.com/job/completed_execution_count',
         'resource.type="cloud_run_job" AND resource.labels.job_name="inkora-staging-maintenance" '
         'AND resource.labels.location="asia-south1" AND metric.labels.result="failed"',
         0, 'At least one maintenance execution failed in five minutes. Inspect the execution and safe '
         'container logs; check Neon connectivity, GCS access and retention failures. '
         'Do not rerun migrations, rotate secrets or delete data as an automatic response.'),
    ]
    result=[]
    for name,metric,scope,threshold,description in specs:
        result.append({**common,'displayName':name,
            'documentation':{'content':description,'mimeType':'text/markdown'},
            'conditions':[{'displayName':name,'conditionThreshold':{
                'filter':'metric.type="'+metric+'" AND '+scope,
                'comparison':'COMPARISON_GT','thresholdValue':threshold,'duration':'0s',
                'aggregations':[{'alignmentPeriod':'300s','perSeriesAligner':'ALIGN_SUM',
                                 'crossSeriesReducer':'REDUCE_SUM'}],
                'trigger':{'count':1}}}]})
    return result


def main(check_only=False):
    login=subprocess.run(['gcloud','auth','print-access-token','--account='+ACCOUNT],
                         capture_output=True,text=True,check=True,timeout=30)
    token=login.stdout.strip()
    if not token or any(c in token for c in '\r\n'):
        raise SystemExit('Invalid access-token response; value withheld.')

    def api(path, body=None):
        request=urllib.request.Request(ROOT+path,
            headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'},
            data=json.dumps(body).encode() if body is not None else None,
            method='POST' if body is not None else 'GET')
        try:
            with urllib.request.urlopen(request,timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            # Requests contain only public monitoring configuration, never
            # application secrets. Keep the bearer token out of diagnostics.
            try:
                detail=json.load(error).get('error',{})
                message=str(detail.get('message','No API validation detail supplied.'))
            except (ValueError,AttributeError):
                message='No structured API validation detail supplied.'
            message=message.replace(token,'[REDACTED]')
            raise SystemExit('Monitoring HTTP '+str(error.code)+' during '+
                ('POST ' if body is not None else 'GET ')+path+': '+message[:1500])

    print('Checking email channel metadata...',flush=True)
    channel=api('/notificationChannels/'+CHANNEL.rsplit('/',1)[-1])
    if channel.get('type')!='email' or not channel.get('enabled'):
        raise SystemExit('Stop: expected email channel is not enabled.')
    if channel.get('verificationStatus')=='UNVERIFIED':
        raise SystemExit('Stop: channel needs email verification before policy creation.')
    required={'run.googleapis.com/request_count':'response_code_class',
              'run.googleapis.com/job/completed_execution_count':'result'}
    for metric,label in required.items():
        print('Checking metric descriptor: '+metric,flush=True)
        descriptor=api('/metricDescriptors/'+urllib.parse.quote(metric,safe='/'))
        if descriptor.get('metricKind')!='DELTA' or descriptor.get('valueType')!='INT64':
            raise SystemExit('Stop: metric definition differs from expected counter type.')
        if label not in {v['key'] for v in descriptor.get('labels',[])}:
            raise SystemExit('Stop: required metric label is unavailable: '+label)
        print('Verified metric definition: '+metric,flush=True)
    print('Inspecting existing alert policies...',flush=True)
    existing=[]
    next_page=None
    while True:
        page=api('/alertPolicies'+('?pageToken='+urllib.parse.quote(next_page,safe='') if next_page else ''))
        existing.extend(page.get('alertPolicies',[]))
        next_page=page.get('nextPageToken')
        if not next_page:break
    missing=[]
    for expected in policies():
        matches=[p for p in existing if p.get('displayName')==expected['displayName']]
        if len(matches)>1:
            raise SystemExit('Stop: duplicate policy names; no policies were changed.')
        if matches:
            policy=matches[0]
            conditions=policy.get('conditions',[])
            actual=conditions[0].get('conditionThreshold',{}) if len(conditions)==1 else {}
            target=expected['conditions'][0]['conditionThreshold']
            if (not policy.get('enabled') or CHANNEL not in policy.get('notificationChannels',[])
                    or any(actual.get(k)!=v for k,v in target.items())):
                raise SystemExit('Stop: existing policy differs; inspect it before changing anything.')
            print('Existing matching policy preserved: '+expected['displayName'],flush=True)
        else:missing.append(expected)
    if check_only:
        print('Read-only preflight passed; '+str(len(missing))+' policies are missing. No policies changed.',flush=True)
        return
    for expected in missing:
        print('Creating policy: '+expected['displayName'],flush=True)
        created=api('/alertPolicies',expected)
        print('Created: '+created['displayName'],flush=True)
        print('Policy resource: '+created['name'],flush=True)
    print('Alert configuration complete. Notification delivery still requires a controlled test.',flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check-only',action='store_true',help='Read metadata and metric definitions without creating policies')
    args=parser.parse_args()
    try:
        main(check_only=args.check_only)
    except urllib.error.HTTPError as error:
        raise SystemExit('Monitoring API returned HTTP '+str(error.code)+'. No credentials or API payload printed. Stop and report this status.')
    except urllib.error.URLError as error:
        raise SystemExit('Monitoring connection failed: '+type(error.reason).__name__+'. Stop and report this class.')
    except (subprocess.CalledProcessError,subprocess.TimeoutExpired):
        raise SystemExit('Cloud Shell authentication failed. Sign in privately; no token output printed.')
