"""Build the Lambda code bundle from a whitelist: only what the functions import or read at run time.

Zipping the repo root and excluding what should not go (the old way) leaks whatever the list forgot:
Terraform's own state file sits in deploy/ after the first apply, and it holds the console key in plain
text. A whitelist cannot leak what it never names.

    python3 deploy/bundle.py            # -> deploy/build/bundle/ (Terraform zips this folder)
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KEEP_DIRS = ["foundry", "profiles", "networks"]          # the code, the company profiles, the networks
KEEP_FILES = ["deploy/handler.py"]                       # the two entry points live in handler.py
SKIP = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache", "*.tfstate*", "*.tfplan")


def build(out: Path | None = None) -> Path:
    out = Path(out or ROOT / "deploy" / "build" / "bundle")
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    for d in KEEP_DIRS:
        shutil.copytree(ROOT / d, out / d, ignore=SKIP)
    for f in KEEP_FILES:
        (out / f).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / f, out / f)
    (out / "deploy" / "__init__.py").write_text("")
    leaked = [str(p.relative_to(out)) for p in out.rglob("*") if p.name.endswith((".tfstate", ".tfstate.backup", ".tfplan"))]
    if leaked:
        raise SystemExit(f"refusing to bundle state or plan files: {leaked}")
    return out


if __name__ == "__main__":
    p = build(Path(sys.argv[1]) if len(sys.argv) > 1 else None)
    n = sum(1 for x in p.rglob("*") if x.is_file())
    print(f"bundle built: {p.relative_to(ROOT) if p.is_relative_to(ROOT) else p} ({n} files)")
