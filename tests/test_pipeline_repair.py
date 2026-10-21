from handlers.pipeline import _repair_tag_if_missing


class FakeEmail:
    def __init__(self, categories):
        self.categories = categories


class FakeGraph:
    def __init__(self):
        self.tagged = []

    def tag_message(self, external_id, categories):
        self.tagged.append((external_id, categories))
        return True


def test_repair_reapplies_stored_tags(monkeypatch):
    import handlers.pipeline as pipeline

    monkeypatch.setattr(
        pipeline.classifications,
        "latest_for_message",
        lambda conn, mid: {"category": "respond", "importance": "P1", "tags": ["invoice"]},
    )
    graph = FakeGraph()
    _repair_tag_if_missing(None, graph, FakeEmail(categories=["P1"]), "db-1", "ext-1")
    assert graph.tagged == [("ext-1", ["respond", "P1", "invoice"])]


def test_repair_skips_already_tagged_message(monkeypatch):
    import handlers.pipeline as pipeline

    monkeypatch.setattr(
        pipeline.classifications,
        "latest_for_message",
        lambda conn, mid: {"category": "respond", "importance": "P1", "tags": []},
    )
    graph = FakeGraph()
    # live message already carries a recognized category tag -> human owns it
    _repair_tag_if_missing(None, graph, FakeEmail(categories=["review", "P2"]), "db-1", "ext-1")
    assert graph.tagged == []


def test_repair_skips_when_no_stored_classification(monkeypatch):
    import handlers.pipeline as pipeline

    monkeypatch.setattr(pipeline.classifications, "latest_for_message", lambda conn, mid: None)
    graph = FakeGraph()
    _repair_tag_if_missing(None, graph, FakeEmail(categories=[]), "db-1", "ext-1")
    assert graph.tagged == []


def test_repair_preserves_unrecognized_live_tags(monkeypatch):
    import handlers.pipeline as pipeline

    monkeypatch.setattr(
        pipeline.classifications,
        "latest_for_message",
        lambda conn, mid: {"category": "respond", "importance": "P1", "tags": ["invoice"]},
    )
    graph = FakeGraph()
    _repair_tag_if_missing(
        None, graph, FakeEmail(categories=["Travel", "keep_until:2026-08-01"]), "db-1", "ext-1"
    )
    assert graph.tagged == [
        ("ext-1", ["respond", "P1", "invoice", "Travel", "keep_until:2026-08-01"])
    ]


def test_repair_handles_null_importance_and_tags(monkeypatch):
    import handlers.pipeline as pipeline

    monkeypatch.setattr(
        pipeline.classifications,
        "latest_for_message",
        lambda conn, mid: {"category": "reference", "importance": None, "tags": None},
    )
    graph = FakeGraph()
    _repair_tag_if_missing(None, graph, FakeEmail(categories=[]), "db-1", "ext-1")
    assert graph.tagged == [("ext-1", ["reference"])]


class _Conn:
    def __init__(self, log):
        self.log = log

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def commit(self):
        self.log.append("commit")


class _Stop(Exception):
    pass


def _drive_run(monkeypatch, existing):
    import handlers.pipeline as pipeline

    log = []
    msg = {"source": "email", "external_id": "e1", "sender": "a@x.com", "subject": "s"}
    monkeypatch.delenv("GCP_PROJECT_ID", raising=False)
    monkeypatch.setattr(pipeline, "get_graph_client", lambda: object())
    monkeypatch.setattr(pipeline, "fetch", lambda mid, client: FakeEmail([]))
    monkeypatch.setattr(pipeline, "normalize", lambda email, raw=None: dict(msg))
    monkeypatch.setattr(pipeline, "get_conn", lambda: _Conn(log))
    monkeypatch.setattr(pipeline.messages, "get_by_external_id", lambda *a: existing)
    monkeypatch.setattr(pipeline.messages, "insert", lambda conn, m: "db-1", raising=False)
    monkeypatch.setattr(pipeline, "_repair_tag_if_missing", lambda *a: None)
    monkeypatch.setattr(pipeline.people_api, "get_person", lambda sender: None)
    monkeypatch.setattr(pipeline, "text_for_embedding", lambda m: "text")
    monkeypatch.setattr(
        pipeline, "embed_and_store", lambda conn, mid, text, model: log.append("embed") or [0.0]
    )
    monkeypatch.setattr(
        pipeline.bounce, "maybe_publish", lambda m, g: log.append(("bounce", m["id"])) or False
    )

    def stop(conn, vec, exclude_id=None):
        log.append("neighbors")
        raise _Stop

    monkeypatch.setattr(pipeline, "retrieve_neighbors", stop)
    try:
        pipeline.run({"resourceData": {"id": "e1"}}, model=None)
    except _Stop:
        pass
    return log


def test_run_publishes_bounce_check_once_after_first_commit(monkeypatch):
    log = _drive_run(monkeypatch, existing=None)
    assert log == ["embed", "commit", ("bounce", "db-1"), "neighbors"]


def test_run_skips_bounce_check_for_duplicate(monkeypatch):
    log = _drive_run(monkeypatch, existing={"id": "db-0"})
    assert not any(isinstance(e, tuple) and e[0] == "bounce" for e in log)
