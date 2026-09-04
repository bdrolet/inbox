import base64
import json
import sys
import types

import pytest

_fake_db = types.ModuleType("clients.db")
_fake_db.get_conn = lambda: None
sys.modules.setdefault("clients.db", _fake_db)

import main  # noqa: E402


class CE:
    def __init__(self, attrs):
        self.data = {
            "message": {
                "data": base64.b64encode(
                    json.dumps({"resourceData": {"id": "x"}}).encode()
                ).decode(),
                "attributes": attrs,
            }
        }


@pytest.fixture
def routes(monkeypatch):
    log = []
    monkeypatch.setattr(main, "_run_pipeline", lambda n, m, context=None: log.append("pipeline"))
    monkeypatch.setattr(main, "_run_sent", lambda n, context=None: log.append("sent"))
    monkeypatch.setattr(main, "_get_model", lambda: None)
    return log


def test_default_is_pipeline(routes):
    main.process(CE({}))
    assert routes == ["pipeline"]


def test_inbox_is_pipeline(routes):
    main.process(CE({"folder": "inbox"}))
    assert routes == ["pipeline"]


def test_sentitems_is_sent(routes):
    main.process(CE({"folder": "sentitems"}))
    assert routes == ["sent"]
