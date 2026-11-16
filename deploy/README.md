# Deploy: the tower and its agents, live on AWS (ap-south-1)

The full walk-through, with every screen and every failure, is the book *The Live Tower*. This is the
short version. Run everything from your laptop (WSL Ubuntu), signed in as your own IAM user. Never put
account ids, keys or state files in the repo (`.gitignore` already keeps `deploy/*.tfstate*` out).

1. Sign in and check: `aws login --region ap-south-1`, then from the repo root `make layer`, `make bundle`
   (re-run it after every code change) and `bash deploy/preflight.sh` (every line PASS; it makes one tiny model call to prove Bedrock answers).
2. First apply, scripted brain: `cd deploy && terraform init && terraform plan -var fake=1` (read the plan),
   then `terraform apply -var fake=1`. Outputs: `endpoint`, `data_bucket`, `panel_table`, `console_key`
   (sensitive: `terraform output -raw console_key`).
3. Smoke: `bash smoke.sh` (health open; writes refused without the key, 401, and with a wrong key, 403;
   one diagnosis, its proposal saved to S3 and read back through `POST /proposal`; the console page; the
   Monday job run by hand; today's spend).
4. Live brain: `terraform apply -var fake=0`, then `bash smoke.sh` again. The diagnosis now costs a few
   cents; `spend#<today>` in the panel table shows the day's total.
5. Open the console: the `endpoint` URL in a browser. Reads are open (the data is made up); the first
   change (a signature, a lever) asks for the key once per tab. A click is queued (API Gateway waits 30 s
   at most, a live re-plan takes minutes): the call answers 202 with a job id, the tower function runs the
   work in a second, asynchronous invocation, and the page polls `GET /api/job/<id>` until it is DONE.
   Set `FOUNDRY_ASYNC_CONSOLE=0` on the tower function only to debug a click synchronously.
6. Proposals: every proposal an agent route makes is copied to `s3://$DATA_BUCKET/proposals/<id>.json`
   before the answer goes back (the answer lists them under `saved`); `POST /proposal {"proposal_id": ...}`
   reads one back. The agent routes still answer within API Gateway's 30 s: a long `/plan` sweep with the
   live brain belongs on the console (queued) or `aws lambda invoke`.
7. The schedules: Monday 06:30 in `tower_timezone` (Asia/Kolkata for GFB; set `-var tower_timezone=Europe/London`
   for a UK company) starts a fresh week; the SLA tick runs every 30 minutes. `-var tick_enabled=false` pauses it.
8. Guards, in the order a runaway meets them: the API throttle (5 req/s), the key authorizer, the day's cap
   (`daily_usd`, HTTP 429 when spent), the per-run budget (`budget_usd`), the kill switch (`kill_switch` in the
   panel item; every flip, from any console, lands in CloudWatch as `panel.changed`).
9. Alarms by email: `-var alert_email=you@example.com`, then confirm the first SNS email.
10. A company's own tables: `aws s3 cp data/ s3://$DATA_BUCKET/acme/ --recursive`, then
   `terraform apply -var data_uri=s3://$DATA_BUCKET/acme`.
11. `terraform destroy` when you are done for the day if you want nothing running; the console key and the
    tower's archive go with it.

Cost at demo scale: API Gateway, Lambda, DynamoDB, S3, Scheduler and SSM stay inside the free tier; the
model is the bill (Sonnet 4.6): every response carries its meter (`cost_usd`), and the day's ledger adds them up.
The account's two AWS Budgets alarms (USD 30, USD 57) stay as they are.

CI: `.github/workflows/evals.yml` runs every eval set offline on every push; `deploy.yml` applies only after a
reviewer approves the `production` environment, through an OIDC role (`AWS_DEPLOY_ROLE_ARN`): no long-lived keys.
