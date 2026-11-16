"""API Gateway (HTTP API) Lambda authorizer, simple responses: a call that can change state or spend money
must carry the x-tower-key header, and the key must match the SecureString parameter Terraform created.

The gateway asks this function before it invokes the tower or an agent, so a refused call never reaches
Bedrock and costs nothing but this check. The comparison is constant-time; the key is read once per
container from SSM Parameter Store and never logged.
"""
from __future__ import annotations

import hmac
import json
import os

_KEY: str | None = None


def _key() -> str:
    global _KEY
    if _KEY is None:
        import boto3
        _KEY = boto3.client("ssm").get_parameter(Name=os.environ["TOWER_KEY_PARAM"], WithDecryption=True)["Parameter"]["Value"]
    return _KEY


def check(given: str, expected: str) -> bool:
    return bool(given) and bool(expected) and hmac.compare_digest(given.encode(), expected.encode())


def handler(event, context):
    given = (event.get("headers") or {}).get("x-tower-key", "")
    try:
        ok = check(given, _key())
    except Exception as e:                    # fail closed: no parameter, no access
        print(json.dumps({"audit": "authorizer.error", "error": type(e).__name__}))
        ok = False
    if not ok:
        rc = event.get("requestContext") or {}
        print(json.dumps({"audit": "authorizer.denied", "route": event.get("routeKey"), "ip": rc.get("http", {}).get("sourceIp")}))
    return {"isAuthorized": ok}
