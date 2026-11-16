"""The Decision Lab: every lab's main() runs, prints its decision, and its key numbers hold.

The labs use numpy and pandas (labs/decision only). CI installs both and sets FOUNDRY_LABS_REQUIRED=1,
so there a missing library fails the run instead of skipping it; on a laptop without them the labs skip
and the stdlib-only core.py tests still run. Each lab's run() is computed once per module (a fixture);
main() is then run against that cached result, so the whole file takes well under a minute.
"""
from __future__ import annotations

import importlib
import importlib.util
import math
import os

import pytest

from labs.decision import core

HAVE_NP = all(importlib.util.find_spec(m) is not None for m in ("numpy", "pandas"))
if os.environ.get("FOUNDRY_LABS_REQUIRED") == "1" and not HAVE_NP:
    raise RuntimeError("FOUNDRY_LABS_REQUIRED=1 but numpy or pandas is missing: the lab tests would only skip")
needs_np = pytest.mark.skipif(not HAVE_NP, reason="the labs need numpy and pandas")
WIDE = 92        # the book's code width: labs 10-15 print nothing wider


# --- core.py (stdlib only) ----------------------------------------------------------------------
def test_core_quantile_and_pinball():
    xs = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert core.quantile(xs, 0.5) == 3.0
    assert core.quantile(xs, 0.9) == pytest.approx(4.6)
    assert core.pinball(10.0, 8.0, 0.9) == pytest.approx(1.8)       # under by 2 at tau 0.9
    assert core.pinball(10.0, 12.0, 0.9) == pytest.approx(0.2)      # over by 2 costs 0.1 a case


def test_core_conformal_and_newsvendor():
    assert core.conformal_q([0.1, 0.2, 0.3], 0.2) == math.inf       # n = 3 is too few to promise 80%
    assert core.conformal_q([i / 10 for i in range(1, 10)], 0.2) == pytest.approx(0.8)
    assert core.critical_ratio(62.0, 1.84) == pytest.approx(0.971, abs=1e-3)
    assert core.normal_stock(2600, 450, 0.95) == pytest.approx(3340.2, abs=0.5)
    assert core.coverage([1, 5, 9], [0, 4, 10], [2, 6, 11]) == pytest.approx(2 / 3)


# --- helpers ------------------------------------------------------------------------------------
def lab(name: str):
    return importlib.import_module(f"labs.decision.{name}")


def run_main(mod, capsys, monkeypatch, cached: dict, check_width: bool = False) -> str:
    """main() on cached results: every name in `cached` (run, run_long) is swapped for its result."""
    for fn, res in cached.items():
        monkeypatch.setattr(mod, fn, lambda *a, _r=res, **k: _r)
    mod.main()
    out = capsys.readouterr().out
    assert "Decision" in out
    if check_width:
        wide = [ln for ln in out.splitlines() if len(ln) > WIDE]
        assert not wide, wide
    return out


# --- Part I ---------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def r01():
    return lab("lab01_quantiles").run()


@needs_np
def test_lab01(r01, capsys, monkeypatch):
    assert r01["master"][0.9] == pytest.approx(3176.7, abs=1)
    assert r01["pooled_ratio"][0.1] == pytest.approx(0.793, abs=1e-3)
    assert (r01["boot_p90_lo"], r01["boot_p90_hi"]) == pytest.approx((3005, 3633), abs=5)
    run_main(lab("lab01_quantiles"), capsys, monkeypatch, {"run": r01})


@pytest.fixture(scope="module")
def r02():
    return lab("lab02_scoring").run()


@needs_np
def test_lab02(r02, capsys, monkeypatch):
    by = {x["name"]: x for x in r02["results"]}
    assert by["agent + normal"]["qloss_pct"] == pytest.approx(4.32, abs=0.01)
    assert by["consensus + normal"]["qloss_pct"] == pytest.approx(4.82, abs=0.01)
    assert by["agent, too wide"]["coverage"] == pytest.approx(0.969, abs=0.002)
    run_main(lab("lab02_scoring"), capsys, monkeypatch, {"run": r02})


@pytest.fixture(scope="module")
def r03():
    return lab("lab03_conformal").run()


@needs_np
def test_lab03(r03, capsys, monkeypatch):
    assert r03["results"][-1]["coverage"] == pytest.approx(0.800, abs=0.002)
    p = r03["pongal"]
    assert p["sensed"] == pytest.approx(4270.3, abs=1)
    assert (p["clean_band"]["low"], p["clean_band"]["high"]) == pytest.approx((1708, 6833), abs=2)
    run_main(lab("lab03_conformal"), capsys, monkeypatch, {"run": r03})


@needs_np
def test_lab04(capsys, monkeypatch):
    r = lab("lab04_newsvendor").run()
    g = r["rows"][0]
    assert (g["cr"], g["stock"]) == (0.971, 3454)
    assert r["rows"][3]["cr"] == pytest.approx(0.364, abs=1e-3)
    assert r["chn_rop"] == r["chn_rop_network"] == 6247
    run_main(lab("lab04_newsvendor"), capsys, monkeypatch, {"run": r})


# --- Part II --------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def r05():
    m = lab("lab05_gbm")
    return {"run": m.run(), "run_long": m.run_long()}


@needs_np
def test_lab05(r05, capsys, monkeypatch):
    a, b = r05["run"]["metrics"], r05["run_long"]["metrics"]
    assert a["consensus"]["wape"] == pytest.approx(13.97, abs=0.02)
    assert a["gbm_hybrid"]["wape"] == pytest.approx(14.15, abs=0.05)
    assert b["gbm"]["all"] == pytest.approx(15.28, abs=0.05)
    assert b["seasonal naive"]["promo"] == pytest.approx(13.43, abs=0.02)
    run_main(lab("lab05_gbm"), capsys, monkeypatch, r05)


@pytest.fixture(scope="module")
def r06():
    return lab("lab06_backtest").run()


@needs_np
def test_lab06(r06, capsys, monkeypatch):
    h = r06["metrics"]["GBM hybrid"]
    assert h["wape"] == pytest.approx(14.32, abs=0.05)
    assert h["fva_consensus"] == pytest.approx(5.10, abs=0.05)
    assert r06["metrics"]["sensing agent"]["wape"] == pytest.approx(14.80, abs=0.02)
    assert 29 <= r06["wins_hybrid"]["wins"] <= 31     # 30 on numpy 2.2/pandas 2.2; 31 on pandas 3.1
    assert r06["intervals"]["cqr_cover"] == pytest.approx(0.791, abs=0.005)
    run_main(lab("lab06_backtest"), capsys, monkeypatch, {"run": r06})


@needs_np
def test_lab07(r06, capsys, monkeypatch):
    r = lab("lab07_foundation").run()          # reuses lab 6's forecasts (cached in the process)
    assert (r["n"], r["n_series"], r["bytes"]) == (1280, 40, 41509)
    sc = {x["name"].split(" (")[0]: x for x in r["scorecard"]}
    assert sc["stand-in"]["wape"] == pytest.approx(18.00, abs=0.05)
    assert sc["GBM hybrid + CQR"]["cover"] == pytest.approx(0.791, abs=0.005)
    run_main(lab("lab07_foundation"), capsys, monkeypatch, {"run": r})


# --- Part III -------------------------------------------------------------------------------------
@needs_np
def test_lab08(capsys, monkeypatch):
    r = lab("lab08_mc_radar").run()
    rows = {f"{x['dc']} {x['sku']}": x for x in r["f11"]["rows"]}
    assert r["f11"]["max_check"] < 0.5                       # the deterministic replay is the radar
    assert rows["CHN MN-1L"]["p_red"] == pytest.approx(1.0)
    assert rows["CHN GC-200"]["p_red"] == pytest.approx(0.687, abs=0.01)
    assert r["after"]["p_red"] == pytest.approx(0.374, abs=0.01)
    run_main(lab("lab08_mc_radar"), capsys, monkeypatch, {"run": r})


@needs_np
def test_lab09(capsys, monkeypatch):
    r = lab("lab09_transfer_pays").run()
    assert r["p_pays"] == pytest.approx(0.966, abs=0.003)
    assert r["e_net"] == pytest.approx(82503, abs=100)
    assert r["best"]["cases"] == 3000
    run_main(lab("lab09_transfer_pays"), capsys, monkeypatch, {"run": r})


@needs_np
def test_lab10_s_S(capsys, monkeypatch):
    r = lab("lab10_s_S").run()
    rows = {x["rule"]: x for x in r["rows"]}
    assert (r["best"]["s"], r["best"]["s"] + r["best"]["Q"]) == (7750, 14250)
    assert rows["ROP + EOQ"]["cost"] == pytest.approx(20554, rel=0.005)
    assert rows["best (s, S)"]["cost"] == pytest.approx(13803, rel=0.005)
    assert rows["best (s, S)"]["fill"] == pytest.approx(0.995, abs=0.002)
    assert r["low"] > r["high"]                               # too low an s costs more than too high
    assert [round(w) for _, _, w in r["dips"]] == [1, 2, 3, 4]
    run_main(lab("lab10_s_S"), capsys, monkeypatch, {"run": r}, check_width=True)


@needs_np
def test_lab11_dock(capsys, monkeypatch):
    r = lab("lab11_dock_queue").run()
    f = r["facts"]
    assert f["at_stake_w12"] == 64354 and f["cases_late_w12"] == 2025
    assert r["unload_base"] == pytest.approx(2.38, abs=0.03)
    assert r["unload_w12"] == pytest.approx(4.44, abs=0.05)
    rows = {x["lever"]: x for x in r["rows"]}
    assert rows["do nothing"]["mean"] == pytest.approx(9.5, abs=0.1)
    assert r["pick"]["lever"] == "re-slot arrivals"
    assert r["meets_target"] == ["both crews to 22:00"]
    assert r["breakeven"] == pytest.approx(44, abs=2)
    run_main(lab("lab11_dock_queue"), capsys, monkeypatch, {"run": r}, check_width=True)


# --- Part IV and V --------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def r12():
    return lab("lab12_regression_mean").run()


@needs_np
def test_lab12_rtm(r12, capsys, monkeypatch):
    m = r12["mean"]
    assert m["worst_now"] == pytest.approx(28.0, abs=0.1)
    assert m["worst_next"] - m["worst_now"] == pytest.approx(-6.1, abs=0.1)
    assert r12["claim"] == pytest.approx(7.5, abs=0.05)
    assert r12["rtm"] == pytest.approx(3.6, abs=0.05)
    assert r12["split_mean"] == pytest.approx(r12["truth_pool"], abs=0.2)  # the coin toss is unbiased
    run_main(lab("lab12_regression_mean"), capsys, monkeypatch, {"run": r12}, check_width=True)


@pytest.fixture(scope="module")
def r13():
    return lab("lab13_did").run()


@needs_np
def test_lab13_did(r13, capsys, monkeypatch):
    assert r13["naive"] == pytest.approx(-36433, abs=10)
    assert r13["did"] == pytest.approx(-4264, abs=10)
    assert r13["truth"] == pytest.approx(-4204, abs=10)
    assert r13["placebo_q"][0] < r13["did"] < r13["placebo_q"][1]        # cannot be told from zero
    run_main(lab("lab13_did"), capsys, monkeypatch, {"run": r13}, check_width=True)


@pytest.fixture(scope="module")
def r14():
    return lab("lab14_switchback").run()


@needs_np
def test_lab14_switchback(r14, capsys, monkeypatch):
    assert r14["truth"]["per_week"] == pytest.approx(47544, abs=10)
    d = {(x["B"], x["washout"]): x for x in r14["designs"]}
    assert d[(1, 0)]["mean"] < 0                                         # week by week: the wrong sign
    assert (r14["best"]["B"], r14["best"]["washout"]) == (5, 1)
    assert abs(d[(5, 1)]["bias"]) < 2000
    assert r14["estimate"] == pytest.approx(49279, abs=10)
    assert r14["p"] == pytest.approx(0.25) and r14["n_tosses"] == 16
    run_main(lab("lab14_switchback"), capsys, monkeypatch, {"run": r14}, check_width=True)


@needs_np
def test_lab15_ledger(r12, r13, r14, capsys, monkeypatch):
    m15 = lab("lab15_value_ledger")
    with pytest.MonkeyPatch.context() as mp:                 # reuse labs 12-14 instead of re-running them
        for name, res in (("lab12_regression_mean", r12), ("lab13_did", r13), ("lab14_switchback", r14)):
            mp.setattr(lab(name), "run", lambda *a, _r=res, **k: _r)
        r = m15.run()
    L = r["ledger"]
    assert L["net"][1] == pytest.approx(21.26e5, abs=0.1e5)
    assert L["p_positive"] == pytest.approx(0.956, abs=0.005)
    assert L["workload"]["adjust_week"] == pytest.approx(13.8, abs=0.05)
    assert r["wrong"][0][1] == 347373                                    # the week-12 queue, from code
    assert r["sim_off_ratio"] == pytest.approx(0.39, abs=0.01)
    run_main(m15, capsys, monkeypatch, {"run": r}, check_width=True)
