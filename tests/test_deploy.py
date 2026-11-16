"""The live deployment, rehearsed offline: the key authorizer, the daily spend cap, the tower's wall clock,
the scheduled jobs, the lever audit, and the Terraform itself (parsed, then read for the rules we rely on)."""
from __future__ import annotations

import datetime as dt
import importlib
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def handler(monkeypatch, tmp_path):
    monkeypatch.setenv("FOUNDRY_SPEND", str(tmp_path / "spend.json"))
    monkeypatch.setenv("FOUNDRY_FAKE", "1")
    monkeypatch.delenv("FOUNDRY_PANEL_TABLE", raising=False)
    monkeypatch.delenv("FOUNDRY_DATA_BUCKET", raising=False)
    import deploy.handler as h
    return importlib.reload(h)


# ---------------------------------------------------------------- the key authorizer
def test_authorizer_compares_in_constant_time_and_fails_closed(monkeypatch):
    from deploy import authorizer as a
    assert a.check("k" * 32, "k" * 32) and not a.check("x" * 32, "k" * 32)
    assert not a.check("", "") and not a.check("", "k")
    monkeypatch.setattr(a, "_key", lambda: "secret")
    assert a.handler({"headers": {"x-tower-key": "secret"}}, None) == {"isAuthorized": True}
    assert a.handler({"headers": {"x-tower-key": "guess"}}, None) == {"isAuthorized": False}

    def broken():
        raise RuntimeError("no parameter")
    monkeypatch.setattr(a, "_key", broken)
    assert a.handler({"headers": {"x-tower-key": "secret"}}, None) == {"isAuthorized": False}


# ---------------------------------------------------------------- the daily spend cap
def test_spend_ledger_refuses_once_the_day_is_spent(tmp_path):
    from foundry.spend import Spend
    s = Spend(cap_usd=0.50, path=tmp_path / "s.json")
    assert s.allowed()[0]
    s.add(0.30)
    s.add(0.25)
    ok, why = s.allowed()
    assert not ok and "USD 0.55 of USD 0.50" in why


def test_dynamo_spend_uses_an_atomic_add():
    from foundry.spend import DynamoSpend

    class FakeDDB:
        def __init__(self):
            self.n, self.calls = 0.0, []

        def get_item(self, **kw):
            return {"Item": {"usd": {"N": str(self.n)}}} if self.n else {}

        def update_item(self, **kw):
            self.calls.append(kw)
            self.n += float(kw["ExpressionAttributeValues"][":u"]["N"])
            return {"Attributes": {"usd": {"N": str(self.n)}}}

    ddb = FakeDDB()
    s = DynamoSpend(cap_usd=1.0, table="t", client=ddb)
    assert s.add(0.4) == pytest.approx(0.4) and s.add(0.7) == pytest.approx(1.1)
    assert ddb.calls[0]["UpdateExpression"] == "ADD usd :u" and ddb.calls[0]["Key"]["panel_id"]["S"].startswith("spend#")
    assert not s.allowed()[0]


def test_handler_returns_429_when_the_day_is_spent(handler, monkeypatch):
    monkeypatch.setenv("FOUNDRY_DAILY_USD", "0")
    ev = {"rawPath": "/diagnose", "requestContext": {"http": {"method": "POST"}}, "body": json.dumps({"dc": "CHN", "week": 12})}
    r = handler.lambda_handler(ev, None)
    assert r["statusCode"] == 429 and "daily model budget" in json.loads(r["body"])["error"]


# ---------------------------------------------------------------- the wall clock
class _Prof:
    def __init__(self, tz, hour):
        self.tz, self.hour = tz, hour

    def section(self, name):
        return {"timezone": self.tz, "monday_run_hour_local": self.hour}


def test_tower_clock_follows_the_profile_and_summer_time(handler, monkeypatch):
    utc = dt.timezone.utc
    assert handler._hours_since_monday(dt.datetime(2026, 11, 10, 3, 0, tzinfo=utc)) == pytest.approx(26.0)   # GFB: Tue 08:30 IST
    monkeypatch.setattr(handler, "_PROFILE", _Prof("Europe/Amsterdam", 6.0))
    assert handler._hours_since_monday(dt.datetime(2027, 6, 28, 10, 0, tzinfo=utc)) == pytest.approx(6.0)    # CEST, UTC+2
    assert handler._hours_since_monday(dt.datetime(2027, 1, 4, 11, 0, tzinfo=utc)) == pytest.approx(6.0)     # CET, UTC+1


# ---------------------------------------------------------------- the scheduled jobs
def test_scheduler_runs_monday_then_ticks_the_sla_clock(handler, monkeypatch, tmp_path):
    from foundry.orchestrator import TowerRuntime
    box = {}

    def fresh():
        box["rt"] = TowerRuntime(handler._store(), handler._PROFILE, client_factory=lambda: handler._client("tower"), folder=tmp_path / "t")
        return box["rt"]
    monkeypatch.setattr(handler, "_tower_fresh", fresh)
    monkeypatch.setattr(handler, "_tower_load", lambda: box["rt"])
    monkeypatch.setattr(handler, "_tower_save", lambda rt: None)
    out = handler.tower_handler({"tower": "monday", "fresh": True}, None)
    assert out["ok"] and out["monday"] == 12 and out["events"] > 0
    monkeypatch.setattr(handler, "_hours_since_monday", lambda now=None: 13.0)     # Monday 19:30: past every P1 and P2 clock
    tick = handler.tower_handler({"tower": "tick"}, None)
    assert tick["ok"] and tick["tick"] == 13.0


def test_monday_is_skipped_when_the_day_is_spent(handler, monkeypatch):
    monkeypatch.setenv("FOUNDRY_DAILY_USD", "0")
    out = handler.tower_handler({"tower": "monday"}, None)
    assert out["ok"] is False and "daily model budget" in out["skipped"]


# ---------------------------------------------------------------- the lever audit
def test_a_panel_flipped_outside_the_code_is_logged(capsys):
    from foundry import levers_dynamo as L

    class FakeDDB:
        kill = False

        def get_item(self, **kw):
            return {"Item": {"panel_id": {"S": "audit-test"}, "kill_switch": {"BOOL": self.kill}, "propose_only": {"BOOL": True},
                             "disabled_tools": {"L": []}}}

    ddb = FakeDDB()
    lv = L.DynamoLevers(table="t", panel_id="audit-test", audit_path="/dev/null", client=ddb)
    lv.read_panel()
    ddb.kill = True                                    # someone flips it in the AWS console
    assert lv.read_panel()["kill_switch"] is True
    lines = [json.loads(x) for x in capsys.readouterr().out.splitlines() if x.startswith("{")]
    assert any(x["audit"] == "panel.changed" and x["after"]["kill_switch"] is True for x in lines)


# ---------------------------------------------------------------- the Terraform, read for the rules we rely on
def _tf():
    hcl2 = pytest.importorskip("hcl2")
    docs = {}
    for f in ("main.tf", "tower.tf"):
        with open(ROOT / "deploy" / f) as fh:
            docs[f] = hcl2.load(fh)
    return docs


def _resources(docs, kind):
    out = {}
    for d in docs.values():
        for block in d.get("resource", []):
            for k, v in block.items():
                if k.strip('"') == kind:
                    for name, body in v.items():
                        out[name.strip('"')] = body
    return out


def test_every_route_that_can_spend_or_change_needs_the_key():
    docs = _tf()
    routes = _resources(docs, "aws_apigatewayv2_route")
    assert set(routes) == {"agents", "health", "tower_read", "tower_write"}
    for name in ("agents", "tower_write"):
        assert routes[name]["authorization_type"].strip('"') == "CUSTOM"
    for name in ("health", "tower_read"):
        assert "authorization_type" not in routes[name]
    assert "POST" not in json.dumps(routes["tower_read"]) and "POST" not in json.dumps(routes["health"])


def test_the_lambda_policy_names_its_resources():
    text = (ROOT / "deploy" / "main.tf").read_text()
    assert 'Resource = "*"' not in text and "arn:aws:logs:*:*:*" not in text
    for sid in ("ProfileInRegion", "ModelInRegion", "ModelGlobal"):
        assert f'Sid = "{sid}"' in text
    assert '"aws:RequestedRegion" = "unspecified"' in text


def test_schedules_run_on_the_company_clock():
    docs = _tf()
    sched = _resources(docs, "aws_scheduler_schedule")
    assert set(sched) == {"monday", "tick"}
    for s in sched.values():
        assert "var.tower_timezone" in json.dumps(s["schedule_expression_timezone"])
    assert '"tower": "monday"' in json.dumps(sched["monday"]["target"]).replace('\\"', '"').replace("=", ":") or \
        "monday" in json.dumps(sched["monday"]["target"])


# ---------------------------------------------------------------- the bundle, the errors, the tower's switch
def test_the_bundle_is_a_whitelist_and_refuses_state_files(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("bundle", ROOT / "deploy" / "bundle.py")
    b = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(b)
    out = b.build(tmp_path / "bundle")
    assert (out / "deploy" / "handler.py").exists() and (out / "foundry" / "console" / "static" / "index.html").exists()
    assert not (out / "tests").exists() and not (out / "deploy" / "main.tf").exists()
    assert not any(p.name == "__pycache__" for p in out.rglob("*"))
    text = (ROOT / "deploy" / "main.tf").read_text()
    assert 'source_dir  = "${path.module}/build/bundle"' in text


def test_a_failed_scheduled_job_raises_and_logs_for_the_alarm(handler, monkeypatch, capsys):
    def boom():
        raise RuntimeError("state unreadable")
    monkeypatch.setattr(handler, "_tower_load", boom)
    with pytest.raises(RuntimeError):
        handler.tower_handler({"tower": "tick"}, None)
    assert any('"audit": "handler.error"' in x for x in capsys.readouterr().out.splitlines())


def test_scripted_runs_do_not_book_on_the_ledger(handler, monkeypatch, tmp_path):
    handler._book(0.43)                                       # FOUNDRY_FAKE=1 in the fixture
    from foundry.spend import Spend
    assert Spend().spent() == 0.0


def test_the_tower_reads_the_dynamo_panel_on_aws(monkeypatch, tmp_path):
    monkeypatch.setenv("FOUNDRY_PANEL_TABLE", "gfb-foundry-panel")
    from foundry.orchestrator import scenarios
    from foundry.levers_dynamo import DynamoLevers
    from foundry.orchestrator.runtime import TowerRuntime
    from foundry.data.sources import load_store
    from foundry.profile import load_profile
    rt = TowerRuntime(load_store("canon:"), load_profile("gfb"), client_factory=None, folder=tmp_path)
    assert isinstance(rt.levers, DynamoLevers)


def test_the_lock_outlives_the_timeout_and_only_its_owner_releases_it(handler):
    import re
    lock = handler._Lock()
    tf = (ROOT / "deploy" / "tower.tf").read_text()
    timeout = int(re.search(r"timeout\s+=\s+(\d+)", tf).group(1))
    assert lock.hold_s > timeout
    src = (ROOT / "deploy" / "handler.py").read_text()
    assert 'ConditionExpression="#o = :t"' in src


# ---------------------------------------------------------------- proposals outlive the container
class _ClientError(Exception):
    def __init__(self, response, op):
        super().__init__(op)
        self.response = response


class _S3:
    def __init__(self):
        self.objects = {}

    def put_object(self, Bucket, Key, Body, ContentType):
        self.objects[(Bucket, Key)] = Body

    def get_object(self, Bucket, Key):
        import io
        if (Bucket, Key) not in self.objects:
            raise _ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)])}


def _fake_aws(monkeypatch, tmp_path, s3):
    """boto3 is in the Lambda runtime, not in CI: stand in for the two calls the handler makes."""
    import sys
    import types
    monkeypatch.setitem(sys.modules, "boto3", types.SimpleNamespace(client=lambda name, **kw: s3))
    monkeypatch.setitem(sys.modules, "botocore", types.ModuleType("botocore"))
    monkeypatch.setitem(sys.modules, "botocore.exceptions", types.SimpleNamespace(ClientError=_ClientError))
    monkeypatch.setenv("FOUNDRY_DATA_BUCKET", "gfb-foundry-data-test")
    monkeypatch.setenv("FOUNDRY_PROPOSALS", str(tmp_path / "proposals.jsonl"))


def _post(path, body):
    return {"rawPath": path, "requestContext": {"http": {"method": "POST"}}, "body": json.dumps(body)}


def test_every_proposal_is_saved_to_s3_and_can_be_read_back(handler, monkeypatch, tmp_path):
    s3 = _S3()
    _fake_aws(monkeypatch, tmp_path, s3)
    body = json.loads(handler.lambda_handler(_post("/plan", {"dc": "CHN", "sku": "MN-1L", "week": 12, "kpi": "OTIF"}), None)["body"])
    assert body["proposal_id"] in body["saved"] and len(body["saved"]) >= 2      # the tower's plan and its specialists' proposals
    assert all(("gfb-foundry-data-test", f"proposals/{p}.json") in s3.objects for p in body["saved"])
    r = handler.lambda_handler(_post("/proposal", {"proposal_id": body["proposal_id"]}), None)
    assert r["statusCode"] == 200 and json.loads(r["body"])["proposal_id"] == body["proposal_id"]
    assert handler.lambda_handler(_post("/proposal", {"proposal_id": "PRP-12-CHN-000000"}), None)["statusCode"] == 404
    assert handler.lambda_handler(_post("/proposal", {"proposal_id": "../tower/state"}), None)["statusCode"] == 400


def test_a_failed_save_is_logged_for_the_alarm_and_the_answer_still_goes_back(handler, monkeypatch, tmp_path, capsys):
    class _Down(_S3):
        def put_object(self, **kw):
            raise RuntimeError("s3 is down")
    _fake_aws(monkeypatch, tmp_path, _Down())
    r = handler.lambda_handler(_post("/diagnose", {"dc": "CHN", "sku": "MN-1L", "week": 12, "kpi": "OTIF"}), None)
    out = capsys.readouterr().out
    assert r["statusCode"] == 200 and json.loads(r["body"])["saved"] == []
    assert '"audit": "handler.error"' in out and "proposals.save" in out


def test_the_proposals_grant_is_narrow():
    text = (ROOT / "deploy" / "main.tf").read_text()
    assert 'Sid = "Proposals", Effect = "Allow", Action = ["s3:PutObject"], Resource = "${aws_s3_bucket.data.arn}/proposals/*"' in text
    assert '"POST /proposal"' in text


# ---------------------------------------------------------------- console clicks run in the background
class _Lambda:
    def __init__(self):
        self.calls = []

    def invoke(self, FunctionName, InvocationType, Payload):
        self.calls.append({"fn": FunctionName, "type": InvocationType, "event": json.loads(Payload)})


def test_a_console_click_on_aws_is_queued_then_done(handler, monkeypatch, tmp_path):
    import sys
    import types
    from foundry.orchestrator import TowerRuntime
    s3, lam = _S3(), _Lambda()
    _fake_aws(monkeypatch, tmp_path, s3)
    monkeypatch.setitem(sys.modules, "boto3", types.SimpleNamespace(client=lambda name, **kw: lam if name == "lambda" else s3))
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "gfb-foundry-tower")
    rt = TowerRuntime(handler._store(), handler._PROFILE, client_factory=lambda: handler._client("tower"), folder=tmp_path / "t")
    monkeypatch.setattr(handler, "_tower_load", lambda: rt)
    monkeypatch.setattr(handler, "_tower_save", lambda r: None)
    click = {"rawPath": "/api/monday", "requestContext": {"http": {"method": "POST"}},
             "headers": {"x-tower-key": "k"}, "body": json.dumps({"week": 12})}
    r = handler.tower_handler(click, None)
    jid = json.loads(r["body"])["job_id"]
    assert r["statusCode"] == 202 and lam.calls[0]["type"] == "Event" and lam.calls[0]["fn"] == "gfb-foundry-tower"
    assert "k" not in json.dumps(lam.calls[0]["event"]["console_job"].get("headers", {}))   # the key is never stored
    poll = {"rawPath": f"/api/job/{jid}", "requestContext": {"http": {"method": "GET"}}}
    assert json.loads(handler.tower_handler(poll, None)["body"])["status"] == "QUEUED"
    assert handler.tower_handler(lam.calls[0]["event"], None) == {"ok": True, "code": 200}   # the second invocation
    done = json.loads(handler.tower_handler(poll, None)["body"])
    assert done["status"] == "DONE" and done["code"] == 200 and done["payload"]["events"]
    assert rt.plans                                                                 # the work happened
    bad = {"rawPath": "/api/job/../state", "requestContext": {"http": {"method": "GET"}}}
    assert handler.tower_handler(bad, None)["statusCode"] == 400


def test_a_queued_click_that_fails_says_so(handler, monkeypatch, tmp_path, capsys):
    s3 = _S3()
    _fake_aws(monkeypatch, tmp_path, s3)

    def boom():
        raise RuntimeError("state unreadable")
    monkeypatch.setattr(handler, "_tower_load", boom)
    jid = "0" * 32
    out = handler.tower_handler({"console_job": {"id": jid, "path": "/api/tick", "body": json.dumps({"at": 1})}}, None)
    rec = json.loads(s3.objects[("gfb-foundry-data-test", f"tower/jobs/{jid}.json")])
    assert out == {"ok": False} and rec["status"] == "FAILED" and "state unreadable" in rec["payload"]["error"]
    assert '"audit": "handler.error"' in capsys.readouterr().out


def test_locally_a_console_click_still_answers_at_once(handler, monkeypatch, tmp_path):
    from foundry.orchestrator import TowerRuntime
    monkeypatch.delenv("AWS_LAMBDA_FUNCTION_NAME", raising=False)
    rt = TowerRuntime(handler._store(), handler._PROFILE, client_factory=lambda: handler._client("tower"), folder=tmp_path / "t")
    monkeypatch.setattr(handler, "_tower_load", lambda: rt)
    monkeypatch.setattr(handler, "_tower_save", lambda r: None)
    r = handler.tower_handler({"rawPath": "/api/tick", "requestContext": {"http": {"method": "POST"}}, "body": json.dumps({"at": 1})}, None)
    assert r["statusCode"] == 200


def test_the_tower_may_invoke_only_itself():
    text = (ROOT / "deploy" / "tower.tf").read_text()
    assert 'Action = ["lambda:InvokeFunction"], Resource = aws_lambda_function.tower.arn' in text
    assert 'prefix = "tower/jobs/"' in (ROOT / "deploy" / "main.tf").read_text()
