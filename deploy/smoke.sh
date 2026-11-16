#!/usr/bin/env bash
# Smoke test of a deployed tower, one check per line. Run from deploy/ after `terraform apply`:
#   bash smoke.sh            # the agents and the tower, with whatever brain the apply set (fake=1 or 0)
# Reads the endpoint and the key from Terraform's outputs; prints no secret.
set -uo pipefail
EP=$(terraform output -raw endpoint)
KEY=$(terraform output -raw console_key)
TABLE=$(terraform output -raw panel_table)
REGION="${AWS_REGION:-ap-south-1}"
ok=0; bad=0
check() { if [ "$2" = "$3" ]; then echo "  PASS  $1"; ok=$((ok + 1)); else echo "  FAIL  $1 (expected $3, got $2)"; bad=$((bad + 1)); fi; }
code() { curl -s -o /tmp/smoke.json -w "%{http_code}" "$@"; }

echo "Smoke test of $EP"
check "health is open"                         "$(code "$EP/health")" 200
check "a write without the key is refused"     "$(code -X POST "$EP/diagnose" -H 'content-type: application/json' -d '{"dc":"CHN","week":12}')" 401
check "a write with a wrong key is refused"    "$(code -X POST "$EP/diagnose" -H 'content-type: application/json' -H 'x-tower-key: wrong' -d '{"dc":"CHN","week":12}')" 403
check "diagnose CHN MN-1L with the key"        "$(code -X POST "$EP/diagnose" -H 'content-type: application/json' -H "x-tower-key: $KEY" \
                                                  -d '{"dc":"CHN","sku":"MN-1L","week":12,"kpi":"OTIF"}')" 200
python3 -c "import json; d=json.load(open('/tmp/smoke.json')); print('        root cause:', (d.get('diagnosis') or {}).get('root_cause'), '| cost USD', d['meter'].get('cost_usd'))"
pid=$(python3 -c "import json; d=json.load(open('/tmp/smoke.json')); print((d.get('saved') or [''])[0])")
check "the diagnosis's proposal was saved"     "$([ -n "$pid" ] && echo 1 || echo 0)" 1
check "a saved proposal reads back"            "$(code -X POST "$EP/proposal" -H 'content-type: application/json' -H "x-tower-key: $KEY" \
                                                  -d "{\"proposal_id\":\"$pid\"}")" 200
check "the console page loads"                 "$(code "$EP/")" 200
check "the tower state is readable"            "$(code "$EP/api/state")" 200

BUCKET=$(terraform output -raw data_bucket)
echo "  ...   clearing the old state (the bucket is versioned: aws s3api list-object-versions can bring it back)"
aws s3 rm --region "$REGION" "s3://$BUCKET/tower/state.json" >/dev/null 2>&1   # so the plans we wait for are this run's
echo "  ...   starting the Monday job by hand, asynchronously as the schedule does (a live Monday takes minutes)"
aws lambda invoke --region "$REGION" --function-name gfb-foundry-tower --invocation-type Event \
    --cli-binary-format raw-in-base64-out --payload '{"tower":"monday","fresh":true}' /tmp/monday.json >/dev/null
plans=0
for i in $(seq 1 60); do               # up to 10 minutes, one look every 10 s
  sleep 10
  plans=$(curl -s "$EP/api/state" | python3 -c 'import json,sys
try: print(sum(json.load(sys.stdin)["counts"].values()))
except Exception: print(0)')
  [ "${plans:-0}" -gt 0 ] && break
done
echo "        plans after Monday: $plans (waited $((i * 10)) s)"
check "plans exist after Monday"               "$([ "${plans:-0}" -gt 0 ] && echo 1 || echo 0)" 1

today=$(date -u +%F)
spent=$(aws dynamodb get-item --region "$REGION" --table-name "$TABLE" --key "{\"panel_id\":{\"S\":\"spend#$today\"}}" \
        --query 'Item.usd.N' --output text 2>/dev/null)
echo "        spend ledger today: USD ${spent:-0}"
echo "$ok passed, $bad failed"
[ "$bad" -eq 0 ]
