from datetime import datetime

from services.classification import aggregate_neighbors, build_prompt

_BASE_MSG = {
    "id": None,
    "source": "email",
    "external_id": "abc123",
    "sender": "alice@example.com",
    "sender_display": "Alice",
    "subject": "Quarterly report",
    "body": "Please review the attached quarterly report by Friday.",
    "body_html": None,
    "received_at": datetime(2026, 6, 10, 14, 0),
    "thread_id": None,
    "raw": {},
    "web_link": None,
}


def test_aggregate_neighbors_counts_by_label():
    neighbors = [
        {"current_label": "urgent"},
        {"current_label": "urgent"},
        {"current_label": "respond"},
        {"current_label": None},
        {"current_label": ""},
    ]
    result = aggregate_neighbors(neighbors)
    assert result == {"urgent": 2, "respond": 1}


def test_aggregate_neighbors_empty():
    assert aggregate_neighbors([]) == {}


def test_build_prompt_returns_static_system_prompt():
    system, _ = build_prompt(_BASE_MSG, {}, [], None)
    assert "urgent" in system
    assert "P0" in system
    assert "JSON" in system


def test_build_prompt_includes_sender_and_subject():
    _, user = build_prompt(_BASE_MSG, {}, [], None)
    assert "alice@example.com" in user
    assert "Quarterly report" in user


def test_build_prompt_formats_received_date():
    _, user = build_prompt(_BASE_MSG, {}, [], None)
    assert "2026-06-10" in user


def test_build_prompt_includes_body():
    _, user = build_prompt(_BASE_MSG, {}, [], None)
    assert "quarterly report" in user


def test_build_prompt_with_sender_context():
    sender_ctx = {
        "message_count": 10,
        "my_response_count": 4,
        "relationship_label": "colleague",
        "notes": "direct manager",
    }
    _, user = build_prompt(_BASE_MSG, {}, [], sender_ctx)
    assert "4/10 replied" in user
    assert "colleague" in user
    assert "direct manager" in user


def test_build_prompt_with_neighbors():
    neighbors = [
        {
            "current_label": "review",
            "current_importance": "P1",
            "sender": "bob@example.com",
            "subject": "Q3 results",
            "body": "Here are the results.",
        }
    ]
    aggregates = {"review": 1}
    _, user = build_prompt(_BASE_MSG, aggregates, neighbors, None)
    assert "Similar labeled emails" in user
    assert "[review, P1]" in user
    assert "bob@example.com" in user
    assert "review: 1" in user


def test_build_prompt_body_truncated_at_1500():
    long_body = "x" * 2000
    msg = {**_BASE_MSG, "body": long_body}
    _, user = build_prompt(msg, {}, [], None)
    assert "x" * 1500 in user
    assert "x" * 1501 not in user


def test_build_prompt_no_neighbors_omits_retrieval_section():
    _, user = build_prompt(_BASE_MSG, {}, [], None)
    assert "Similar labeled emails" not in user
    assert "Label distribution" not in user
