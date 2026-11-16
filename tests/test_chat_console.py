"""Ask the tower and the console API."""
import pytest

from foundry.chat import agent as chat
from foundry.console.api import ConsoleAPI
from foundry.orchestrator import scenarios


@pytest.fixture(scope="module")
def week12(tmp_path_factory):
    rt, _ = scenarios.week12(tmp_path_factory.mktemp("w12"))
    return rt


def test_chat_explains_a_plan_with_its_ids(week12):
    r = chat.ask("Why is the tower moving stock into Chennai?", week12)
    assert r.stop_reason == "submitted" and "500 cases from KOL" in r.answer["text"]
    assert r.answer["cited"][0].startswith("PRP-12-CHN")


def test_chat_hands_data_questions_to_talk_to_data(week12):
    r = chat.ask("What was OTIF at Chennai for MN-1L in week 12?", week12)
    assert "0.6667" in r.answer["text"] and [c["tool"] for c in r.tool_calls] == ["ask_data", "answer"]


def test_chat_never_acts(week12):
    r = chat.ask("Approve the Chennai plan for me", week12)
    assert "can't" in r.answer["text"] and not any(c["tool"] == "approve" for c in r.tool_calls)


def test_console_routes(week12, monkeypatch):
    api = ConsoleAPI(week12)
    assert api.handle("GET", "/api/health")[0] == 200
    code, st = api.handle("GET", "/api/state")
    assert code == 200 and st["counts"]["APPLIED"] >= 4 and "radar_now" in st
    pid = next(iter(week12.plans))
    code, pl = api.handle("GET", f"/api/plan/{pid}")
    assert code == 200 and pl["trace"][0]["type"] == "schedule.monday"
    assert api.handle("GET", "/api/cell/12/CHN/MN-1L")[1]["risk"] in ("RED", "AMBER", "GREEN")
    assert api.handle("GET", "/api/nope")[0] == 404
    monkeypatch.setenv("FOUNDRY_CONSOLE_KEY", "secret")
    assert api.handle("POST", "/api/tick", body={"at": 99})[0] == 401
    code, out = api.handle("POST", "/api/tick", body={"at": 99}, headers={"X-Tower-Key": "secret"})
    assert code == 200 and out["accepted"]


def test_control_room_eval_gate_passes_offline(tmp_path):
    import json, subprocess, sys
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    out = tmp_path / "cr.json"
    res = subprocess.run([sys.executable, "evals/run_control_room_evals.py", "--out", str(out)], cwd=root, capture_output=True, text=True)
    assert res.returncode == 0, res.stdout[-800:]
    s = json.loads(out.read_text())["summary"]
    assert s["passed"] == s["cases"] == 38 and s["refusals_hold"]
