"""The pipeline asks people-api for sender context and tolerates None."""

import importlib
from pathlib import Path

import pytest


def test_pipeline_imports_people_api_not_senders():
    src = (Path(__file__).resolve().parents[1] / "handlers" / "pipeline.py").read_text()
    assert "people_api" in src and "senders" not in src


def test_repo_has_no_senders_module():
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("repo.senders")
