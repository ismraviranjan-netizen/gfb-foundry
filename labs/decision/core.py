"""The arithmetic every lab leans on, in plain Python (no numpy), so it can be read in one sitting
and tested in CI without extra packages.

quantile        the tau-quantile of a sample (linear interpolation between order statistics)
pinball         the loss that a tau-quantile minimises; the score for a range forecast
coverage        the share of outcomes inside [low, high]
winkler         an interval's width plus a penalty for every miss (scaled by 2 / alpha)
conformal_q     the finite-sample quantile of calibration scores that split conformal uses
wape, mase      point-forecast errors that survive zeros and different scales
critical_ratio  the newsvendor's answer: which quantile to stock to
"""
from __future__ import annotations

import math
from statistics import NormalDist

N01 = NormalDist()


def quantile(xs: list[float], tau: float) -> float:
    """Type-7 quantile (the default in numpy and R): sort, then interpolate between neighbours."""
    if not xs:
        raise ValueError("empty sample")
    if not 0.0 <= tau <= 1.0:
        raise ValueError("tau must be in [0, 1]")
    s = sorted(xs)
    h = (len(s) - 1) * tau
    lo = math.floor(h)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (h - lo) * (s[hi] - s[lo])


def pinball(y: float, q: float, tau: float) -> float:
    """Under-forecast by u costs tau * u; over-forecast by u costs (1 - tau) * u."""
    u = y - q
    return tau * u if u >= 0 else (tau - 1.0) * u


def mean_pinball(ys: list[float], qs: list[float], tau: float) -> float:
    return sum(pinball(y, q, tau) for y, q in zip(ys, qs)) / len(ys)


def coverage(ys: list[float], lows: list[float], highs: list[float]) -> float:
    inside = sum(1 for y, a, b in zip(ys, lows, highs) if a <= y <= b)
    return inside / len(ys)


def winkler(y: float, low: float, high: float, alpha: float) -> float:
    """The interval score for a (1 - alpha) interval: width, plus 2/alpha times the miss."""
    score = high - low
    if y < low:
        score += 2.0 / alpha * (low - y)
    elif y > high:
        score += 2.0 / alpha * (y - high)
    return score


def conformal_q(scores: list[float], alpha: float) -> float:
    """Split conformal: the ceil((n + 1)(1 - alpha))-th smallest score. With n scores from data
    the model never saw, a new score falls at or below it with probability >= 1 - alpha, if the new
    case is exchangeable with the calibration cases. Returns inf when n is too small to promise."""
    n = len(scores)
    k = math.ceil((n + 1) * (1.0 - alpha))
    if k > n:
        return math.inf
    return sorted(scores)[k - 1]


def wape(actuals: list[float], forecasts: list[float]) -> float:
    """Sum of absolute errors over sum of actuals, in percent: big series weigh more, zeros are safe."""
    den = sum(abs(a) for a in actuals)
    return 100.0 * sum(abs(a - f) for a, f in zip(actuals, forecasts)) / den if den else 0.0


def bias_pct(actuals: list[float], forecasts: list[float]) -> float:
    """(sum of forecasts - sum of actuals) / sum of actuals, in percent. Positive = forecast ran high."""
    den = sum(actuals)
    return 100.0 * (sum(forecasts) - den) / den if den else 0.0


def mase(actuals: list[float], forecasts: list[float], history: list[float], m: int = 1) -> float:
    """Mean absolute error scaled by the in-sample MAE of the naive (lag-m) forecast on the history.
    Below 1.0 beats the naive forecast; above 1.0 loses to it."""
    naive = [abs(history[i] - history[i - m]) for i in range(m, len(history))]
    scale = sum(naive) / len(naive) if naive else 0.0
    mae = sum(abs(a - f) for a, f in zip(actuals, forecasts)) / len(actuals)
    return mae / scale if scale else math.inf


def critical_ratio(underage: float, overage: float) -> float:
    """The newsvendor: stock to the quantile cu / (cu + co). cu = what a case short costs (the lost
    margin); co = what a case left over costs (holding, markdown, waste)."""
    if underage < 0 or overage < 0 or underage + overage == 0:
        raise ValueError("costs must be non-negative and not both zero")
    return underage / (underage + overage)


def normal_stock(mu: float, sigma: float, tau: float) -> float:
    """The tau-quantile of a normal demand: mu + z(tau) * sigma."""
    return mu + N01.inv_cdf(tau) * sigma


def normal_loss(z: float) -> float:
    """The standard normal loss function L(z) = phi(z) - z (1 - Phi(z)): expected shortage in sigmas."""
    return N01.pdf(z) - z * (1.0 - N01.cdf(z))


def newsvendor_cost(mu: float, sigma: float, stock: float, underage: float, overage: float) -> float:
    """Expected cost of stocking `stock` against normal demand: cu * E[short] + co * E[left over]."""
    z = (stock - mu) / sigma
    short = sigma * normal_loss(z)
    left = (stock - mu) + short
    return underage * short + overage * left
