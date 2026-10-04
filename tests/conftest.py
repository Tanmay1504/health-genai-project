"""
Test configuration. Runs BEFORE any project module is imported, so the env
vars below take effect:
  * LLM_PROVIDER=mock    -> no network, no model; agents use deterministic templates
  * AUDIT_DB_PATH        -> a throwaway SQLite file, never your real audit log
"""
import os
import tempfile
from pathlib import Path

import pytest

os.environ["LLM_PROVIDER"] = "mock"
os.environ["AUDIT_DB_PATH"] = str(Path(tempfile.mkdtemp()) / "audit_test.db")

ROOT = Path(__file__).resolve().parent.parent


def pipeline_ready() -> bool:
    """Smoke tests need the data pipeline + trained models to exist."""
    return (ROOT / "data/processed/graph_features.csv").exists() and (ROOT / "brain/models/metadata.json").exists()


needs_pipeline = pytest.mark.skipif(not pipeline_ready(), reason="run the data pipeline + `python -m brain.train` first")
