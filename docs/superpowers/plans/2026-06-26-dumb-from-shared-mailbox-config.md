# Dumb `from` Address + Shared-Mailbox Config Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Use superpowers:test-driven-development — write the failing test before the implementation in every task.

**Goal:** Make the outbound-email API client dumb. Callers should send only a `from` email address and never have to know whether that address is a shared mailbox. The server decides shared-vs-not from configuration.

**Architecture:** Today `api/routers/emails.py` carries `FromMailbox.shared`, resolves it inline (`_from_parts`), and calls the Graph client directly — so a Graph routing detail leaks all the way up to the HTTP client. We move the *policy* ("is this address a shared mailbox?") out of the router and into the service layer, sourced from a single config object:

- A new root-level **`config.py`** owns env parsing. A frozen `Config` dataclass normalizes `SHARED_MAILBOXES` once into a `frozenset[str]` and exposes `is_shared(address)` (a case-insensitive membership test) plus the set itself (for enumeration).
- A new **`services/sending.py`** owns the outbound-email concern. Its functions take only `from_address`, resolve `from_shared` via `get_config().is_shared(...)`, and call the Graph client.
- **`api/routers/emails.py`** becomes thin: it drops `shared` from `FromMailbox`, deletes `_from_parts`/`_get_client`, calls the sending service, and keeps only HTTP concerns (token auth + Graph-error→HTTPException mapping).
- **`api/routers/search.py`** stops re-parsing `SHARED_MAILBOXES` and reads the same config object, so the one registry has a single normalization.

The Graph client is unchanged: the mechanical `from_shared` → path routing (`_mailbox_base` / `_build_message`) stays where it belongs (I/O). `from_shared` survives only on the service→client seam; it never reaches the router again.

**Tech Stack:** Python 3.13 (FastAPI app), stdlib `dataclasses` + `functools.lru_cache`, pytest. **No new dependencies** — pydantic is only transitively present and `pydantic-settings` is NOT installed; do not add it.

## Global Constraints

- **Branch:** Do this on a dedicated branch off `main` (e.g. `feat/dumb-from-address`). Do NOT build on `feat/self-healing-subscription` — that branch carries unrelated unmerged renew/Terraform work.
- **No new dependencies.** `config.py` uses only the standard library (`os`, `dataclasses`, `functools`). Do not introduce `pydantic-settings` or any settings library.
- **`config.py` is a project leaf.** It must not import from `clients/`, `repo/`, `services/`, `handlers/`, `api/`, or `models/`. Everything may import *it*; it imports nothing from the project. This keeps the dependency arrow one-way and preserves the "`functions/` standalone, minimal deps" rule.
- **Layer rules hold (`CLAUDE.md`):** `clients/` stays I/O-only — do NOT put the shared-mailbox lookup in the Graph client. The policy lives in `services/sending.py` (one concern), driven by `config.py`.
- **API behavior must be preserved exactly:**
  - Shared mailbox (`from_address` in `SHARED_MAILBOXES`) → path-targets `/users/{addr}/messages` (`from_shared=True`).
  - Alias / M365 group (any other non-empty `from_address`) → `/me` + message-level `from` header (`from_shared=False`).
  - No `from` → primary mailbox, no `from` header.
- **Normalization is symmetric.** The set is lowercased AND the looked-up address is lowercased. Centralize both in `Config.is_shared()` so no caller can forget.
- **`@lru_cache` caveat:** `get_config()` reads env once per process (correct for Cloud Functions / the API container). Any test that sets env vars MUST call `get_config.cache_clear()` (or build `Config.from_env()` directly) to observe the change.
- **Scope discipline:** Migrate only `SHARED_MAILBOXES` and `SEARCH_TOKEN` into `config.py` in this PR. Do NOT sweep the other ~13 files that read `os.environ`; that's a separate follow-up.

---

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `config.py` | Root config leaf: env parsing, `Config` dataclass, `get_config()`, `is_shared()` | Create |
| `services/sending.py` | Outbound-email service; resolves shared from config, calls Graph client | Create |
| `api/routers/emails.py` | Drop `shared` from API; call sending service; keep HTTP concerns only | Modify |
| `api/routers/search.py` | Read shared mailboxes from config instead of re-parsing env | Modify |
| `tests/test_config.py` | Unit tests for env parsing, normalization, membership | Create |
| `tests/test_sending.py` | Unit tests: sending service resolves shared and forwards to client | Create |
| `CLAUDE.md` | Note `config.py` + `SHARED_MAILBOXES` semantics under Code layout | Modify |

---

## Task 1: `config.py` root config leaf (unit-tested)

**Files:**
- Create: `config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces:
  - `Config` — frozen dataclass with `shared_mailboxes: frozenset[str]` and `search_token: str | None`.
  - `Config.from_env() -> Config` — parses env; splits `SHARED_MAILBOXES` on `,`, strips, drops blanks, lowercases into a `frozenset`; reads `SEARCH_TOKEN` (empty → `None`).
  - `Config.is_shared(address: str | None) -> bool` — `False` for `None`/empty; otherwise `address.strip().lower() in self.shared_mailboxes`.
  - `get_config() -> Config` — `@lru_cache(maxsize=1)` wrapper over `Config.from_env()`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_config.py`:

```python
import importlib

import config as config_mod
from config import Config


def _fresh(monkeypatch, **env):
    """Build a Config from a clean env snapshot, bypassing the process cache."""
    monkeypatch.delenv("SHARED_MAILBOXES", raising=False)
    monkeypatch.delenv("SEARCH_TOKEN", raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return Config.from_env()


def test_shared_mailboxes_parsed_and_normalized(monkeypatch):
    cfg = _fresh(monkeypatch, SHARED_MAILBOXES=" Team@x.com ,  ops@X.com , ")
    assert cfg.shared_mailboxes == frozenset({"team@x.com", "ops@x.com"})


def test_empty_shared_mailboxes(monkeypatch):
    assert _fresh(monkeypatch).shared_mailboxes == frozenset()
    assert _fresh(monkeypatch, SHARED_MAILBOXES="").shared_mailboxes == frozenset()


def test_is_shared_case_insensitive(monkeypatch):
    cfg = _fresh(monkeypatch, SHARED_MAILBOXES="team@x.com")
    assert cfg.is_shared("TEAM@x.com") is True
    assert cfg.is_shared(" team@x.com ") is True
    assert cfg.is_shared("someone@x.com") is False


def test_is_shared_handles_none_and_empty(monkeypatch):
    cfg = _fresh(monkeypatch, SHARED_MAILBOXES="team@x.com")
    assert cfg.is_shared(None) is False
    assert cfg.is_shared("") is False


def test_search_token_blank_is_none(monkeypatch):
    assert _fresh(monkeypatch, SEARCH_TOKEN="").search_token is None
    assert _fresh(monkeypatch, SEARCH_TOKEN="secret").search_token == "secret"


def test_get_config_is_cached(monkeypatch):
    monkeypatch.setenv("SHARED_MAILBOXES", "a@x.com")
    config_mod.get_config.cache_clear()
    first = config_mod.get_config()
    monkeypatch.setenv("SHARED_MAILBOXES", "b@x.com")  # ignored until cache_clear
    assert config_mod.get_config() is first
    config_mod.get_config.cache_clear()
    assert config_mod.get_config().shared_mailboxes == frozenset({"b@x.com"})
```

Run: `pytest tests/test_config.py` — must fail (no `config` module yet).

- [ ] **Step 2: Implement `config.py`**

```python
"""Process configuration sourced from environment variables.

Root-level leaf module: imports nothing from the project so every layer can
depend on it without creating cycles. Values are read once per process via
get_config() (see the @lru_cache note in the implementation plan).
"""
import os
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class Config:
    shared_mailboxes: frozenset[str]
    search_token: str | None

    @staticmethod
    def from_env() -> "Config":
        raw = os.environ.get("SHARED_MAILBOXES", "")
        return Config(
            shared_mailboxes=frozenset(
                m.strip().lower() for m in raw.split(",") if m.strip()
            ),
            search_token=os.environ.get("SEARCH_TOKEN") or None,
        )

    def is_shared(self, address: str | None) -> bool:
        if not address:
            return False
        return address.strip().lower() in self.shared_mailboxes


@lru_cache(maxsize=1)
def get_config() -> Config:
    return Config.from_env()
```

- [ ] **Step 3: Verify** — `pytest tests/test_config.py` passes.

---

## Task 2: `services/sending.py` outbound-email service (unit-tested)

**Files:**
- Create: `services/sending.py`
- Test: `tests/test_sending.py`

**Interfaces:** functions accept only `from_address` (never `shared`/`from_shared`), resolve shared internally, and call `clients.graph.get_graph_client()`:
- `create_draft(*, to, subject, body, cc=[], bcc=[], body_type="Text", from_address=None) -> dict`
- `add_attachment(message_id, name, content_bytes_b64, content_type=None, *, from_address=None, is_inline=False) -> dict`
- `send_draft(message_id, *, from_address=None) -> None`
- `send_message(*, to, subject, body, cc=[], bcc=[], body_type="Text", from_address=None) -> None`

Each computes `from_shared = get_config().is_shared(from_address)` and forwards every other argument unchanged to the Graph client method of the same name.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_sending.py`:

```python
import config as config_mod
import services.sending as sending


class _FakeClient:
    def __init__(self):
        self.calls = []

    def create_draft(self, **kwargs):
        self.calls.append(("create_draft", kwargs))
        return {"id": "d1", "webLink": "http://x"}

    def send_message(self, **kwargs):
        self.calls.append(("send_message", kwargs))

    def send_draft(self, message_id, **kwargs):
        self.calls.append(("send_draft", {"message_id": message_id, **kwargs}))

    def add_attachment(self, message_id, name, content_bytes_b64, content_type=None, **kwargs):
        self.calls.append(("add_attachment", {"message_id": message_id, **kwargs}))
        return {"id": "a1"}


def _patch(monkeypatch, shared_csv=""):
    monkeypatch.setenv("SHARED_MAILBOXES", shared_csv)
    config_mod.get_config.cache_clear()
    fake = _FakeClient()
    monkeypatch.setattr(sending, "get_graph_client", lambda: fake)
    return fake


def test_shared_address_resolves_from_shared_true(monkeypatch):
    fake = _patch(monkeypatch, "team@x.com")
    sending.create_draft(to=["a@b.com"], subject="s", body="b", from_address="team@x.com")
    _, kwargs = fake.calls[0]
    assert kwargs["from_shared"] is True
    assert kwargs["from_address"] == "team@x.com"


def test_alias_address_resolves_from_shared_false(monkeypatch):
    fake = _patch(monkeypatch, "team@x.com")
    sending.send_message(to=["a@b.com"], subject="s", body="b", from_address="alias@x.com")
    _, kwargs = fake.calls[0]
    assert kwargs["from_shared"] is False


def test_no_from_address_is_not_shared(monkeypatch):
    fake = _patch(monkeypatch, "team@x.com")
    sending.send_draft("msg-1")
    _, kwargs = fake.calls[0]
    assert kwargs["from_shared"] is False
    assert kwargs["message_id"] == "msg-1"
```

Run: `pytest tests/test_sending.py` — must fail (no `services.sending` yet).

- [ ] **Step 2: Implement `services/sending.py`**

```python
"""Outbound email: drafts, attachments, sends.

Owns the shared-mailbox policy — callers pass only a from-address, and this
service decides shared-vs-not from config before delegating to the Graph
client (which handles the mechanical path routing).
"""
from clients.graph import get_graph_client
from config import get_config


def create_draft(*, to, subject, body, cc=None, bcc=None, body_type="Text", from_address=None):
    return get_graph_client().create_draft(
        to=to, subject=subject, body=body, cc=cc or [], bcc=bcc or [],
        body_type=body_type,
        from_address=from_address,
        from_shared=get_config().is_shared(from_address),
    )


def add_attachment(message_id, name, content_bytes_b64, content_type=None, *,
                   from_address=None, is_inline=False):
    return get_graph_client().add_attachment(
        message_id, name, content_bytes_b64, content_type,
        from_address=from_address,
        from_shared=get_config().is_shared(from_address),
        is_inline=is_inline,
    )


def send_draft(message_id, *, from_address=None):
    get_graph_client().send_draft(
        message_id,
        from_address=from_address,
        from_shared=get_config().is_shared(from_address),
    )


def send_message(*, to, subject, body, cc=None, bcc=None, body_type="Text", from_address=None):
    get_graph_client().send_message(
        to=to, subject=subject, body=body, cc=cc or [], bcc=bcc or [],
        body_type=body_type,
        from_address=from_address,
        from_shared=get_config().is_shared(from_address),
    )
```

> **Note on auth:** `clients.graph.get_graph_client()` authenticates and raises `RuntimeError` on failure (vs the router's old `_get_client()` which raised `HTTPException(503)`). The router maps that in Task 3 — see `_call_graph`.

- [ ] **Step 3: Verify** — `pytest tests/test_sending.py` passes.

---

## Task 3: Make `api/routers/emails.py` dumb

**Files:**
- Modify: `api/routers/emails.py`

**Changes:**

- [ ] **Step 1:** Drop `shared` from `FromMailbox`:
  ```python
  class FromMailbox(BaseModel):
      address: str | None = None
  ```

- [ ] **Step 2:** Delete `_from_parts` and `_get_client` entirely.

- [ ] **Step 3:** Add `import services.sending as sending` and a tiny helper for the address:
  ```python
  def _from_address(from_: FromMailbox | None) -> str | None:
      return from_.address if from_ else None
  ```

- [ ] **Step 4:** Rewrite the four write endpoints to call the service through `_call_graph`. Example (`create_draft`):
  ```python
  @router.post("/drafts", response_model=DraftResponse)
  def create_draft(req: CreateDraftRequest, _: None = Depends(_verify_token)) -> DraftResponse:
      created = _call_graph(
          sending.create_draft,
          to=req.to, subject=req.subject, body=req.body,
          cc=req.cc, bcc=req.bcc, body_type=req.body_type,
          from_address=_from_address(req.from_),
      )
      return DraftResponse(id=created.get("id"), web_link=created.get("webLink"))
  ```
  Apply the same shape to `add_attachment`, `send_draft`, and `send_message` — pass `from_address=_from_address(req.from_)` and drop all `shared`/`from_shared` references. `send_draft` keeps its `req.from_ if req else None` guard.

- [ ] **Step 5:** Extend `_call_graph` to map the service auth failure to 503 (it previously came from `_get_client`):
  ```python
  except RuntimeError as e:           # get_graph_client() auth failure
      raise HTTPException(status_code=503, detail="authentication failed") from e
  ```
  Keep the existing `ValueError → 400`, `HTTPError 403 → 403`, other `HTTPError → 502` branches.

- [ ] **Step 6:** The read endpoints (`get_email`, `get_attachments`) still use a client directly. Leave their behavior intact, but since `_get_client` is deleted, have them call `get_graph_client()` and let `_call_graph`/the 503 mapping cover auth — or keep a minimal local acquisition. Simplest: wrap their client acquisition the same way the writes do. Preserve the existing 404 "message not found" behavior for `get_email`.

- [ ] **Step 7: Verify** — `from FromMailbox` no longer has `shared`; `grep -n "shared\|from_shared\|_from_parts\|_get_client" api/routers/emails.py` returns nothing. Run `pytest` (full suite) — green.

---

## Task 4: Point `api/routers/search.py` at config

**Files:**
- Modify: `api/routers/search.py`

- [ ] **Step 1:** Replace the inline env parse (lines ~92–93):
  ```python
  # before
  shared = [m.strip() for m in os.environ.get("SHARED_MAILBOXES", "").split(",") if m.strip()]
  mailboxes = ["me"] + shared
  # after
  from config import get_config
  mailboxes = ["me", *get_config().shared_mailboxes]
  ```
  Order among shared mailboxes is irrelevant for fan-out, so iterating the `frozenset` is fine.

- [ ] **Step 2 (optional, do it):** Have both routers' `_verify_token` read `get_config().search_token` instead of `os.environ.get("SEARCH_TOKEN")`, so the token has one source too. Behavior unchanged: missing token → auth disabled.

- [ ] **Step 3:** Remove the now-unused `import os` if nothing else in the file needs it (`emails.py` may still need it — check per file).

- [ ] **Step 4: Verify** — `pytest`; a quick manual `TestClient` smoke (the app builds via `api/main.py`) returns 200 on a search with `SHARED_MAILBOXES` set.

---

## Task 5: Docs

**Files:**
- Modify: `CLAUDE.md`

- [ ] Under **Code layout**, add `config.py` (root config leaf) and `services/sending.py` (outbound email) rows.
- [ ] Note that `SHARED_MAILBOXES` (comma-separated) is the single registry the API uses to decide both which mailboxes to search and whether an outbound `from` address is a shared mailbox; the API client only ever sends a `from` address.

---

## Final Verification

- [ ] `pytest` — full suite green.
- [ ] `grep -rn "FromMailbox" api/` shows no `shared` field and no client/router code branching on it.
- [ ] `grep -rn "SHARED_MAILBOXES" .` shows it read only inside `config.py`.
- [ ] Manual: a `POST /emails/send` with `{"from": {"address": "<a shared mailbox>"}}` routes shared; with an alias address it stamps the `from` header — confirm via Graph client logs (`base=/users/...` vs `base=/me`).
- [ ] Open the PR with the `/pr-open` skill (per `CLAUDE.md` workflow).
