#!/usr/bin/env bash
# Preflight: everything that must be true before `terraform apply`. Run from the repo root:
#   bash deploy/preflight.sh
# It changes nothing in AWS except one tiny model call (about USD 0.001) to prove Bedrock answers.
set -uo pipefail
REGION="${AWS_REGION:-ap-south-1}"
MODEL="${MODEL_ID:-global.anthropic.claude-sonnet-4-6}"
ok=0; bad=0
pass() { echo "  PASS  $1"; ok=$((ok + 1)); }
fail() { echo "  FIX   $1"; bad=$((bad + 1)); }

echo "Preflight for gfb-foundry in $REGION"

command -v aws >/dev/null && pass "aws CLI: $(aws --version 2>&1 | cut -d' ' -f1)" || fail "install the AWS CLI v2"
command -v terraform >/dev/null && pass "terraform: $(terraform version | head -1)" || fail "install Terraform 1.5 or later"
command -v curl >/dev/null && pass "curl" || fail "install curl"

who=$(aws sts get-caller-identity --query Arn --output text 2>/dev/null)
if [ -n "$who" ]; then pass "signed in as $who"; else fail "sign in: aws login --region $REGION"; fi

acct=$(aws sts get-caller-identity --query Account --output text 2>/dev/null)
[ -n "$acct" ] && echo "        account $acct (the repo never holds it; Terraform reads it at plan time)"

limit=$(aws lambda get-account-settings --region "$REGION" --query AccountLimit.ConcurrentExecutions --output text 2>/dev/null)
if [ -n "$limit" ]; then
  pass "Lambda concurrency limit $limit (the tower uses a DynamoDB lock, not a reservation)"
else
  fail "could not read Lambda account settings (permissions or region?)"
fi

body='[{"role":"user","content":[{"text":"Reply with the single word: ready"}]}]'
answer=$(aws bedrock-runtime converse --region "$REGION" --model-id "$MODEL" --messages "$body" \
         --inference-config '{"maxTokens":5}' --query 'output.message.content[0].text' --output text 2>&1)
if echo "$answer" | grep -qi "ready"; then
  pass "Bedrock answers through $MODEL"
else
  fail "Bedrock did not answer through $MODEL: $answer"
fi

[ -f deploy/build/layer.zip ] && pass "layer built (deploy/build/layer.zip)" || fail "build the layer: make layer"
[ -f deploy/build/bundle/deploy/handler.py ] && pass "code bundle built (deploy/build/bundle)" || fail "build the code bundle: make bundle"
if find deploy/build/bundle -name '*.tfstate*' 2>/dev/null | grep -q .; then fail "a state file is inside the bundle: rebuild it"; fi

if git check-ignore -q deploy/terraform.tfstate 2>/dev/null; then pass "state files are git-ignored"; else fail "add deploy/*.tfstate* to .gitignore"; fi

echo "$ok passed, $bad to fix"
[ "$bad" -eq 0 ]
