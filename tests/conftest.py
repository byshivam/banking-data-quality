"""Shared fixtures: one dirty and one clean extract per test session.

The pipeline picks up PIPELINE_BUGS from the environment, so the bug-hunt
runner can rerun this exact suite with a planted bug switched on.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from arya_dq import db, generate, pipeline, rules


@pytest.fixture(scope="session")
def dirty_raw(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("dirty")
    generate.write(generate.build(inject=True), out)
    return out


@pytest.fixture(scope="session")
def clean_raw(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("clean")
    generate.write(generate.build(inject=False), out)
    return out


@pytest.fixture(scope="session")
def manifest(dirty_raw) -> dict:
    return json.loads((dirty_raw / "manifest.json").read_text())


def _loaded(raw: Path):
    con = db.connect()
    db.load_raw(con, raw)
    pipeline.run(con)
    return con


@pytest.fixture(scope="session")
def dirty_db(dirty_raw):
    con = _loaded(dirty_raw)
    yield con
    con.close()


@pytest.fixture(scope="session")
def clean_db(clean_raw):
    con = _loaded(clean_raw)
    yield con
    con.close()


@pytest.fixture(scope="session")
def dirty_findings(dirty_db) -> dict[str, list[str]]:
    return rules.run_all(dirty_db)


@pytest.fixture(scope="session")
def clean_findings(clean_db) -> dict[str, list[str]]:
    return rules.run_all(clean_db)
