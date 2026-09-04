"""The pipeline asks people-api for sender context and tolerates None."""

import importlib

import pytest


def test_pipeline_imports_people_api_not_senders():
    src = open("handlers/pipeline.py").read()
    assert "people_api" in src and "senders" not in src


def test_repo_has_no_senders_module():
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("repo.senders")
