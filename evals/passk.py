"""pass^k and pass@k: the arithmetic behind "will the demo work ten times in a row".

pass^k  = p**k          probability that k independent runs ALL succeed (what a live demo needs)
pass@k  = 1 - (1-p)**k  probability that at least one of k runs succeeds (what a retry loop gets)
"""
from __future__ import annotations

import sys


def pass_power_k(p: float, k: int) -> float:
    return p ** k


def pass_at_k(p: float, k: int) -> float:
    return 1 - (1 - p) ** k


if __name__ == "__main__":
    p = float(sys.argv[1]) if len(sys.argv) > 1 else 0.9
    for k in (1, 3, 6, 10):
        print(f"p={p:.2f} k={k:2d}  pass^k={pass_power_k(p, k):.3f}  pass@k={pass_at_k(p, k):.3f}")
