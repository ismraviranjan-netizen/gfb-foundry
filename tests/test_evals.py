import subprocess
import sys
from pathlib import Path


def test_eval_gate_passes_offline(tmp_path):
    out = tmp_path / "results.json"
    root = Path(__file__).resolve().parent.parent
    r = subprocess.run([sys.executable, str(root / "evals" / "run_evals.py"), "--out", str(out)], capture_output=True, text=True, cwd=root)
    assert r.returncode == 0, r.stdout[-2000:]
    assert '"gate": "PASS"' in r.stdout
