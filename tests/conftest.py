import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["FOUNDRY_RUNS"] = str(Path(tempfile.mkdtemp(prefix="foundry-tests-")) / "runs.jsonl")   # the run log never lands in the repo

from foundry.data.generate import build, canonical_week12   # noqa: E402
from foundry.profile import load_profile                     # noqa: E402
from foundry.levers import Levers                            # noqa: E402
from foundry.approval import ProposalStore                   # noqa: E402


@pytest.fixture(scope="session")
def store():
    return build(canonical_week12(), seed=7)


@pytest.fixture(scope="session")
def profile():
    return load_profile("gfb")


@pytest.fixture
def levers(tmp_path):
    return Levers(tmp_path / "panel.json", tmp_path / "audit.jsonl")


@pytest.fixture
def proposals(tmp_path):
    return ProposalStore(tmp_path / "proposals.jsonl")
