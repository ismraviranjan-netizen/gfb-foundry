"""AWS Lambda entry point: one function, one route per agent.

  POST /diagnose  {"dc": "CHN", "sku": "MN-1L", "week": 12, "kpi": "OTIF"}      Part 1
  POST /sweep     {"week": 12}                                                   Part 1
  POST /rebalance {"sku": "MN-1L", "week": 12, "to_dc": "CHN"} | {"sweep": true, "week": 12}   Part 2
  POST /sense     {"dc": "CHN", "sku": "MN-1L", "origin_week": 16, "horizon": 2} Part 3 (data: canon:sensing or the company's history)
  POST /queue     {"week": 12, "now_hours": 5, "digest": true}                   Part 4
  POST /plan      {"dc": "CHN", "sku": "MN-1L", "week": 12, "kpi": "OTIF"} | {"sweep": true, "week": 12}   Part 5
  POST /proposal  {"proposal_id": "PRP-12-CHN-1a2b3c"}   read back a saved proposal
  GET  /health

Proposals: every proposal a call makes is saved to s3://FOUNDRY_DATA_BUCKET/proposals/<id>.json before the
answer goes back (the /tmp file dies with the container); the answer lists them under "saved".

Data: FOUNDRY_DATA_URI (canon: by default; s3://bucket/prefix for a company's CSVs + network.yaml), copied
to /tmp on a cold start. Levers: the DynamoDB panel when FOUNDRY_PANEL_TABLE is set, else /tmp/panel.json.
FOUNDRY_FAKE=1 runs every agent on its scripted brain (the smoke test).
"""
from __future__ import annotations

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from foundry.data.sources import load_store                  # noqa: E402
from foundry.profile import load_profile                     # noqa: E402
from foundry.levers import Levers                            # noqa: E402
from foundry.approval import ProposalStore                   # noqa: E402

os.environ.setdefault("FOUNDRY_PANEL", "/tmp/panel.json")
os.environ.setdefault("FOUNDRY_AUDIT", "/tmp/audit.jsonl")
os.environ.setdefault("FOUNDRY_PROPOSALS", "/tmp/proposals.jsonl")
os.environ.setdefault("FOUNDRY_RUNS", "/tmp/runs.jsonl")
os.environ.setdefault("FOUNDRY_QUEUE_DIR", "/tmp")
os.environ.setdefault("FOUNDRY_SPEND", "/tmp/spend.json")
if os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
    os.environ.setdefault("FOUNDRY_AUDIT_STDOUT", "1")       # lever decisions reach CloudWatch, not only /tmp

_PROFILE = load_profile(os.environ.get("FOUNDRY_PROFILE", "gfb"))
_STORES: dict = {}


def _store(kind: str = "incident"):
    """The incident store (FOUNDRY_DATA_URI) or the sensing history (FOUNDRY_SENSING_URI), cached per container."""
    if kind not in _STORES:
        uri = os.environ.get("FOUNDRY_SENSING_URI", "canon:sensing") if kind == "sensing" else os.environ.get("FOUNDRY_DATA_URI", "canon:")
        _STORES[kind] = load_store(uri)
    return _STORES[kind]


def _levers() -> Levers:
    if os.environ.get("FOUNDRY_PANEL_TABLE"):
        from foundry.levers_dynamo import DynamoLevers
        return DynamoLevers(audit_path=os.environ["FOUNDRY_AUDIT"])
    return Levers()


def _spend():
    from foundry.spend import ledger
    return ledger()


def _book(cost_usd: float) -> None:
    """Book a run's cost on today's ledger; a failed write is logged, never raised to the caller. The
    scripted brain's meter is an estimate of what a model would have cost, not a bill: it is not booked."""
    if os.environ.get("FOUNDRY_FAKE", "0") == "1":
        return
    try:
        if cost_usd:
            _spend().add(float(cost_usd))
    except Exception as e:
        print(json.dumps({"audit": "spend.book_failed", "error": str(e), "cost_usd": cost_usd}))


def _client(agent: str):
    if os.environ.get("FOUNDRY_FAKE", "0") == "1":
        if agent == "rca":
            from foundry.bedrock_client import FakeBedrockClient
            return FakeBedrockClient()
        if agent == "rebalancing":
            from foundry.agents.rebalancing.fake import FakeRebalancingClient
            return FakeRebalancingClient()
        if agent == "sensing":
            from foundry.agents.sensing.fake import FakeSensingClient
            return FakeSensingClient()
        if agent == "queue":
            from foundry.agents.rca_exception.queue_fake import FakeQueueClient
            return FakeQueueClient()
        from foundry.tower.fake import FakeTowerClient
        return FakeTowerClient()
    from foundry.bedrock_client import BedrockClient
    return BedrockClient()


def lambda_handler(event, context):
    method = (event.get("requestContext", {}).get("http", {}).get("method") or event.get("httpMethod") or "POST").upper()
    path = (event.get("rawPath") or event.get("path") or "/diagnose").rstrip("/")
    if path.endswith("/health"):
        return _resp(200, {"ok": True, "profile": _PROFILE.company, "data": os.environ.get("FOUNDRY_DATA_URI", "canon:"), "fake": os.environ.get("FOUNDRY_FAKE", "0") == "1"})
    if method != "POST":
        return _resp(405, {"error": "POST only"})
    body = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        import base64
        body = base64.b64decode(body).decode()
    try:
        req = json.loads(body)
    except json.JSONDecodeError:
        return _resp(400, {"error": "body must be JSON"})
    if path.endswith("/proposal"):           # a read: no model, so the day's cap does not apply
        return _read_proposal(str(req.get("proposal_id", "")))
    ps = ProposalStore()
    mark = len(ps._all())                    # rows after this mark are this call's proposals
    budget = float(os.environ.get("FOUNDRY_BUDGET_USD", "0.25"))
    ok, why = _spend().allowed()            # the day's cap, checked before anything can reach Bedrock
    if not ok:
        return _resp(429, {"error": why})
    week = int(req.get("week", 12))
    try:
        if path.endswith("/sweep") or path.endswith("/diagnose"):
            from foundry.agents.rca_exception import agent
            if path.endswith("/diagnose") and "dc" not in req:
                return _resp(400, {"error": "dc is required"})
            task = {"mode": "sweep", "week": week} if path.endswith("/sweep") else \
                   {"mode": "diagnose", "dc": req["dc"], "sku": req.get("sku"), "week": week, "kpi": req.get("kpi", "OTIF")}
            res = agent.run(task, _store(), _PROFILE, _client("rca"), _levers(), ps, budget_usd=budget)
            return _done(res, "diagnosis", res.diagnosis, ps=ps, mark=mark)
        if path.endswith("/rebalance"):
            from foundry.agents.rebalancing import agent
            if not req.get("sweep") and "sku" not in req:
                return _resp(400, {"error": "sku is required"})
            task = {"mode": "sweep", "week": week} if req.get("sweep") else {"mode": "transfer", "sku": req["sku"], "week": week, "to_dc": req.get("to_dc")}
            res = agent.run(task, _store(), _PROFILE, _client("rebalancing"), _levers(), ps, budget_usd=budget)
            return _done(res, "plan", res.plan, ps=ps, mark=mark)
        if path.endswith("/sense"):
            from foundry.agents.sensing import agent
            if not req.get("sweep") and ("dc" not in req or "sku" not in req):
                return _resp(400, {"error": "dc and sku are required"})
            origin = int(req.get("origin_week", 16))
            task = {"mode": "sweep", "origin_week": origin, "horizon": int(req.get("horizon", 2))} if req.get("sweep") else \
                   {"mode": "sense", "dc": req["dc"], "sku": req["sku"], "origin_week": origin, "horizon": int(req.get("horizon", 2))}
            res = agent.run(task, _store("sensing"), _PROFILE, _client("sensing"), _levers(), ps, budget_usd=budget)
            return _done(res, "sensing", res.sensing, ps=ps, mark=mark)
        if path.endswith("/queue"):
            from foundry.agents.rca_exception import queue as Q, queue_agent
            st = _store()
            qu = Q.load_or_build(st, _PROFILE, week, proposals=ps)
            now = float(req.get("now_hours", 0))
            ticked = Q.tick(qu, _PROFILE, now) if req.get("tick") else None
            if ticked:
                Q.to_json(qu, Q.queue_path(week))
            if req.get("digest"):
                res = queue_agent.run({"mode": "digest", "week": week, "now_hours": now}, st, _PROFILE, _client("queue"), _levers(), queue=qu, budget_usd=budget)
                return _done(res, "digest", res.digest, extra={"tick": ticked}, ps=ps, mark=mark)
            return _resp(200, {"week": week, "now_hours": now, "items": qu.table(now), "summary": Q.digest(qu, now), "tick": ticked})
        if path.endswith("/plan"):
            from foundry.tower import agent
            if not req.get("sweep") and "dc" not in req:
                return _resp(400, {"error": "dc is required"})
            task = {"mode": "sweep", "week": week} if req.get("sweep") else \
                   {"mode": "breach", "dc": req["dc"], "sku": req.get("sku"), "week": week, "kpi": req.get("kpi", "OTIF")}
            res = agent.run(task, _store(), _PROFILE, _client("tower"), _levers(), ps, budget_usd=max(budget, 0.6))
            return _done(res, "plan", res.plan, extra={"specialists": res.specialists, "total_meter": res.total_meter}, ps=ps, mark=mark)
        return _resp(404, {"error": f"no route {path}"})
    except Exception as e:  # never a stack trace to the caller; the log line feeds the handled-errors alarm
        _log_error("lambda_handler", path, e)
        return _resp(502, {"error": str(e)})


def _log_error(where: str, what: str, e: Exception) -> None:
    """One JSON line that the log metric filter counts (main.tf: aws_cloudwatch_log_metric_filter.handled)."""
    import traceback
    print(json.dumps({"audit": "handler.error", "where": where, "what": what, "error": f"{type(e).__name__}: {e}",
                      "trace": traceback.format_exc()[-1500:]}))


def _done(res, key: str, payload, extra: dict | None = None, ps: ProposalStore | None = None, mark: int = 0) -> dict:
    _book((res.meter or {}).get("cost_usd", 0.0))
    code = 200 if res.stop_reason == "submitted" else 502
    body = {"run_id": res.run_id, "stop_reason": res.stop_reason, key: payload, "proposal_id": getattr(res, "proposal_id", None),
            "meter": res.meter, "elapsed_s": res.elapsed_s, "error": res.error}
    body.update(extra or {})
    if ps is not None:
        body["saved"] = _save_proposals(ps, mark)
    return _resp(code, body)


_PROPOSAL_ID = re.compile(r"^PRP-[A-Za-z0-9-]{1,60}$")


def _save_proposals(ps: ProposalStore, mark: int) -> list[str]:
    """Copy this call's proposals (rows after `mark`, the latest row of each) to S3, one
    object each, so a person can still sign them after the container is gone. A failed write
    is logged for the handled-errors alarm and the id left out of the answer's "saved" list;
    the answer itself still goes back."""
    latest = {r["proposal_id"]: r for r in ps._all()[mark:]}
    bucket = os.environ.get("FOUNDRY_DATA_BUCKET")
    if not bucket:
        return sorted(latest)                # local run: the jsonl file is the record
    import boto3
    s3, saved = boto3.client("s3"), []
    for pid, row in latest.items():
        try:
            s3.put_object(Bucket=bucket, Key=f"proposals/{pid}.json",
                          Body=json.dumps(row, default=str).encode(),
                          ContentType="application/json")
            saved.append(pid)
        except Exception as e:
            _log_error("proposals.save", pid, e)
    return sorted(saved)


def _read_proposal(pid: str) -> dict:
    if not _PROPOSAL_ID.match(pid):
        return _resp(400, {"error": "proposal_id like PRP-12-CHN-1a2b3c is required"})
    bucket = os.environ.get("FOUNDRY_DATA_BUCKET")
    if not bucket:
        row = ProposalStore().get(pid)
        return _resp(200, row) if row else _resp(404, {"error": f"no proposal {pid}"})
    import boto3
    from botocore.exceptions import ClientError
    try:
        obj = boto3.client("s3").get_object(Bucket=bucket, Key=f"proposals/{pid}.json")
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") in ("NoSuchKey", "404", "AccessDenied"):
            return _resp(404, {"error": f"no proposal {pid}"})
        _log_error("proposals.read", pid, e)
        return _resp(502, {"error": "could not read the proposal"})
    return _resp(200, json.loads(obj["Body"].read()))


def _resp(code: int, body: dict) -> dict:
    return {"statusCode": code, "headers": {"content-type": "application/json"}, "body": json.dumps(body, default=str)}


# --- The Control Room: the tower runtime behind the console, the Monday cron and the carrier feed --------
# A second function (gfb-foundry-tower) runs this entry point. The runtime's state is one JSON document in
# S3 (tower/state.json), read at the start of a call and written at the end, so only one writer may run at a
# time: every write takes the DynamoDB lock "lock#tower" first (_Lock below). A call that cannot get it in
# 20 s answers 409 (HTTP) or raises, so the Scheduler or EventBridge retries.
_STATE_KEY = os.environ.get("FOUNDRY_TOWER_STATE_KEY", "tower/state.json")
_CARRIER_EVENTS = {"eta.updated", "lane.delayed", "transfer.delayed", "snapshot.refreshed"}


class Busy(Exception):
    """Another call holds the tower: the caller retries (HTTP 409; the Scheduler retries on its own)."""


class _Lock:
    """One writer at a time without reserved concurrency: a conditional put on "lock#tower" in the panel
    table, with an expiry so a crashed call cannot hold it for ever. Locally (no table) it is a no-op."""

    def __init__(self, wait_s: float = 20.0, hold_s: int = 960):     # longer than the tower's 900 s timeout
        self.table = os.environ.get("FOUNDRY_PANEL_TABLE")
        self.wait_s, self.hold_s = wait_s, hold_s

    def __enter__(self):
        if not self.table:
            return self
        import time
        import boto3
        from botocore.exceptions import ClientError
        self.ddb = boto3.client("dynamodb")
        deadline = time.time() + self.wait_s
        while True:
            now = int(time.time())
            try:
                import uuid
                self.token = uuid.uuid4().hex                       # whose lock it is: only the owner releases it
                self.ddb.put_item(TableName=self.table, Item={"panel_id": {"S": "lock#tower"}, "expires": {"N": str(now + self.hold_s)},
                                                              "owner": {"S": self.token}},
                                  ConditionExpression="attribute_not_exists(panel_id) OR expires < :now",
                                  ExpressionAttributeValues={":now": {"N": str(now)}})
                return self
            except ClientError as e:
                if e.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
                    raise
                if time.time() > deadline:
                    raise Busy("the tower is busy with another call; try again in a moment")
                time.sleep(0.5)

    def __exit__(self, *exc):
        if self.table:
            try:
                self.ddb.delete_item(TableName=self.table, Key={"panel_id": {"S": "lock#tower"}},
                                     ConditionExpression="#o = :t", ExpressionAttributeNames={"#o": "owner"},
                                     ExpressionAttributeValues={":t": {"S": self.token}})
            except Exception:
                pass                         # the expiry frees it
        return False


def _tower_load():
    from foundry.orchestrator import TowerRuntime
    folder = "/tmp/tower"
    os.makedirs(folder, exist_ok=True)
    bucket = os.environ.get("FOUNDRY_DATA_BUCKET")
    local = os.path.join(folder, "state.json")
    if bucket:
        import boto3
        try:
            boto3.client("s3").download_file(bucket, _STATE_KEY, local)
        except Exception:                     # first call: no state yet
            if os.path.exists(local):
                os.remove(local)
    factory = (lambda: _client("tower"))
    if os.path.exists(local):
        return TowerRuntime.load(local, client_factory=factory, folder=folder)
    return TowerRuntime(_store(), _PROFILE, client_factory=factory, folder=folder)


def _tower_save(rt) -> None:
    path = rt.save()
    bucket = os.environ.get("FOUNDRY_DATA_BUCKET")
    if bucket:
        import boto3
        boto3.client("s3").upload_file(str(path), bucket, _STATE_KEY)


def _tower_clock():
    """The profile's wall clock: its time zone and the Monday run hour (GFB: Asia/Kolkata, 06:30)."""
    import datetime as dt
    tcfg = _PROFILE.section("tower") if hasattr(_PROFILE, "section") else {}
    name = tcfg.get("timezone", "Asia/Kolkata")
    hour = float(tcfg.get("monday_run_hour_local", tcfg.get("monday_run_hour_ist", 6.5)))
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(name)                  # tzdata ships in the layer; summer time is handled for Europe
    except Exception:
        tz = dt.timezone(dt.timedelta(hours=5, minutes=30)) if name in ("Asia/Kolkata", "IST") else dt.timezone.utc
    return tz, hour


def _hours_since_monday(now=None) -> float:
    """The tower's clock from the wall clock: hours since this week's Monday run, in the profile's time zone."""
    import datetime as dt
    tz, hour = _tower_clock()
    now = (now or dt.datetime.now(dt.timezone.utc)).astimezone(tz)
    h, m = int(hour), int(round((hour % 1) * 60))
    monday = (now - dt.timedelta(days=now.weekday())).replace(hour=h, minute=m, second=0, microsecond=0)
    return max(0.0, (now - monday).total_seconds() / 3600)


def _tower_fresh():
    """A new week: keep last week's state under tower/archive/, then start an empty runtime."""
    from foundry.orchestrator import TowerRuntime
    import datetime as dt
    bucket = os.environ.get("FOUNDRY_DATA_BUCKET")
    if bucket:
        import boto3
        s3 = boto3.client("s3")
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H%M")
        try:
            s3.copy_object(Bucket=bucket, Key=f"tower/archive/{stamp}.json", CopySource={"Bucket": bucket, "Key": _STATE_KEY})
        except Exception:
            pass                             # no state yet: nothing to keep
    folder = "/tmp/tower"
    os.makedirs(folder, exist_ok=True)
    local = os.path.join(folder, "state.json")
    if os.path.exists(local):
        os.remove(local)
    return TowerRuntime(_store(), _PROFILE, client_factory=(lambda: _client("tower")), folder=folder)


def _cost_usd(rt) -> float:
    from foundry.bedrock_client import INR_PER_USD
    return rt.cost_inr() / INR_PER_USD


def tower_handler(event, context):
    if event.get("console_job"):              # a console click, queued by the call below: run it under the lock
        return _console_job(event["console_job"])
    method = (event.get("requestContext", {}).get("http", {}).get("method") or "").upper()
    if method == "GET":
        return _tower_work(event)            # the page and the reads never write the state: no lock
    if method == "POST" and _async_console() and (event.get("rawPath") or "").startswith("/api/"):
        return _console_enqueue(event)       # API Gateway waits 30 s at most; a live Monday takes minutes
    try:
        with _Lock():
            return _tower_work(event)
    except Busy as e:
        if event.get("tower") or event.get("detail-type"):
            raise                            # Scheduler and EventBridge retry a failed invocation
        return _resp(409, {"error": str(e)})


# --- console clicks run in the background --------------------------------------------------------------
# An HTTP API integration gives up after 30 s, and a click that re-plans a week makes dozens of model
# calls. So on Lambda a console POST is queued: the call writes tower/jobs/<id>.json (QUEUED), invokes this
# function again asynchronously with {"console_job": ...}, and answers 202 at once. The second invocation
# takes the lock, does the work, saves the state and writes the job's answer (DONE or FAILED). The page
# polls GET /api/job/<id>. The key is checked by the authorizer before the 202 and is never stored.
_JOB_ID = re.compile(r"^[0-9a-f]{32}$")


def _async_console() -> bool:
    on_lambda = bool(os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))
    return on_lambda and os.environ.get("FOUNDRY_ASYNC_CONSOLE", "1") == "1"


def _job_write(jid: str, rec: dict) -> None:
    bucket = os.environ.get("FOUNDRY_DATA_BUCKET")
    data = json.dumps(rec, default=str).encode()
    if bucket:
        import boto3
        s3 = boto3.client("s3")
        s3.put_object(Bucket=bucket, Key=f"tower/jobs/{jid}.json", Body=data,
                      ContentType="application/json")
        return
    os.makedirs("/tmp/tower/jobs", exist_ok=True)
    with open(f"/tmp/tower/jobs/{jid}.json", "wb") as f:
        f.write(data)


def _job_read(jid: str) -> dict:
    if not _JOB_ID.match(jid):
        return _resp(400, {"error": "a job id is 32 hex characters"})
    bucket = os.environ.get("FOUNDRY_DATA_BUCKET")
    try:
        if bucket:
            import boto3
            obj = boto3.client("s3").get_object(Bucket=bucket, Key=f"tower/jobs/{jid}.json")
            raw = obj["Body"].read()
        else:
            with open(f"/tmp/tower/jobs/{jid}.json", "rb") as f:
                raw = f.read()
    except Exception:
        return _resp(404, {"error": f"no job {jid} (yet)"})
    return _resp(200, json.loads(raw))


def _console_enqueue(event) -> dict:
    import time
    import uuid
    ok, why = _spend().allowed()
    if not ok:
        return _resp(429, {"error": why})
    body = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        import base64
        body = base64.b64decode(body).decode()
    try:
        json.loads(body)
    except json.JSONDecodeError:
        return _resp(400, {"error": "body must be JSON"})
    jid = uuid.uuid4().hex
    path = event.get("rawPath") or "/api/"
    job = {"id": jid, "path": path, "query": event.get("queryStringParameters") or {},
           "body": body}                         # no headers: the key is never stored
    try:
        import boto3
        _job_write(jid, {"id": jid, "status": "QUEUED", "path": path,
                         "queued_at": time.time()})
        boto3.client("lambda").invoke(FunctionName=os.environ["AWS_LAMBDA_FUNCTION_NAME"],
                                      InvocationType="Event",
                                      Payload=json.dumps({"console_job": job}).encode())
    except Exception as e:
        _log_error("console_enqueue", path, e)
        return _resp(502, {"error": "could not queue the request"})
    return _resp(202, {"job_id": jid, "status": "QUEUED", "poll": f"/api/job/{jid}"})


def _console_job(job: dict) -> dict:
    import time
    from foundry.console.api import ConsoleAPI
    jid = str(job.get("id", ""))
    if not _JOB_ID.match(jid):
        return {"ok": False, "error": "bad job id"}
    try:
        with _Lock():
            rt = _tower_load()
            before = _cost_usd(rt)
            body = json.loads(job.get("body") or "{}")
            api = ConsoleAPI(rt)
            query = job.get("query") or {}
            code, payload = api.handle("POST", job["path"], query, body, {})
            if code == 200:
                _tower_save(rt)
                _book(_cost_usd(rt) - before)
    except Busy:
        _job_write(jid, {"id": jid, "status": "RETRYING", "path": job.get("path"),
                         "at": time.time()})
        raise                          # Lambda retries an asynchronous invocation twice
    except Exception as e:
        _log_error("console_job", job.get("path", "?"), e)
        _job_write(jid, {"id": jid, "status": "FAILED", "code": 502,
                         "path": job.get("path"),
                         "payload": {"error": f"{type(e).__name__}: {e}"},
                         "at": time.time()})
        return {"ok": False}
    _job_write(jid, {"id": jid, "status": "DONE", "code": code, "path": job.get("path"),
                     "payload": payload, "at": time.time()})
    return {"ok": True, "code": code}


def _tower_work(event):
    from foundry.console.api import ConsoleAPI
    try:
        job = event.get("tower")                       # EventBridge Scheduler sends {"tower": "monday"} or {"tower": "tick"}
        if job == "tick":                              # the SLA clock: reminders and escalations happen on wall-clock time
            rt = _tower_load()
            before = _cost_usd(rt)
            out = rt.tick(max(rt.now, _hours_since_monday()))
            _tower_save(rt)
            _book(_cost_usd(rt) - before)
            return {"ok": True, "tick": round(rt.now, 2), "events": len(out["events"])}
        if job == "monday" or event.get("source") == "aws.events" or event.get("detail-type") == "Scheduled Event":
            ok, why = _spend().allowed()
            if not ok:
                print(json.dumps({"audit": "monday.skipped", "reason": why}))
                return {"ok": False, "skipped": why}
            rt = _tower_fresh() if event.get("fresh", True) else _tower_load()
            week = int(os.environ.get("FOUNDRY_TOWER_WEEK", "12"))
            out = rt.monday(week, at=max(rt.now, _hours_since_monday()) if not event.get("fresh", True) else 0.0)
            _tower_save(rt)
            _book(_cost_usd(rt))
            return {"ok": True, "monday": week, "events": len(out["events"])}
        if event.get("detail-type") in _CARRIER_EVENTS:
            ok, why = _spend().allowed()
            if not ok:
                return {"ok": False, "skipped": why}
            rt = _tower_load()
            before = _cost_usd(rt)
            d, kind, at = event.get("detail") or {}, event["detail-type"], max(rt.now, _hours_since_monday())
            if kind == "eta.updated":
                out = rt.eta(int(d["week"]), d["dc"], d["sku"], int(d["eta_days"]), d.get("carrier", ""), d.get("truck_id", ""), at=at)
            elif kind == "lane.delayed":
                out = rt.lane_delay(int(d["week"]), d["lane_from"], d["lane_to"], int(d["delay_days"]), d.get("reason", ""), d.get("carrier", ""),
                                    d.get("applies_to", "all"), at=at)
            elif kind == "transfer.delayed":
                out = rt.transfer_delay(d["transfer_id"], int(d["lands_day"]), d.get("reason", ""), at=at)
            else:
                out = rt.refresh(int(d["week"]), d["dc"], d["sku"], d.get("by", ""), at=at)
            _tower_save(rt)
            _book(_cost_usd(rt) - before)
            return {"ok": True, "accepted": out["accepted"], "events": len(out["events"])}
        method = (event.get("requestContext", {}).get("http", {}).get("method") or "GET").upper()
        path = event.get("rawPath") or "/"
        if method == "GET" and path.startswith("/api/job/"):
            return _job_read(path.rstrip("/").rsplit("/", 1)[-1])
        if method == "GET" and not path.startswith("/api/"):
            page = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "foundry", "console", "static", "index.html")
            with open(page) as f:
                return {"statusCode": 200, "headers": {"content-type": "text/html; charset=utf-8"}, "body": f.read()}
        body = event.get("body") or "{}"
        if event.get("isBase64Encoded"):
            import base64
            body = base64.b64decode(body).decode()
        if method == "POST":
            ok, why = _spend().allowed()
            if not ok:
                return _resp(429, {"error": why})
        rt = _tower_load()
        before = _cost_usd(rt)
        code, payload = ConsoleAPI(rt).handle(method, path, event.get("queryStringParameters") or {},
                                              json.loads(body) if method == "POST" else None, event.get("headers") or {})
        if method == "POST" and code == 200:
            _tower_save(rt)
            _book(_cost_usd(rt) - before)
        return _resp(code, payload)
    except Exception as e:      # never a stack trace to the caller
        _log_error("tower_handler", event.get("tower") or event.get("detail-type") or event.get("rawPath") or "?", e)
        if event.get("tower") or event.get("detail-type"):
            raise                   # a scheduled job or a feed has no caller to read a 502: fail loudly, let it retry
        return _resp(502, {"error": f"{type(e).__name__}: {e}"})
