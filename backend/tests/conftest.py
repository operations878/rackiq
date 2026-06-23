"""Shared pytest fixtures — every test runs against a throwaway DuckDB file."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from app import db


@pytest.fixture()
def con():
    """A fresh, initialized DuckDB connection on a temporary file."""
    path = tempfile.mktemp(suffix=".duckdb")
    c = db.get_connection(path, read_only=False)
    db.init_db(c)
    yield c
    c.close()
    Path(path).unlink(missing_ok=True)


@pytest.fixture()
def client():
    """A FastAPI TestClient whose shared connection points at a temporary file."""
    from fastapi.testclient import TestClient

    from app.main import create_app

    path = tempfile.mktemp(suffix=".duckdb")
    db.DEFAULT_DB_PATH = Path(path)
    db._shared.clear()
    c = TestClient(create_app())
    c.post("/api/studio/reset", json={})
    yield c
    db._shared.clear()
    Path(path).unlink(missing_ok=True)
