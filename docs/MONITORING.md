# INKORA staging alerts

The email channel supplied by the user is
`projects/inkora-510915/notificationChannels/2115294414842539147`.
The helper uses Cloud Shell's authenticated account, never a service-account key.
It inspects counter metric descriptors and all existing policies before writing.
Matching policies are preserved; unexpected existing definitions stop setup.
It does not recreate/rotate application secrets, change IAM or redeploy services.

Run `python3 tools/cloudshell_alerts_setup.py` in authenticated Cloud Shell.
It creates only missing policies:

| Policy | Trigger | Destination |
| --- | --- | --- |
| Repeated web errors | More than 2 server errors in 5 minutes, aggregated across staging revisions | INKORA email channel |
| Maintenance execution failure | At least 1 failed cleanup execution in 5 minutes | INKORA email channel |

Metrics, rather than capability-bearing request logs, drive these alerts. Customer
request-log exclusions remain intact. CPU/requests/metrics/alerting can be billable;
this setup is not a spending cap. Verify current Cloud Monitoring pricing.

These policies do not detect every failure: scheduler invocation failures without
a started execution, a completely idle inaccessible service, and individual stale
devices require additional monitoring. They are initial staging policies, not
proof of full production observability.

Configuration is not notification-delivery evidence. Verify policy/channel state
in Console and test an isolated synthetic failure. Do not break the running web
service, revoke Neon/GCS access or run retention against another database just to
trigger an alert. A synthetic failing Cloud Run job can test the same execution
metric/email path without customer data, using a separate temporary policy scoped
to that job. Remove temporary test resources after the alert and recovery are
verified. Preserve the actual web and cleanup policies.

Incident response: inspect safe errors and the failed execution, check database
and storage availability, protect ambiguous print jobs from replays, and record
recovery. Do not log URLs with capabilities, passwords, tokens or customer files.
