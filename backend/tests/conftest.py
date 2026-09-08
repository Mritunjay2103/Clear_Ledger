"""Test fixtures.

Every test process gets its own data directory and its own SQLite file, set
before ``app.config`` is imported, so a test run can never touch the demo
database or the developer's uploads.
"""

from __future__ import annotations

import os
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

_TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="clearledger-tests-"))
os.environ["DATA_DIR"] = str(_TEST_DATA_DIR)
os.environ["APP_ENV"] = "test"
os.environ["EXTRACTION_PROVIDER"] = "rules"
os.environ["DEMO_USERNAME"] = ""
os.environ["DEMO_PASSWORD"] = ""
os.environ["STATIC_DIR"] = ""
os.environ["RATE_LIMIT_PER_MINUTE"] = "10000"


@pytest.fixture(scope="session")
def data_dir() -> Path:
    return _TEST_DATA_DIR


@pytest.fixture(scope="session")
def samples_dir() -> SampleLibrary:
    directory = PROJECT_ROOT / "data" / "samples"
    if not (directory / "catalog.json").exists():
        pytest.skip("Sample fixtures are not generated. Run scripts/generate_samples.py first.")
    return SampleLibrary(directory)


class SampleLibrary:
    """Resolves a sample id to its PDF, so tests never hard-code filenames."""

    def __init__(self, root: Path) -> None:
        import json

        self.root = root
        catalog = json.loads((root / "catalog.json").read_text(encoding="utf-8"))
        self._by_id = {entry["id"]: entry for entry in catalog["samples"]}

    def path(self, sample_id: str) -> Path:
        entry = self._by_id.get(sample_id)
        if entry is None:
            raise KeyError(f"No sample {sample_id!r}. Known: {sorted(self._by_id)}")
        return self.root / "pdf" / entry["filename"]

    def entry(self, sample_id: str) -> dict:
        return self._by_id[sample_id]

    def __truediv__(self, name: str) -> Path:
        return self.path(name.removesuffix(".pdf"))


def _fresh_schema():
    from app.db import models  # noqa: F401  (registers the tables)
    from app.db.session import Base, get_engine, get_session_factory

    engine = get_engine()
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    return get_session_factory()


@pytest.fixture()
def session() -> Iterator:
    """A database session against a schema created fresh for this test."""
    factory = _fresh_schema()
    db = factory()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture()
def seeded_session(session):
    """A session with the shipped reference data loaded."""
    from app.services.seed import seed_reference_data

    seed_reference_data(session)
    session.commit()
    return session


@pytest.fixture()
def client(monkeypatch) -> Iterator:
    """A TestClient over the real app, on a schema created fresh per test.

    The queue worker is bypassed: runs execute inline so a test never has to
    sleep. The workflow code under test is exactly the code the server runs.
    """
    from fastapi.testclient import TestClient

    from app.api.security import reset_rate_limits
    from app.services import queue as queue_module

    reset_rate_limits()
    from app.services.seed import seed_reference_data

    factory = _fresh_schema()
    with factory() as db:
        seed_reference_data(db)
        db.commit()

    from app.services.workflow import execute_run

    monkeypatch.setattr(queue_module.run_queue, "enqueue", execute_run)

    from app.main import app

    with TestClient(app) as test_client:
        test_client.headers.update({"X-ClearLedger-Request": "1"})
        yield test_client


@pytest.fixture()
def mark_failed():
    """Force a run into the FAILED state, to exercise recovery paths."""

    def _mark(run_id: str, message: str = "Simulated technical failure.") -> None:
        from app.db.models import WorkflowRun
        from app.db.session import session_scope

        with session_scope() as db:
            run = db.get(WorkflowRun, run_id)
            run.execution_status = "FAILED"
            run.decision = None
            run.error_code = "internal_error"
            run.error_message = message
            run.version += 1

    return _mark
