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

    monkeypatch.setattr(
        pipeline.classifications, "latest_for_message", lambda conn, mid: None
    )
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
    assert graph.tagged == [("ext-1", ["respond", "P1", "invoice", "Travel", "keep_until:2026-08-01"])]


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
