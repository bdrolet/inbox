# Inbox Grooming Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the morning `inbox-sweep` so stale urgent messages are re-triaged by Claude (keep / demote / archive) and untagged legacy mail is republished to the processor for classification, making the Inbox self-healing.

**Architecture:** Two new per-message decisions inside the existing sweep pass — a `retriage` action (urgent + >3 days old → Claude verdict, recheck state carried on the message via `keep_until:` tags) and a `republish` action (untagged + >24 h old → synthetic notification to the `inbox-messages` Pub/Sub topic, capped at 50/night). The processor gains a duplicate-repair path so republishing an already-stored-but-untagged message re-applies its tag instead of silently skipping. Spec: `docs/superpowers/specs/2026-07-17-inbox-grooming-design.md`.

**Tech Stack:** Python 3.13, Microsoft Graph API, Anthropic SDK (`claude-sonnet-4-6`), google-cloud-pubsub, pytest, Terraform.

## Global Constraints

- **Never write to `message_embeddings.current_label`** and never emit `label_applied` events from re-triage — that column/event is human feedback only (project invariant).
- Re-triage thresholds: first check at **3 days**, recheck hold of **3 days**, republish age floor **24 hours**, republish cap **50/night** — module constants, exact values.
- Fail-safe defaults everywhere: missing `received_at` → skip; reply-lookup failure → `False`; Claude error or unknown verdict → `still_urgent`; publish failure → retry next night. A grooming failure must never abort the rest of the sweep.
- Layer rules: `clients/` I/O only; `services/` one concern, calls clients/repo; `repo/` takes an open connection; `services/sweep_rules.py` stays pure (no I/O) so it runs in CI.
- All work on a feature branch off `main` (`git checkout -b inbox-grooming` at the start); never commit code to `main`. PR opened at the end via the `pr-open` skill.
- Run tests with `.venv/bin/python -m pytest` from the repo root (activate with `source .venv/bin/activate` once).

---

### Task 1: Widen the sweep's Inbox listing

The sweep needs each message's age and conversation to make grooming decisions. `list_inbox_categories()` currently selects only `id,categories`.

**Files:**
- Modify: `clients/azure/graph_email_client.py:1028-1039` (`list_inbox_categories`)
- Test: `tests/test_graph_read.py` (append)

**Interfaces:**
- Consumes: nothing new.
- Produces: `list_inbox_categories() -> list[dict]` where each dict is `{"id": str, "categories": list[str], "receivedDateTime": str | None, "conversationId": str | None}`. Tasks 6 uses these keys verbatim.

- [ ] **Step 1: Create the feature branch**

```bash
git checkout main && git pull && git checkout -b inbox-grooming
```

- [ ] **Step 2: Write the failing test**

Append to `tests/test_graph_read.py` (it already defines `_Resp` and `_client()` helpers at the top — reuse them):

```python
def test_list_inbox_categories_includes_received_and_conversation(monkeypatch):
    seen = {}

    def fake_get(url, headers=None, params=None):
        seen["url"] = url
        return _Resp(
            json_data={
                "value": [
                    {
                        "id": "m1",
                        "categories": ["urgent", "P0"],
                        "receivedDateTime": "2026-07-10T12:00:00Z",
                        "conversationId": "conv1",
                    },
                    {"id": "m2"},
                ]
            }
        )

    monkeypatch.setattr(requests, "get", fake_get)
    rows = _client().list_inbox_categories()
    assert "receivedDateTime,conversationId" in seen["url"] or "receivedDateTime" in seen["url"]
    assert rows[0] == {
        "id": "m1",
        "categories": ["urgent", "P0"],
        "receivedDateTime": "2026-07-10T12:00:00Z",
        "conversationId": "conv1",
    }
    assert rows[1] == {"id": "m2", "categories": [], "receivedDateTime": None, "conversationId": None}
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_graph_read.py::test_list_inbox_categories_includes_received_and_conversation -v`
Expected: FAIL — `KeyError`/assert mismatch (fields missing from returned dicts).

- [ ] **Step 4: Widen the listing**

In `clients/azure/graph_email_client.py`, replace the body of `list_inbox_categories`:

```python
    def list_inbox_categories(self) -> list[dict]:
        """Return [{'id', 'categories', 'receivedDateTime', 'conversationId'}]
        for all Inbox messages (immutable IDs)."""
        results: list[dict] = []
        url = (
            f"{self.graph_endpoint}/me/mailFolders/inbox/messages"
            "?$select=id,categories,receivedDateTime,conversationId&$top=100"
        )
        while url:
            resp = requests.get(url, headers=self.get_headers(immutable=True))
            resp.raise_for_status()
            data = resp.json()
            for m in data.get("value", []):
                results.append(
                    {
                        "id": m["id"],
                        "categories": m.get("categories", []),
                        "receivedDateTime": m.get("receivedDateTime"),
                        "conversationId": m.get("conversationId"),
                    }
                )
            url = data.get("@odata.nextLink")
        return results
```

- [ ] **Step 5: Run the graph tests**

Run: `.venv/bin/python -m pytest tests/test_graph_read.py -v`
Expected: all PASS (existing tests unaffected — they don't call this method).

- [ ] **Step 6: Commit**

```bash
git add clients/azure/graph_email_client.py tests/test_graph_read.py
git commit -m "feat: include receivedDateTime + conversationId in sweep Inbox listing"
```

---

### Task 2: `latest_reply_from_me` Graph helper

Content-bearing signal for re-triage: what did Ben last say in this conversation since the message arrived? Returning the reply's `bodyPreview` (not a boolean) lets the verdict distinguish a holding reply ("on it, will finish Friday" → still pending) from a resolving one ("done, fixed" → resolvable). Sent Items only contains mail the mailbox owner sent, so a Sent Items lookup by `conversationId` is sufficient — no `from`-address comparison needed.

**Files:**
- Modify: `clients/azure/graph_email_client.py` (add method after `list_inbox_categories`)
- Test: `tests/test_graph_read.py` (append)

**Interfaces:**
- Produces: `latest_reply_from_me(conversation_id: str, after: datetime) -> str | None` — `bodyPreview` of the most recent Sent Items message in the conversation sent after `after`; `None` when there is none. Failure → `None` (absence of evidence). Task 5 calls this.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_graph_read.py`:

```python
from datetime import datetime, timezone


def test_latest_reply_from_me_returns_newest_preview(monkeypatch):
    def fake_get(url, headers=None, params=None):
        assert "sentitems" in url
        assert params["$filter"] == "conversationId eq 'conv1'"
        return _Resp(
            json_data={
                "value": [
                    {"id": "s1", "sentDateTime": "2026-07-11T09:00:00Z",
                     "bodyPreview": "older reply"},
                    {"id": "s2", "sentDateTime": "2026-07-12T09:00:00Z",
                     "bodyPreview": "on it, will finish Friday"},
                ]
            }
        )

    monkeypatch.setattr(requests, "get", fake_get)
    after = datetime(2026, 7, 10, tzinfo=timezone.utc)
    assert _client().latest_reply_from_me("conv1", after) == "on it, will finish Friday"


def test_latest_reply_from_me_none_when_only_older_replies(monkeypatch):
    def fake_get(url, headers=None, params=None):
        return _Resp(
            json_data={
                "value": [{"id": "s1", "sentDateTime": "2026-07-09T09:00:00Z",
                           "bodyPreview": "before it arrived"}]
            }
        )

    monkeypatch.setattr(requests, "get", fake_get)
    after = datetime(2026, 7, 10, tzinfo=timezone.utc)
    assert _client().latest_reply_from_me("conv1", after) is None


def test_latest_reply_from_me_none_on_graph_error(monkeypatch):
    def fake_get(url, headers=None, params=None):
        raise requests.exceptions.ConnectionError("boom")

    monkeypatch.setattr(requests, "get", fake_get)
    after = datetime(2026, 7, 10, tzinfo=timezone.utc)
    assert _client().latest_reply_from_me("conv1", after) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_graph_read.py -k latest_reply -v`
Expected: FAIL — `AttributeError: 'GraphEmailClient' object has no attribute 'latest_reply_from_me'`.

- [ ] **Step 3: Implement**

Add to `clients/azure/graph_email_client.py` (needs `from datetime import datetime` — already imported at module top; verify, add if missing). The most-recent pick happens client-side (`$orderby` combined with a `$filter` on a different property is unreliable in Graph):

```python
    def latest_reply_from_me(self, conversation_id: str, after: datetime) -> str | None:
        """bodyPreview of the most recent Sent Items message in the conversation
        sent after `after`, or None if there is none. Returns None on any
        failure — absence of evidence, callers must not treat it as proof of
        no reply."""
        try:
            resp = requests.get(
                f"{self.graph_endpoint}/me/mailFolders/sentitems/messages",
                headers=self.get_headers(immutable=True),
                params={
                    "$filter": f"conversationId eq '{conversation_id}'",
                    "$select": "id,sentDateTime,bodyPreview",
                    "$top": "25",
                },
            )
            resp.raise_for_status()
            best: tuple[datetime, str] | None = None
            for m in resp.json().get("value", []):
                sent = m.get("sentDateTime")
                if not sent:
                    continue
                sent_dt = datetime.fromisoformat(sent.replace("Z", "+00:00"))
                if sent_dt > after and (best is None or sent_dt > best[0]):
                    best = (sent_dt, m.get("bodyPreview", ""))
            return best[1] if best else None
        except requests.exceptions.RequestException:
            logger.warning("latest_reply_from_me failed for conversation %s", conversation_id)
            return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_graph_read.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add clients/azure/graph_email_client.py tests/test_graph_read.py
git commit -m "feat: latest_reply_from_me conversation lookup for re-triage"
```

---

### Task 3: Sweep rules — `retriage` and `republish` decisions

Pure logic only. `decide()` gains an optional `received_at` and two new outcomes. Decision order (unchanged where existing): unelapsed `keep_until:` → **hold** (wins over everything, including re-triage — a manual hold defers grooming); recognized folder-mapped tag → **move**; `urgent` + old → **retriage**; untagged + no `keep_until:` at all + old → **republish**; else **skip**.

**Files:**
- Modify: `services/sweep_rules.py`
- Test: `tests/test_sweep_rules.py` (append)

**Interfaces:**
- Produces (Task 6 consumes all of these):
  - `decide(categories: list[str], now: datetime, received_at: datetime | None = None) -> SweepDecision` — `SweepDecision.action` may now also be `"retriage"` or `"republish"`.
  - `parse_graph_datetime(value: str | None) -> datetime | None`
  - Constants: `RETRIAGE_AFTER = timedelta(days=3)`, `REPUBLISH_AFTER = timedelta(hours=24)`, `RETRIAGE_HOLD_DAYS = 3`, `REPUBLISH_NIGHTLY_CAP = 50`, `KNOWN_CATEGORIES = {"urgent", "respond", "review", "reference", "ignore"}`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_sweep_rules.py`:

```python
from services.sweep_rules import parse_graph_datetime


def _received(y, m, d, hh=12):
    return datetime(y, m, d, hh, 0, tzinfo=ET)


def test_parse_graph_datetime():
    dt = parse_graph_datetime("2026-07-10T12:00:00Z")
    assert dt is not None and dt.tzinfo is not None
    assert parse_graph_datetime(None) is None
    assert parse_graph_datetime("garbage") is None


def test_decide_retriages_stale_urgent():
    # received 07-10 noon, sweep 07-14 5 AM -> older than 3 days
    d = decide(["urgent", "P0"], _now(2026, 7, 14), _received(2026, 7, 10))
    assert d.action == "retriage"


def test_decide_skips_fresh_urgent():
    # received 07-12, sweep 07-14 -> under 3 days
    assert decide(["urgent", "P0"], _now(2026, 7, 14), _received(2026, 7, 12)).action == "skip"


def test_decide_urgent_without_received_at_skips():
    assert decide(["urgent", "P0"], _now(2026, 7, 14), None).action == "skip"


def test_decide_keep_until_defers_retriage():
    d = decide(
        ["urgent", "keep_until:2026-07-20"], _now(2026, 7, 14), _received(2026, 7, 1)
    )
    assert d.action == "hold"


def test_decide_republishes_old_untagged():
    d = decide(["P2", "newsletter"], _now(2026, 7, 14), _received(2026, 7, 12))
    assert d.action == "republish"
    assert decide([], _now(2026, 7, 14), _received(2025, 1, 1)).action == "republish"


def test_decide_skips_fresh_or_undated_untagged():
    # under 24h old
    assert decide([], _now(2026, 7, 14), _received(2026, 7, 13, 12)).action == "skip"
    # no received_at -> fail safe
    assert decide([], _now(2026, 7, 14), None).action == "skip"


def test_decide_untagged_with_any_keep_until_never_republishes():
    # even an elapsed keep_until means "Ben touched this" -> leave it alone
    d = decide(["keep_until:2026-01-01"], _now(2026, 7, 14), _received(2026, 1, 1))
    assert d.action == "skip"


def test_decide_tagged_messages_unchanged():
    d = decide(["reference", "P3"], _now(2026, 7, 14), _received(2026, 7, 1))
    assert d.action == "move" and d.folder == "Archive"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_sweep_rules.py -v`
Expected: new tests FAIL (`ImportError: parse_graph_datetime` / `TypeError: decide() takes 2 positional arguments`); existing tests still PASS.

- [ ] **Step 3: Implement**

In `services/sweep_rules.py`, add after the imports/constants (extend the existing `timedelta` import):

```python
RETRIAGE_AFTER = timedelta(days=3)
REPUBLISH_AFTER = timedelta(hours=24)
RETRIAGE_HOLD_DAYS = 3
REPUBLISH_NIGHTLY_CAP = 50
KNOWN_CATEGORIES = {"urgent", "respond", "review", "reference", "ignore"}


def parse_graph_datetime(value: str | None) -> datetime | None:
    """Graph ISO timestamp ('...Z') -> aware datetime, or None if absent/bad."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
```

Replace `decide()`:

```python
def decide(
    categories: list[str], now: datetime, received_at: datetime | None = None
) -> SweepDecision:
    keep_tags = [c for c in categories if c.startswith(KEEP_UNTIL_PREFIX)]
    for tag in keep_tags:
        elapses = _parse_keep_until(tag[len(KEEP_UNTIL_PREFIX) :])
        if elapses is None or now < elapses:
            return SweepDecision(action="hold")

    for c in categories:
        folder = folder_for_category(c)
        if folder is not None:
            return SweepDecision(action="move", folder=folder, strip_categories=keep_tags)

    if "urgent" in categories:
        if received_at is not None and now - received_at > RETRIAGE_AFTER:
            return SweepDecision(action="retriage")
        return SweepDecision(action="skip")

    tagged = any(c in KNOWN_CATEGORIES for c in categories)
    if not tagged and not keep_tags:
        if received_at is not None and now - received_at > REPUBLISH_AFTER:
            return SweepDecision(action="republish")
    return SweepDecision(action="skip")
```

Update the `SweepDecision.action` Literal to `Literal["move", "hold", "skip", "retriage", "republish"]`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_sweep_rules.py -v`
Expected: all PASS (old and new).

- [ ] **Step 5: Commit**

```bash
git add services/sweep_rules.py tests/test_sweep_rules.py
git commit -m "feat: sweep rules gain retriage + republish decisions"
```

---

### Task 4: Sweep rules — verdict → outcome mapping

Pure mapping from a re-triage verdict to concrete tag/folder changes, so the table is unit-testable with no I/O. All `keep_until:` tags present at verdict time are dropped (only elapsed ones can reach here — unelapsed holds never produce a `retriage` decision) and a fresh one is added on `still_urgent`.

**Files:**
- Modify: `services/sweep_rules.py`
- Test: `tests/test_sweep_rules.py` (append)

**Interfaces:**
- Produces (Task 6 consumes):

```python
@dataclass
class RetriageOutcome:
    verdict: str                     # normalized verdict actually applied
    folder: str | None               # None -> stays in Inbox
    new_categories: list[str]        # full replacement category list

apply_verdict(verdict: str, categories: list[str], now: datetime) -> RetriageOutcome
```

Mapping: `still_urgent` (and any unknown value, fail safe) → folder `None`, categories minus old `keep_until:*` plus `keep_until:<(now + 3 days).date().isoformat()>`; `needs_response` → folder `"reply_required"`, `urgent`→`respond` swap, `keep_until:*` stripped; `resolved_or_expired` → folder `"Archive"`, `urgent` and `keep_until:*` stripped.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_sweep_rules.py`:

```python
from services.sweep_rules import apply_verdict


def test_apply_verdict_still_urgent_adds_hold():
    out = apply_verdict("still_urgent", ["urgent", "P0"], _now(2026, 7, 14))
    assert out.folder is None
    assert out.new_categories == ["urgent", "P0", "keep_until:2026-07-17"]
    assert out.verdict == "still_urgent"


def test_apply_verdict_still_urgent_replaces_old_hold():
    out = apply_verdict(
        "still_urgent", ["urgent", "keep_until:2026-07-10"], _now(2026, 7, 14)
    )
    assert out.new_categories == ["urgent", "keep_until:2026-07-17"]


def test_apply_verdict_needs_response_demotes():
    out = apply_verdict("needs_response", ["urgent", "P0"], _now(2026, 7, 14))
    assert out.folder == "reply_required"
    assert out.new_categories == ["respond", "P0"]


def test_apply_verdict_resolved_archives():
    out = apply_verdict(
        "resolved_or_expired", ["urgent", "P0", "keep_until:2026-07-01"], _now(2026, 7, 14)
    )
    assert out.folder == "Archive"
    assert out.new_categories == ["P0"]


def test_apply_verdict_unknown_treated_as_still_urgent():
    out = apply_verdict("banana", ["urgent"], _now(2026, 7, 14))
    assert out.folder is None
    assert out.verdict == "still_urgent"
    assert out.new_categories == ["urgent", "keep_until:2026-07-17"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_sweep_rules.py -k apply_verdict -v`
Expected: FAIL — `ImportError: cannot import name 'apply_verdict'`.

- [ ] **Step 3: Implement**

Add to `services/sweep_rules.py`:

```python
@dataclass
class RetriageOutcome:
    verdict: str
    folder: str | None
    new_categories: list[str]


def apply_verdict(verdict: str, categories: list[str], now: datetime) -> RetriageOutcome:
    """Map a re-triage verdict onto concrete tag/folder changes. Unknown
    verdicts fail safe to still_urgent (nothing leaves the Inbox)."""
    base = [c for c in categories if not c.startswith(KEEP_UNTIL_PREFIX)]
    if verdict == "needs_response":
        return RetriageOutcome(
            verdict=verdict,
            folder="reply_required",
            new_categories=["respond" if c == "urgent" else c for c in base],
        )
    if verdict == "resolved_or_expired":
        return RetriageOutcome(
            verdict=verdict,
            folder="Archive",
            new_categories=[c for c in base if c != "urgent"],
        )
    hold = (now + timedelta(days=RETRIAGE_HOLD_DAYS)).date().isoformat()
    return RetriageOutcome(
        verdict="still_urgent",
        folder=None,
        new_categories=base + [f"{KEEP_UNTIL_PREFIX}{hold}"],
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_sweep_rules.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add services/sweep_rules.py tests/test_sweep_rules.py
git commit -m "feat: verdict-to-outcome mapping for stale-urgent re-triage"
```

---

### Task 5: Claude verdict call + re-triage service

Two pieces: a thin Claude client function returning the parsed verdict JSON (raises on garbage — policy lives above it), and `services/retriage.py` owning evidence-gathering + the fail-safe.

**Files:**
- Modify: `clients/claude.py` (add function)
- Create: `services/retriage.py`
- Test: `tests/test_retriage.py` (new)

**Interfaces:**
- Consumes: `client.get_email_details(message_id)` → `Email` (has `.subject`, `.from_display`, `.received_date`, `.get_body_text()`); `client.latest_reply_from_me(conversation_id, after) -> str | None` (Task 2); `parse_graph_datetime` (Task 3).
- Produces (Task 6 consumes): `services.retriage.evaluate(client, message_id: str, conversation_id: str | None, received_at: datetime, now: datetime) -> str` — always returns one of `"still_urgent" | "needs_response" | "resolved_or_expired"`; any internal failure returns `"still_urgent"`.
- Also produces: `clients.claude.retriage_verdict(system_prompt: str, user_message: str) -> dict` — parsed JSON with at least a `"verdict"` key; raises `ValueError` on unparseable output.

- [ ] **Step 1: Add the Claude client function**

Add to `clients/claude.py` (after `classify`):

```python
def retriage_verdict(system_prompt: str, user_message: str) -> dict:
    """Call Claude for a stale-urgent re-triage verdict. Returns the parsed
    JSON dict (must contain 'verdict'); raises ValueError on bad output —
    fail-safe handling belongs to the caller."""
    response = _get_client().messages.create(
        model="claude-sonnet-4-6",
        max_tokens=256,
        temperature=0,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )
    usage = response.usage
    otel.claude_tokens.add(usage.input_tokens, {"token_type": "input"})
    otel.claude_tokens.add(usage.output_tokens, {"token_type": "output"})
    text = response.content[0].text.strip()  # type: ignore[union-attr]
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"retriage verdict is not JSON: {text}") from e
    if "verdict" not in data:
        raise ValueError(f"retriage verdict missing 'verdict': {data}")
    return data
```

- [ ] **Step 2: Write the failing service tests**

Create `tests/test_retriage.py`:

```python
from datetime import datetime, timezone

import services.retriage as retriage


class FakeEmail:
    subject = "Server down"
    from_display = "Ops <ops@example.com>"
    received_date = "2026-07-10 12:00:00"

    def get_body_text(self):
        return "The server is down, please fix ASAP." * 200  # long -> exercises trim


class FakeClient:
    def __init__(self, email=FakeEmail(), reply=None):
        self._email = email
        self._reply = reply

    def get_email_details(self, message_id):
        return self._email

    def latest_reply_from_me(self, conversation_id, after):
        return self._reply


NOW = datetime(2026, 7, 14, 5, 0, tzinfo=timezone.utc)
RECEIVED = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)


def test_evaluate_returns_verdict_with_reply_excerpt(monkeypatch):
    seen = {}

    def fake_verdict(system_prompt, user_message):
        seen["user"] = user_message
        return {"verdict": "resolved_or_expired", "reason": "owner says it is fixed"}

    monkeypatch.setattr(retriage, "retriage_verdict", fake_verdict)
    v = retriage.evaluate(
        FakeClient(reply="Fixed it yesterday, all good."), "m1", "conv1", RECEIVED, NOW
    )
    assert v == "resolved_or_expired"
    assert "Server down" in seen["user"]
    assert "Fixed it yesterday, all good." in seen["user"]  # reply excerpt rendered
    assert len(seen["user"]) < 10000  # body trimmed


def test_evaluate_renders_no_reply_line(monkeypatch):
    seen = {}

    def fake_verdict(system_prompt, user_message):
        seen["user"] = user_message
        return {"verdict": "still_urgent"}

    monkeypatch.setattr(retriage, "retriage_verdict", fake_verdict)
    retriage.evaluate(FakeClient(reply=None), "m1", "conv1", RECEIVED, NOW)
    assert "has not replied" in seen["user"]


def test_evaluate_fail_safe_on_claude_error(monkeypatch):
    def boom(system_prompt, user_message):
        raise ValueError("bad json")

    monkeypatch.setattr(retriage, "retriage_verdict", boom)
    assert retriage.evaluate(FakeClient(), "m1", "conv1", RECEIVED, NOW) == "still_urgent"


def test_evaluate_fail_safe_on_fetch_failure(monkeypatch):
    class NoEmail(FakeClient):
        def get_email_details(self, message_id):
            return None

    assert retriage.evaluate(NoEmail(), "m1", "conv1", RECEIVED, NOW) == "still_urgent"


def test_evaluate_handles_missing_conversation(monkeypatch):
    monkeypatch.setattr(
        retriage, "retriage_verdict", lambda s, u: {"verdict": "needs_response"}
    )
    assert retriage.evaluate(FakeClient(), "m1", None, RECEIVED, NOW) == "needs_response"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_retriage.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.retriage'`.

- [ ] **Step 4: Implement the service**

Create `services/retriage.py`:

```python
"""Re-evaluate a stale urgent message: gather evidence (content, age, reply
signal) and ask Claude for a verdict. One concern: producing a verdict string.

Fail safe: any failure returns "still_urgent" — the verdict that changes
nothing. Verdicts are time-based policy, not human feedback; they never touch
current_label or emit label_applied events.
"""

import logging
from datetime import datetime
from typing import Any

from clients.claude import retriage_verdict

logger = logging.getLogger(__name__)

_BODY_LIMIT = 2000

VERDICTS = {"still_urgent", "needs_response", "resolved_or_expired"}

SYSTEM_PROMPT = """You are re-evaluating an email that was classified URGENT when it \
arrived but has sat in the owner's inbox for several days. Decide its current \
disposition.

Respond with JSON only: {"verdict": "<verdict>", "reason": "<one line>"}

Verdicts:
- "still_urgent": still time-sensitive and actionable; the owner has not dealt with it.
- "needs_response": no longer on fire, but still deserves a reply from the owner.
- "resolved_or_expired": the owner already replied, the deadline or event has passed, \
or no action is required anymore.

The owner-reply excerpt is factual (their latest Sent Items message in the thread). \
Read it for meaning: a holding reply ("on it", "will do this Friday") means the matter \
is STILL PENDING, not resolved; only a reply that actually resolves the matter supports \
"resolved_or_expired". Be conservative: when uncertain, answer "still_urgent"."""


def evaluate(
    client: Any,
    message_id: str,
    conversation_id: str | None,
    received_at: datetime,
    now: datetime,
) -> str:
    """Verdict for a stale urgent message. Always returns a member of VERDICTS."""
    try:
        email = client.get_email_details(message_id)
        if email is None:
            logger.warning("retriage: could not fetch %s — keeping urgent", message_id)
            return "still_urgent"

        reply = None
        if conversation_id:
            reply = client.latest_reply_from_me(conversation_id, received_at)
        reply_line = (
            f'Owner\'s latest reply in this thread since it arrived: "{reply[:300]}"'
            if reply
            else "Owner has not replied in this thread since it arrived."
        )

        user_message = (
            f"Subject: {email.subject}\n"
            f"From: {email.from_display}\n"
            f"Received: {email.received_date} ({(now - received_at).days} days ago)\n"
            f"Today: {now.date().isoformat()}\n"
            f"{reply_line}\n\n"
            f"Body:\n{email.get_body_text()[:_BODY_LIMIT]}"
        )
        data = retriage_verdict(SYSTEM_PROMPT, user_message)
        verdict = data.get("verdict", "")
        logger.info(
            "retriage %s -> %s (%s)", message_id, verdict, data.get("reason", "")
        )
        if verdict not in VERDICTS:
            return "still_urgent"
        return verdict
    except Exception:
        logger.warning("retriage failed for %s — keeping urgent", message_id, exc_info=True)
        return "still_urgent"
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_retriage.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add clients/claude.py services/retriage.py tests/test_retriage.py
git commit -m "feat: re-triage service — Claude verdict with reply signal, fail-safe"
```

---

### Task 6: Sweep orchestration + entry-point wiring

`run_sweep` executes the two new actions with per-message error isolation and the nightly republish cap, keeping its CI-testability by taking the retriage evaluator and publisher as injectable callables. `main.py` wires the real ones.

**Files:**
- Modify: `services/sweep.py`
- Modify: `main.py:122-136` (`sweep` entry point)
- Test: `tests/test_sweep.py` (append/extend)

**Interfaces:**
- Consumes: `decide`, `apply_verdict`, `parse_graph_datetime`, `REPUBLISH_NIGHTLY_CAP` (Tasks 3–4); `services.retriage.evaluate` (Task 5); `clients.pubsub.publish(topic, event)` (existing); client methods `move_message_to_action_folder`, `set_categories` (existing).
- Produces: `run_sweep(client, now, evaluate=None, republish=None) -> dict[str, int]` with count keys `moved, held, skipped, errored, retriaged_kept, retriaged_demoted, retriaged_archived, republished`. `evaluate` has Task 5's signature; `republish` is `Callable[[str], None]` taking the Graph message id. When either callable is `None`, its action is counted as `skipped` (safe no-op). New counts flow to Grafana automatically — `main.py` already emits every key via `otel.sweep_actions.add(n, {"action": action})`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_sweep.py`, extend `FakeClient` and add tests:

```python
class FakeClient:
    def __init__(self, messages):
        self._messages = messages
        self.moved = []
        self.stripped = []

    def list_inbox_categories(self):
        return self._messages

    def move_message_to_action_folder(self, msg_id, folder):
        self.moved.append((msg_id, folder))
        return {"id": msg_id}

    def set_categories(self, msg_id, categories):
        self.stripped.append((msg_id, categories))
        return True
```

(unchanged — shown for context) and append:

```python
def test_run_sweep_retriage_verdict_paths():
    now = datetime(2026, 7, 14, 5, 0, tzinfo=ET)
    stale = "2026-07-01T12:00:00Z"
    client = FakeClient(
        [
            {"id": "keep", "categories": ["urgent"], "receivedDateTime": stale,
             "conversationId": "c1"},
            {"id": "demote", "categories": ["urgent", "P0"], "receivedDateTime": stale,
             "conversationId": "c2"},
            {"id": "done", "categories": ["urgent"], "receivedDateTime": stale,
             "conversationId": "c3"},
        ]
    )
    verdicts = {"keep": "still_urgent", "demote": "needs_response",
                "done": "resolved_or_expired"}

    def evaluate(c, message_id, conversation_id, received_at, now_):
        return verdicts[message_id]

    counts = run_sweep(client, now, evaluate=evaluate)
    assert counts["retriaged_kept"] == 1
    assert counts["retriaged_demoted"] == 1
    assert counts["retriaged_archived"] == 1
    # kept: fresh keep_until applied in place, no move
    assert ("keep", ["urgent", "keep_until:2026-07-17"]) in client.stripped
    assert all(m[0] != "keep" for m in client.moved)
    # demoted: moved + retagged respond
    assert ("demote", "reply_required") in client.moved
    assert ("demote", ["respond", "P0"]) in client.stripped
    # archived: moved + urgent stripped
    assert ("done", "Archive") in client.moved
    assert ("done", []) in client.stripped


def test_run_sweep_republishes_untagged_with_cap(monkeypatch):
    import services.sweep_rules as rules

    monkeypatch.setattr(rules, "REPUBLISH_NIGHTLY_CAP", 2)
    now = datetime(2026, 7, 14, 5, 0, tzinfo=ET)
    old = "2026-07-01T12:00:00Z"
    client = FakeClient(
        [{"id": f"u{i}", "categories": [], "receivedDateTime": old} for i in range(4)]
    )
    published = []
    counts = run_sweep(client, now, republish=published.append)
    assert counts["republished"] == 2
    assert published == ["u0", "u1"]
    assert counts["skipped"] == 2  # over the cap


def test_run_sweep_grooming_errors_do_not_abort():
    now = datetime(2026, 7, 14, 5, 0, tzinfo=ET)
    stale = "2026-07-01T12:00:00Z"
    client = FakeClient(
        [
            {"id": "boom", "categories": ["urgent"], "receivedDateTime": stale},
            {"id": "a", "categories": ["reference"], "receivedDateTime": stale},
        ]
    )

    def evaluate(*args, **kwargs):
        raise RuntimeError("claude exploded")

    counts = run_sweep(client, now, evaluate=evaluate)
    assert counts["errored"] == 1
    assert ("a", "Archive") in client.moved  # batch continued


def test_run_sweep_without_grooming_callables_is_safe():
    now = datetime(2026, 7, 14, 5, 0, tzinfo=ET)
    old = "2026-07-01T12:00:00Z"
    client = FakeClient(
        [
            {"id": "u", "categories": ["urgent"], "receivedDateTime": old},
            {"id": "n", "categories": [], "receivedDateTime": old},
        ]
    )
    counts = run_sweep(client, now)  # no evaluate/republish
    assert counts["skipped"] == 2
    assert client.moved == []
```

Note the existing two tests (`test_run_sweep_moves_holds_skips`, `test_run_sweep_counts_move_errors`) must keep passing unchanged — their fake messages lack `receivedDateTime`, which must be tolerated (treated as `None`).

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_sweep.py -v`
Expected: new tests FAIL (`TypeError: run_sweep() got an unexpected keyword argument 'evaluate'` / missing count keys); the two existing tests still PASS.

- [ ] **Step 3: Implement**

Replace `services/sweep.py`:

```python
"""Morning sweep orchestration: list Inbox, decide per message, move/strip,
re-triage stale urgent mail, republish untagged mail for classification.

Pure of GCP/functions-framework deps so it is unit-testable in CI. The Cloud
Function entry point in main.py builds the Graph client, the retriage
evaluator, and the Pub/Sub republish callable, and calls run_sweep.
"""

import logging
from datetime import datetime
from typing import Any, Callable

import services.sweep_rules as rules
from services.sweep_rules import apply_verdict, decide, parse_graph_datetime

logger = logging.getLogger(__name__)

Evaluate = Callable[[Any, str, str | None, datetime, datetime], str]
Republish = Callable[[str], None]


def run_sweep(
    client: Any,
    now: datetime,
    evaluate: Evaluate | None = None,
    republish: Republish | None = None,
) -> dict[str, int]:
    counts = {
        "moved": 0,
        "held": 0,
        "skipped": 0,
        "errored": 0,
        "retriaged_kept": 0,
        "retriaged_demoted": 0,
        "retriaged_archived": 0,
        "republished": 0,
    }
    for msg in client.list_inbox_categories():
        received_at = parse_graph_datetime(msg.get("receivedDateTime"))
        d = decide(msg.get("categories", []), now, received_at)
        if d.action == "hold":
            counts["held"] += 1
            continue
        if d.action == "skip":
            counts["skipped"] += 1
            continue
        if d.action == "retriage":
            _retriage(client, msg, received_at, now, evaluate, counts)
            continue
        if d.action == "republish":
            _republish(msg, republish, counts)
            continue
        # action == "move"
        moved = client.move_message_to_action_folder(msg["id"], d.folder)
        if moved is None:
            counts["errored"] += 1
            logger.warning("sweep: move failed for %s -> %s", msg["id"], d.folder)
            continue
        counts["moved"] += 1
        if d.strip_categories:
            remaining = [c for c in msg.get("categories", []) if c not in d.strip_categories]
            client.set_categories(moved.get("id", msg["id"]), remaining)
    logger.info("sweep complete: %s", counts)
    return counts


def _retriage(client, msg, received_at, now, evaluate, counts) -> None:
    if evaluate is None or received_at is None:
        counts["skipped"] += 1
        return
    try:
        verdict = evaluate(client, msg["id"], msg.get("conversationId"), received_at, now)
        outcome = apply_verdict(verdict, msg.get("categories", []), now)
        if outcome.folder is None:
            client.set_categories(msg["id"], outcome.new_categories)
            counts["retriaged_kept"] += 1
            return
        moved = client.move_message_to_action_folder(msg["id"], outcome.folder)
        if moved is None:
            counts["errored"] += 1
            logger.warning("sweep: retriage move failed for %s", msg["id"])
            return
        client.set_categories(moved.get("id", msg["id"]), outcome.new_categories)
        if outcome.folder == "Archive":
            counts["retriaged_archived"] += 1
        else:
            counts["retriaged_demoted"] += 1
    except Exception:
        counts["errored"] += 1
        logger.warning("sweep: retriage failed for %s", msg["id"], exc_info=True)


def _republish(msg, republish, counts) -> None:
    if republish is None or counts["republished"] >= rules.REPUBLISH_NIGHTLY_CAP:
        counts["skipped"] += 1
        return
    try:
        republish(msg["id"])
        counts["republished"] += 1
    except Exception:
        counts["errored"] += 1
        logger.warning("sweep: republish failed for %s", msg["id"], exc_info=True)
```

(Note: `_republish` reads the cap via `rules.REPUBLISH_NIGHTLY_CAP` so the test's `monkeypatch.setattr(rules, ...)` takes effect.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_sweep.py tests/test_sweep_rules.py -v`
Expected: all PASS.

- [ ] **Step 5: Wire the entry point**

In `main.py`, replace the `sweep` function (currently lines 122–136):

```python
def sweep(request):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from clients import pubsub
    from clients.graph import get_graph_client
    from services.retriage import evaluate
    from services.sweep import run_sweep

    topic = os.environ.get("INBOX_MESSAGES_TOPIC", "inbox-messages")

    def republish(message_id: str) -> None:
        pubsub.publish(topic, {"resourceData": {"id": message_id}})

    otel.flush()
    try:
        counts = run_sweep(
            get_graph_client(),
            datetime.now(ZoneInfo("America/New_York")),
            evaluate=evaluate,
            republish=republish,
        )
        for action, n in counts.items():
            otel.sweep_actions.add(n, {"action": action})
        return (counts, 200)
    finally:
        otel.flush()
```

(`os` is already imported at the top of `main.py`; verify, add if missing. The published payload matches what the webhook publishes and what `handlers/pipeline.run` reads: `notification["resourceData"]["id"]`.)

- [ ] **Step 6: Run the full test suite**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add services/sweep.py main.py tests/test_sweep.py
git commit -m "feat: sweep executes re-triage verdicts and republishes untagged mail"
```

---

### Task 7: Processor duplicate repair

Republishing is only a useful repair if the processor does something for a message that's already in the DB. Today `pipeline.run` silently returns on duplicates. Change: if the stored message has a classification **and the live message is currently untagged**, re-apply the stored tag. The untagged check protects human corrections — a re-notified message whose tags a human changed must not be clobbered with old LLM output.

**Files:**
- Modify: `clients/azure/graph_email_client.py` (add `categories` to `get_email_details` `$select`)
- Modify: `clients/azure/email.py` (expose `categories`)
- Modify: `repo/messages.py` (add getter)
- Modify: `repo/classifications.py` (add getter)
- Modify: `handlers/pipeline.py:58-62` (duplicate branch)
- Test: `tests/test_pipeline_repair.py` (new)

**Interfaces:**
- Produces:
  - `Email.categories: list[str]` (empty list when absent).
  - `repo.messages.get_by_external_id(conn, source: str, external_id: str) -> Optional[dict]` (full row incl. `id`).
  - `repo.classifications.latest_for_message(conn, message_id: str) -> Optional[dict]` (keys `category`, `importance`, `tags`).
  - `handlers.pipeline._repair_tag_if_missing(conn, graph_client, email, db_message_id: str, external_id: str) -> None`.

- [ ] **Step 1: Expose categories on fetched emails**

In `clients/azure/graph_email_client.py::get_email_details`, add `categories` to the `$select` string (line ~387):

```python
        params = {
            "$select": "id,subject,from,toRecipients,ccRecipients,bccRecipients,receivedDateTime,sentDateTime,body,bodyPreview,isRead,hasAttachments,attachments,webLink,categories"
```

In `clients/azure/email.py::Email.__init__`, add alongside the other fields:

```python
        self.categories = data.get("categories", [])
```

- [ ] **Step 2: Add the repo getters**

`repo/messages.py`:

```python
def get_by_external_id(
    conn: psycopg.Connection, source: str, external_id: str
) -> Optional[dict]:
    return conn.execute(
        "SELECT * FROM messages WHERE source = %s AND external_id = %s",
        (source, external_id),
    ).fetchone()
```

`repo/classifications.py`:

```python
def latest_for_message(conn: psycopg.Connection, message_id: str) -> Optional[dict]:
    return conn.execute(
        """
        SELECT category, importance, tags
        FROM classifications
        WHERE message_id = %s
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (message_id,),
    ).fetchone()
```

- [ ] **Step 3: Write the failing tests**

Create `tests/test_pipeline_repair.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_pipeline_repair.py -v`
Expected: FAIL — `ImportError: cannot import name '_repair_tag_if_missing'`.

- [ ] **Step 5: Implement the repair in `handlers/pipeline.py`**

Add near the top (after existing imports):

```python
from models.types import Category

_KNOWN_CATEGORY_TAGS = {c.value for c in Category}
```

Add the helper (module level, below `run`):

```python
def _repair_tag_if_missing(conn, graph_client, email, db_message_id, external_id) -> None:
    """A duplicate notification for a stored message: if the live message is
    untagged but a classification exists, re-apply its tag. Makes republishing
    a safe universal repair action. Never overwrites existing tags — a tagged
    message may carry a human correction."""
    live_tags = set(getattr(email, "categories", []) or [])
    if live_tags & _KNOWN_CATEGORY_TAGS:
        return
    stored = classifications.latest_for_message(conn, db_message_id)
    if stored is None:
        logger.warning(
            "Duplicate %s exists in DB with no classification — manual attention", external_id
        )
        return
    categories = [stored["category"]]
    if stored.get("importance"):
        categories.append(stored["importance"])
    categories += stored.get("tags") or []
    graph_client.tag_message(external_id, categories)
    logger.info("Repaired missing tag on %s -> %s", external_id, categories)
```

Replace the duplicate branch in `run` (currently `pipeline.py:59-62`):

```python
                existing = messages.get_by_external_id(
                    conn, msg["source"], msg["external_id"]
                )
                if existing:
                    logger.debug(f"Duplicate {msg['external_id']} — repairing if untagged")
                    otel.emails_duplicates.add(1)
                    _repair_tag_if_missing(
                        conn, graph_client, email, existing["id"], msg["external_id"]
                    )
                    return
```

(`messages.exists` keeps its other callers, if any — do not delete it.)

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_pipeline_repair.py tests/ -v`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add clients/azure/graph_email_client.py clients/azure/email.py repo/messages.py repo/classifications.py handlers/pipeline.py tests/test_pipeline_repair.py
git commit -m "feat: processor repairs missing tags on duplicate notifications"
```

---

### Task 8: Terraform — publisher IAM + sweep env/secrets

The sweep CF runs as the processor's service account (`google_service_account.process_cf` — see `terraform/cloud_functions.tf:500`), which already has accessor rights on `anthropic-api-key` but **no publisher role on the `inbox-messages` topic** (only the webhook SA publishes today, `terraform/iam.tf:110`).

**Files:**
- Modify: `terraform/iam.tf`
- Modify: `terraform/cloud_functions.tf` (sweep function block, ~line 484)

**Interfaces:**
- Consumes: env var names from Task 6 — `INBOX_MESSAGES_TOPIC`, `ANTHROPIC_API_KEY`.

- [ ] **Step 1: Add the publisher binding**

In `terraform/iam.tf`, after the existing `process_cf_email_events_publisher` resource (line ~95):

```hcl
# Sweep (runs as process_cf SA) republishes untagged Inbox mail for classification
resource "google_pubsub_topic_iam_member" "process_cf_messages_publisher" {
  topic  = google_pubsub_topic.inbox_messages.name
  role   = "roles/pubsub.publisher"
  member = "serviceAccount:${google_service_account.process_cf.email}"
}
```

- [ ] **Step 2: Add sweep env + Anthropic secret**

In `terraform/cloud_functions.tf`, in the `google_cloudfunctions2_function "sweep"` `service_config` block: extend `environment_variables`:

```hcl
    environment_variables = {
      GCP_PROJECT_ID       = var.project_id
      MSAL_SECRET_NAME     = "msal-token-cache"
      INBOX_MESSAGES_TOPIC = google_pubsub_topic.inbox_messages.name
    }
```

and add alongside the existing `secret_environment_variables` blocks:

```hcl
    secret_environment_variables {
      key        = "ANTHROPIC_API_KEY"
      project_id = var.project_id
      secret     = google_secret_manager_secret.secrets["anthropic-api-key"].secret_id
      version    = "latest"
    }
```

(Match the exact reference style of the processor function's `ANTHROPIC_API_KEY` block in the same file — copy it.)

- [ ] **Step 3: Plan**

Use the `/terraform-plan` skill (it handles credentials and posts results). Expected changes: 1 IAM member to add, 1 function to update in-place. No destroys.

- [ ] **Step 4: Commit**

```bash
git add terraform/iam.tf terraform/cloud_functions.tf
git commit -m "infra: sweep publishes to inbox-messages + gets Anthropic key"
```

(Apply happens after merge via the `/terraform-apply` skill — see Task 9.)

---

### Task 9: Docs, PR, local verification, rollout

**Files:**
- Modify: `CLAUDE.md` (Project state + Sweep row)

- [ ] **Step 1: Update CLAUDE.md**

In the **Project state** section, extend the deferred-folder-moves paragraph (or add a sibling paragraph):

```markdown
**Inbox grooming (shipped):** the 5 AM sweep also grooms what it can't file. Urgent messages older than 3 days are re-triaged by Claude using the message content and the text of Ben's latest reply in the thread (if any): `still_urgent` re-holds them via a `keep_until:+3d` tag (re-checked every 3 days), `needs_response` demotes them to `respond`/`reply_required`, `resolved_or_expired` archives them. Verdicts are policy, never human feedback — they don't touch `current_label`. Untagged Inbox mail older than 24 h is republished (≤50/night) to the `inbox-messages` topic for normal classification; the processor repairs missing tags on duplicate notifications, so republishing is a safe universal repair. See `docs/superpowers/specs/2026-07-17-inbox-grooming-design.md`.
```

Update the **Sweep** row in the Stack table:

```markdown
| **Sweep** | Cloud Function `inbox-sweep` (HTTP, Cloud Scheduler `0 5 * * *` America/New_York) — files Inbox mail by its current category tag; re-triages stale urgent mail via Claude; republishes untagged mail for classification |
```

- [ ] **Step 2: Run the full suite one last time**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: all PASS.

- [ ] **Step 3: Commit docs**

```bash
git add CLAUDE.md
git commit -m "docs: record inbox grooming in project state"
```

- [ ] **Step 4: Open the PR**

Use the `pr-open` skill (repo rule — don't hand-roll push + `gh pr create`).

- [ ] **Step 5: Verify locally**

Use the `verifying-pr-locally` skill against the branch. E2E checks from the spec:
- Plant an urgent-tagged test message dated 4+ days back; run the sweep locally (call `services.sweep.run_sweep` with the real Graph client, real `services.retriage.evaluate`, and `republish=None`); confirm the verdict path taken — for `still_urgent`, the applied `keep_until:` tag is visible on the message.
- Plant an untagged message >24 h old; run the sweep with a `republish` that invokes the local pipeline directly (per the `testing-inbox-pipeline` skill); verify it gets tagged; run the sweep again and verify it files.
- For a DB row that has a classification but an untagged live message, run the pipeline with a duplicate notification and verify the tag is re-applied.

- [ ] **Step 6: Merge + deploy + apply**

After review: merge the PR, watch the deploy with the `monitoring-inbox-deploy` skill, then run the `/terraform-apply` skill for the IAM + env changes. Watch the first morning's sweep logs (`fetch-inbox-logs` skill) for verdict distribution; tune the re-triage prompt if it is over-eager.
