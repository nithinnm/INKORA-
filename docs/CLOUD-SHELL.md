# Deploy the INKORA staging pilot from Cloud Shell

These commands use your Cloud Shell login, not a service-account key. Run each block in order in the same shell. Stop on an error. This deploys simulated printing, not paid production printing. Cloud SQL, storage, registry, jobs and Cloud Run can incur charges. The shared-core database below is a small pilot, without HA. Review its price and configure a billing budget before step 3; a budget alert does not cap spending.

## Resume after the successful Cloud Build

The staging image reported built and pushed successfully is:
`asia-south1-docker.pkg.dev/inkora-510915/inkora/web@sha256:f1f85033dd99cb7c6096c970d4987c9f779aff45a72f0b6eb92a936095208cef`.
Keep this immutable image. Skip step 2: do not rebuild or retry a local Docker push.

The configured architecture is Cloud Run (1 CPU/1 GiB, concurrency 2, at most 3 instances), PostgreSQL through the authenticated Cloud SQL socket, a private GCS documents bucket, three Secret Manager bindings, a one-off migration job and scheduled maintenance. Admin MFA, owner isolation, kiosk/device credentials, per-kiosk prices, customer QR/PDF handling, immutable quotes, job leases/review and audit functionality remain in the application. SQLite and the trust-auth Compose database are local-only and must not be deployed. `INKORA_ENV=staging` keeps simulated printing available; production mode deliberately blocks it.

Run this inventory before any resource creation. It does not inspect secret values. Resolve missing API/permission errors before interpreting inventory results.

```bash
gcloud config set project inkora-510915
export PROJECT=inkora-510915 REGION=asia-south1 SERVICE=inkora-staging
export IMAGE=asia-south1-docker.pkg.dev/inkora-510915/inkora/web@sha256:f1f85033dd99cb7c6096c970d4987c9f779aff45a72f0b6eb92a936095208cef
export BUCKET=inkora-510915-staging-documents SQL_INSTANCE=inkora-staging-db
export SA=inkora-staging@inkora-510915.iam.gserviceaccount.com
test "$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')" = 433513064052
gcloud billing projects describe "$PROJECT"
gcloud artifacts docker images describe "$IMAGE"
gcloud services list --enabled --project="$PROJECT"
gcloud run services list --region="$REGION"
gcloud run jobs list --region="$REGION"
gcloud sql instances list
gcloud storage buckets list --project="$PROJECT"
gcloud secrets list
gcloud iam service-accounts list
```

If `inkora-staging` already exists, also inspect its bindings and deployed revision. The commands below show metadata, not Secret Manager contents; do not share output if an older revision mistakenly contains inline secret values.

```bash
gcloud run services describe inkora-staging --region=asia-south1 --format='yaml(status.url,status.latestReadyRevisionName,spec.template.spec.serviceAccountName,spec.template.spec.containers,spec.template.metadata.annotations)'
```

### Budget gate before Cloud SQL

In Console → Billing, select the billing account returned by `gcloud billing projects describe`, then Budgets & alerts → Create budget:

1. Name: `INKORA staging monthly`.
2. Scope: only project `inkora-510915`; include all services so Cloud Build, Artifact Registry, Cloud SQL, Cloud Run, storage, secrets and scheduling are covered.
3. Period: monthly; amount: your agreed monthly staging budget, in the billing account's currency. Do not assume a ₹1,000 budget guarantees this architecture fits within ₹1,000.
4. Actual-spend thresholds: 50%, 80%, 100%; add a 100% forecast threshold.
5. Enable email notifications to billing administrators/users and your monitored notification channel where available. Confirm the recipient is monitored.
6. Save, reopen and verify project scope, amount/currency, thresholds and notifications. Reuse an existing matching budget instead of creating a duplicate.

Project Owner alone may not have permission to create billing-account budgets; request Billing Account Costs Manager access from the billing administrator if needed. Do not continue past this gate until the budget is saved. Alerts can arrive late and do not stop spending.

Review Cloud SQL's Mumbai price estimate for the `db-f1-micro` pilot with 10 GB SSD, backups/PITR and networking before creating it. It bills continuously and is not highly available. If the estimate exceeds the intended budget, stop and choose a PostgreSQL provider/plan within budget; retain PostgreSQL transactions and existing features rather than substituting SQLite or removing functionality. No claim of net-zero cost is made.

After the inventory confirms the planned resources are new, billing is enabled, the budget is saved and the estimate is acceptable, enable only the required APIs and continue at **step 3** below, then steps 4–8. Do not rerun creation commands against existing resources or rotate existing application keys.

```bash
gcloud services enable run.googleapis.com sqladmin.googleapis.com storage.googleapis.com secretmanager.googleapis.com iam.googleapis.com cloudscheduler.googleapis.com
```

## 1. Get the source and verify the project

Clone the GitHub repository in Cloud Shell. For a private repository, use your own GitHub login; do not paste a token into this chat. If you already have a checkout, preserve local changes and update it instead of cloning over it.

```bash
git clone https://github.com/nithinnm/INKORA-.git
cd INKORA-
set -euo pipefail
gcloud config set project inkora-510915
export PROJECT=inkora-510915 REGION=asia-south1 SERVICE=inkora-staging
export BUCKET=inkora-510915-staging-documents SQL_INSTANCE=inkora-staging-db
export SA=inkora-staging@inkora-510915.iam.gserviceaccount.com
test "$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')" = 433513064052
gcloud billing projects describe "$PROJECT"
```

Confirm `billingEnabled: true`. Inspect existing resources before creating anything. A permission/API error is not an empty inventory; resolve it before continuing. Never change an existing database or bucket merely because a name matches.

```bash
gcloud services enable run.googleapis.com artifactregistry.googleapis.com sqladmin.googleapis.com storage.googleapis.com secretmanager.googleapis.com iam.googleapis.com cloudscheduler.googleapis.com
gcloud run services list --platform=managed
gcloud sql instances list
gcloud storage buckets list --project="$PROJECT"
gcloud artifacts repositories list --location="$REGION"
gcloud secrets list
gcloud iam service-accounts list
```

If any planned name already exists, stop and check it belongs to this fleet. The following commands are for new resources, not a rerun/reset script.

## 2. Build an immutable image

```bash
gcloud artifacts repositories create inkora --repository-format=docker --location="$REGION"
gcloud auth configure-docker "$REGION-docker.pkg.dev" --quiet
export IMAGE="$REGION-docker.pkg.dev/$PROJECT/inkora/web:$(date -u +%Y%m%d%H%M%S)"
docker build -f deploy/Dockerfile.cloudshell -t "$IMAGE" .
docker push "$IMAGE"
export IMAGE="$REGION-docker.pkg.dev/$PROJECT/inkora/web@$(docker inspect --format='{{index .RepoDigests 0}}' "$IMAGE" | cut -d@ -f2)"
```

The Cloud Shell Dockerfile installs the same SHA-256 locked dependencies through normal verified TLS. It does not require the managed development environment's offline wheels. Scan the registry image before public launch.

## 3. Create the private storage, database and identity (billable)

```bash
gcloud iam service-accounts create inkora-staging --display-name='INKORA staging runtime'
gcloud storage buckets create "gs://$BUCKET" --location="$REGION" --uniform-bucket-level-access --public-access-prevention
gcloud storage buckets update "gs://$BUCKET" --soft-delete-duration=0 --lifecycle-file=deploy/storage-lifecycle.json
gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" --member="serviceAccount:$SA" --role=roles/storage.objectUser
gcloud sql instances create "$SQL_INSTANCE" --database-version=POSTGRES_17 --edition=ENTERPRISE --tier=db-f1-micro --region="$REGION" --storage-type=SSD --storage-size=10 --backup-start-time=20:00 --enable-point-in-time-recovery
gcloud sql databases create inkora --instance="$SQL_INSTANCE"
gcloud projects add-iam-policy-binding "$PROJECT" --member="serviceAccount:$SA" --role=roles/cloudsql.client --condition=None
export CONNECTION="$(gcloud sql instances describe "$SQL_INSTANCE" --format='value(connectionName)')"
```

Do not add authorized public IP networks. Cloud Run will use the authenticated Cloud SQL Unix socket. The instance has a public endpoint, but the connector uses IAM and encrypted transport. Soft delete is explicitly disabled on this disposable-document bucket; establish the final retention policy before production.

## 4. Generate and store secrets without printing them

Do not enable shell tracing (`set -x`). The temporary directory stays outside source; remove it at the end. Keep the session/encryption secrets stable for future releases.

```bash
export PRIVATE_DIR="$(mktemp -d)"
chmod 700 "$PRIVATE_DIR"
python3 - <<'PY'
import os,secrets,pathlib
p=pathlib.Path(os.environ['PRIVATE_DIR'])
for name in ('db-password','session-secret'):
 (p/name).write_text(secrets.token_urlsafe(48)); (p/name).chmod(0o600)
import base64
(p/'encryption-key').write_text(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())
(p/'encryption-key').chmod(0o600)
password=(p/'db-password').read_text()
(p/'database-url').write_text('postgresql+psycopg://inkora:'+password+'@/inkora?host=/cloudsql/'+os.environ['CONNECTION'])
(p/'database-url').chmod(0o600)
PY
gcloud sql users create inkora --instance="$SQL_INSTANCE" --password="$(cat "$PRIVATE_DIR/db-password")"
for item in database-url session-secret encryption-key; do
  gcloud secrets create "inkora-staging-$item" --replication-policy=automatic --data-file="$PRIVATE_DIR/$item"
  gcloud secrets add-iam-policy-binding "inkora-staging-$item" --member="serviceAccount:$SA" --role=roles/secretmanager.secretAccessor
done
export SECRET_BINDINGS='DATABASE_URL=inkora-staging-database-url:1,INKORA_SESSION_SECRET=inkora-staging-session-secret:1,INKORA_ENCRYPTION_KEY=inkora-staging-encryption-key:1'
export PUBLIC_URL="https://$SERVICE-433513064052.$REGION.run.app"
export APP_ENV="INKORA_ENV=staging,INKORA_CLOUD_PROJECT=$PROJECT,INKORA_GCS_BUCKET=$BUCKET,INKORA_PUBLIC_URL=$PUBLIC_URL"
```

The database-password argument is ephemeral process data; it is never a literal shell-history entry. Do not share command tracing or secret files.

## 5. Migrate before starting web traffic

```bash
gcloud run jobs create inkora-staging-migrate --image="$IMAGE" --region="$REGION" --service-account="$SA" --set-cloudsql-instances="$CONNECTION" --set-env-vars="$APP_ENV" --set-secrets="$SECRET_BINDINGS" --command=flask --args=--app,inkora:create_app,db,upgrade --tasks=1 --parallelism=1 --max-retries=0 --task-timeout=600s --cpu=1 --memory=1Gi
gcloud run jobs execute inkora-staging-migrate --region="$REGION" --wait
gcloud run deploy "$SERVICE" --image="$IMAGE" --region="$REGION" --service-account="$SA" --add-cloudsql-instances="$CONNECTION" --set-env-vars="$APP_ENV" --set-secrets="$SECRET_BINDINGS" --no-allow-unauthenticated --min-instances=0 --max-instances=3 --concurrency=2 --cpu=1 --memory=1Gi --timeout=60 --port=8080 --startup-probe=httpGet.path=/health/ready,httpGet.port=8080,periodSeconds=5,failureThreshold=12 --liveness-probe=httpGet.path=/health/live,httpGet.port=8080,periodSeconds=30,failureThreshold=3
export PUBLIC_URL="$(gcloud run services describe "$SERVICE" --region="$REGION" --format='value(status.url)')"
gcloud run services update "$SERVICE" --region="$REGION" --update-env-vars="INKORA_PUBLIC_URL=$PUBLIC_URL"
curl --fail --silent --show-error -H "Authorization: Bearer $(gcloud auth print-identity-token)" "$PUBLIC_URL/health/ready"
```

Expect `{"status":"ready"}`. The first URL is a bootstrap base; the actual service URL is read back and saved before use.

## 6. Create the admin with interactive prompts

Use a local Cloud SQL proxy with your existing Cloud Shell account. No service-account key is needed. The proxy listens on loopback only.

```bash
curl --fail --location --output "$PRIVATE_DIR/cloud-sql-proxy" https://storage.googleapis.com/cloud-sql-connectors/cloud-sql-proxy/v2.14.3/cloud-sql-proxy.linux.amd64
chmod 700 "$PRIVATE_DIR/cloud-sql-proxy"
"$PRIVATE_DIR/cloud-sql-proxy" --gcloud-auth --address=127.0.0.1 --port=5439 "$CONNECTION" >"$PRIVATE_DIR/proxy.log" 2>&1 &
export PROXY_PID=$!
python3 - <<'PY'
import os,pathlib,socket,time
p=pathlib.Path(os.environ['PRIVATE_DIR'])
for i in range(30):
 try:
  with socket.create_connection(('127.0.0.1',5439),timeout=1): break
 except OSError: time.sleep(1)
else: raise SystemExit('Proxy failed: inspect the private proxy.log, without sharing credentials.')
values={'DATABASE_URL':'postgresql+psycopg://inkora:'+(p/'db-password').read_text()+'@127.0.0.1:5439/inkora','INKORA_SESSION_SECRET':(p/'session-secret').read_text(),'INKORA_ENCRYPTION_KEY':(p/'encryption-key').read_text(),'INKORA_ENV':'staging','INKORA_CLOUD_PROJECT':os.environ['PROJECT'],'INKORA_GCS_BUCKET':os.environ['BUCKET'],'INKORA_PUBLIC_URL':os.environ['PUBLIC_URL']}
(p/'admin.env').write_text('\n'.join(k+'='+v for k,v in values.items())+'\n'); (p/'admin.env').chmod(0o600)
PY
docker run --rm -it --network=host --env-file="$PRIVATE_DIR/admin.env" "$IMAGE" flask --app inkora:create_app create-admin
kill "$PROXY_PID"
```

Enter your email/name and a unique password of at least 14 characters. Do not put passwords into command arguments. Admin MFA enrollment is required at login.

## 7. Schedule retention maintenance

```bash
gcloud run jobs create inkora-staging-maintenance --image="$IMAGE" --region="$REGION" --service-account="$SA" --set-cloudsql-instances="$CONNECTION" --set-env-vars="INKORA_ENV=staging,INKORA_CLOUD_PROJECT=$PROJECT,INKORA_GCS_BUCKET=$BUCKET,INKORA_PUBLIC_URL=$PUBLIC_URL" --set-secrets="$SECRET_BINDINGS" --command=flask --args=--app,inkora:create_app,maintenance,--retention-hours,24 --tasks=1 --parallelism=1 --max-retries=0 --task-timeout=600s --cpu=1 --memory=1Gi
gcloud run jobs execute inkora-staging-maintenance --region="$REGION" --wait
gcloud iam service-accounts create inkora-scheduler --display-name='INKORA maintenance scheduler'
export SCHEDULER_SA=inkora-scheduler@inkora-510915.iam.gserviceaccount.com
gcloud run jobs add-iam-policy-binding inkora-staging-maintenance --region="$REGION" --member="serviceAccount:$SCHEDULER_SA" --role=roles/run.invoker
gcloud scheduler jobs create http inkora-staging-maintenance --location="$REGION" --schedule='*/15 * * * *' --time-zone=Asia/Kolkata --uri="https://run.googleapis.com/v2/projects/$PROJECT/locations/$REGION/jobs/inkora-staging-maintenance:run" --http-method=POST --oauth-service-account-email="$SCHEDULER_SA" --oauth-token-scope=https://www.googleapis.com/auth/cloud-platform --headers=Content-Type=application/json --message-body='{}' --max-retry-attempts=0
```

One task runs per execution, capped at ten minutes against a fifteen-minute schedule. Do not manually execute maintenance during a scheduled run. Monitor job failures and object retention; bucket lifecycle alone is not the application cleanup scheduler.

## 8. Inspect logging, then enable the pilot website

Cloud Run request logs can record the private `/print/` customer capabilities. Before exposing customer routes, add an exclusion on the project's `_Default` sink. Preserve existing exclusions. In Console → Logging → Log Router → `_Default` → Edit sink → Add exclusion, name it `inkora-private-customer-paths` and use:

```text
resource.type="cloud_run_revision"
resource.labels.service_name="inkora-staging"
logName="projects/inkora-510915/logs/run.googleapis.com%2Frequests"
httpRequest.requestUrl=~"/print(/|\\?)"
```

Check other log sinks/export destinations also exclude these paths. Verify using a disposable pilot capability. This console step is deliberate: blindly replacing sink exclusions would discard existing project settings.

After health, migration, backups and logging checks pass, this explicit command allows browser/phone/device access. App login, MFA and device credentials still apply. It creates a publicly reachable staging pilot, not a production paid-printing service.

```bash
gcloud run services add-iam-policy-binding "$SERVICE" --region="$REGION" --member=allUsers --role=roles/run.invoker
printf 'Pilot URL: %s\n' "$PUBLIC_URL"
rm -rf -- "$PRIVATE_DIR"
unset PRIVATE_DIR
```

If an organization policy prohibits `allUsers`, stop rather than bypassing it. Sign in → enroll MFA → invite owner → register kiosk → set prices → activate simulator → customer QR/PDF → simulated job. Follow `docs/WALKTHROUGH.md`.

## Release limits and troubleshooting

These commands have not been executed against your project. Locally, the application and offline container were tested; this Cloud Shell build/deployment path still needs verification. Paste a failed command's error, with keys/tokens/URLs carrying capabilities removed. Do not rerun resource creation or regenerate the stable encryption/session keys blindly.

Live Razorpay/ledger integration, real printer/Pi acceptance, signed updater/rollback, HA/PITR restore drills, load/RSS checks, image OS scanning and monitoring acceptance remain production gates. Do not switch `INKORA_ENV` to production to bypass them: production intentionally blocks the simulated print routes. Cloud SQL continues billing when Cloud Run is idle. Database/image/resource deletion is not included in this guide.
