# email_classified Graph Fields + Calendar Code Removal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Hand all calendar logic to the schedule service. Phase A (one PR, safe now): put `graph_message_id` and `has_attachments` on every `email_classified` event so schedule can fetch `.ics` attachments itself. Phase B (second PR, only after schedule v1.1 is live and verified): delete inbox's own calendar code — `.ics` detection, `calendar_invites`, the Graph RSVP path, RSVP links — so inbox owns mail only.

**Architecture:** Phase A touches the payload seam only: `Message` gains `has_attachments` (from Graph's `hasAttachments`), and `build_event` copies `external_id` → `graph_message_id` and `has_attachments` onto the event. Nothing consumes them in inbox. Phase B removes `services/calendar_invite.py`, `services/calendar_response.py`, `repo/calendar_invites.py` (+ table DDL), `email_events.invite_extras`, `handlers/actions/_shared.py::prepare` and its three callers' invite seeds, the `/calendar` route in `functions/webhook/main.py`, the `calendar_action` CF + `inbox-calendar` topic in terraform, `CalendarInvite`, the `icalendar` dependency, and their tests. Tasks keeps working throughout: the new keys are ignored, and the invite seeds simply become `None`.

**Tech Stack:** Python 3.11, existing pytest suite (with the `clients.db` `sys.modules` stub pattern), ruff, mypy, Terraform.

**Spec:** schedule repo `docs/superpowers/specs/2026-08-17-invite-mirroring-rsvp-relay-design.md` (revised 2026-08-17: "Ownership change", "Payload change", "Rollout / gating"). Consumer plan: schedule repo `docs/superpowers/plans/2026-08-17-invite-mirroring-rsvp-relay.md` (its Task 1 defines the mirror of the payload; its Task 18 points here).

## Global Constraints

- Wire shape (Phase A) is fixed by schedule's `models/events.py::EmailClassifiedEvent` and must match key-for-key:
  ```json
  "graph_message_id": "AAMkAGI2...",   // Message.external_id — immutable Graph message id (or the group conversation id for group posts; schedule treats a 404 as "no invite")
  "has_attachments":  true              // Graph message.hasAttachments; false when unknown
  ```
- MSAL cache hygiene (Phase A): `msal-token-cache` had **727 enabled versions** on 2026-08-17 (Secret Manager bills per enabled version, ~$0.06/version/month). Every writer prunes to the newest `MSAL_CACHE_KEEP_VERSIONS` (default 3) after `add_secret_version`; schedule does the same on its side. `secretVersionManager` (already granted to inbox's writer SAs) includes `destroy`. Best-effort, never raises.
- Additive only in Phase A: no existing key changes; tasks' `models/events.py` mirror needs no change (`NotRequired`; unknown keys ignored).
- **Phase B is gated.** Do not start Task 4+ until schedule v1.1 is deployed and the schedule plan's rollout checklist steps 1–6 have passed (one Google event per invite; RSVP round-trip via Graph confirmed). During the gap, inbox's `/calendar` path still exists but nothing new links to it — harmless.
- After Phase B the ownership rule is: **inbox owns mail (classify, tag, publish); schedule owns everything calendar.** Inbox must not grow calendar code again.
- Layer rules as `CLAUDE.md`. Test modules that import anything reaching `clients.db` must install the `clients.db` `sys.modules` stub first, exactly as `tests/test_calendar_response.py` / `tests/test_email_events.py` do.
- Tooling: `.venv/bin/pytest tests/ -q`, `.venv/bin/ruff check . && .venv/bin/ruff format --check .`, `.venv/bin/mypy clients/ services/ handlers/ models/ repo/ main.py`, `cd terraform && terraform init -backend=false && terraform validate`.
- Git: Phase A on branch `feat/email-events-graph-fields`, Phase B on `feat/remove-calendar-code`, both off `main`; commit per task; open each PR with `/pr-open` (repo rule — no hand-rolled push/PR).

---

## File structure

| File | Phase | Status | Responsibility |
|---|---|---|---|
| `models/message.py` | A | modify | `has_attachments: bool` |
| `services/ingestion.py` | A | modify | `normalize` sets `has_attachments=email.has_attachments` |
| `services/email_events.py` | A | modify | `build_event` adds `graph_message_id`, `has_attachments` |
| `tests/test_email_events.py`, `tests/test_ingestion_fetch.py` | A | modify | payload + normalize tests |
| `clients/azure/graph_email_client.py` | A | modify | `_save_cache_to_secret_manager` prunes old enabled versions (keep newest 3) |
| `scripts/prune_msal_cache_versions.py` | A | create | one-off backlog cleanup (727 → 3) |
| `tests/test_graph_msal_cache.py` | A | create | prune helper tests |
| `CLAUDE.md` | A, B | modify | payload note (A); ownership paragraph (B) |
| `services/calendar_invite.py`, `services/calendar_response.py`, `repo/calendar_invites.py`, `handlers/actions/_shared.py`, `tests/test_calendar_response.py` | B | delete | inbox calendar code |
| `models/types.py` | B | modify | remove `CalendarInvite` |
| `services/email_events.py` | B | modify | remove `invite_extras`; docstring |
| `handlers/actions/review.py`, `respond.py`, `urgent.py` | B | modify | drop `prepare()` + invite seeds |
| `functions/webhook/main.py` | B | modify | remove `/calendar` route + `inbox-calendar` publisher |
| `main.py` | B | modify | remove `calendar_action` entry point |
| `terraform/pubsub.tf`, `terraform/iam.tf`, `terraform/cloud_functions.tf` | B | modify | remove `inbox_calendar` topic, publisher binding, `calendar_action` CF |
| `repo/schema.sql` | B | modify | remove `calendar_invites` DDL (comment with manual `DROP TABLE`) |
| `requirements.txt`, `requirements-dev.txt` | B | modify | remove `icalendar` |
| `tests/test_email_events.py` | B | modify | remove invite tests |

---

## Phase A — additive payload fields

### Task 0: Branch

- [ ] **Step 1: Branch off up-to-date main and confirm green baseline**

```bash
cd /Users/ben/src/inbox
git checkout main && git pull --ff-only
git checkout -b feat/email-events-graph-fields
.venv/bin/pytest tests/ -q
```

Expected: all tests pass.

---

### Task 1: `Message.has_attachments` from Graph

**Files:**
- Modify: `models/message.py`
- Modify: `services/ingestion.py` (`normalize`)
- Test: `tests/test_ingestion_fetch.py`

**Interfaces:**
- Produces: `Message.has_attachments: bool` (TypedDict key; `normalize` sets it from `Email.has_attachments`, which the Graph client fills from `hasAttachments`).

- [ ] **Step 1: Write the failing test**

Look at how `tests/test_ingestion_fetch.py` builds an `Email` for `normalize` (it has a helper/fixture for the Graph email dict). Add:

```python
def test_normalize_carries_has_attachments():
    from clients.azure.email import Email
    from services.ingestion import normalize

    email = Email(
        {
            "id": "AAMk1",
            "subject": "s",
            "hasAttachments": True,
            "from": {},
            "toRecipients": [],
            "ccRecipients": [],
            "body": {},
        }
    )
    assert normalize(email)["has_attachments"] is True
    email2 = Email(
        {
            "id": "AAMk2",
            "subject": "s",
            "from": {},
            "toRecipients": [],
            "ccRecipients": [],
            "body": {},
        }
    )
    assert normalize(email2)["has_attachments"] is False
```

(If the `Email` constructor in that test file needs more keys to avoid attribute errors — e.g. `receivedDateTime` — copy the minimal dict the neighbouring tests already use.)

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/test_ingestion_fetch.py -q`
Expected: FAIL — `KeyError: 'has_attachments'`.

- [ ] **Step 3: Implement**

`models/message.py` — add after `web_link`:

```python
    has_attachments: bool  # Graph hasAttachments — schedule uses it to decide whether to fetch .ics
```

`services/ingestion.py::normalize` — add to the `Message(...)` constructor:

```python
has_attachments = (bool(getattr(email, "has_attachments", False)),)
```

- [ ] **Step 4: Run tests + types**

Run: `.venv/bin/pytest tests/test_ingestion_fetch.py -q && .venv/bin/mypy clients/ services/ handlers/ models/ repo/ main.py`
Expected: PASS. (mypy: any other `Message(...)` literal constructions in `services/`/`handlers/` must now pass `has_attachments=` — `grep -rn "Message(" services handlers` and add `has_attachments=False` where a real value isn't available.)

- [ ] **Step 5: Commit**

```bash
git add models/message.py services/ingestion.py tests/test_ingestion_fetch.py
git commit -m "feat(message): carry Graph hasAttachments on Message"
```

---

### Task 2: `graph_message_id` + `has_attachments` on `email_classified`

**Files:**
- Modify: `services/email_events.py`
- Test: `tests/test_email_events.py`

**Interfaces:**
- Produces: `build_event(...)["graph_message_id"] == msg["external_id"]`, `build_event(...)["has_attachments"] == bool(msg.get("has_attachments"))`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_email_events.py` (uses its `_msg()`/`_classification()` helpers):

```python
def test_build_event_carries_graph_message_id_and_has_attachments(monkeypatch):
    monkeypatch.delenv("REDIRECTOR_BASE_URL", raising=False)
    msg = {**_msg(), "has_attachments": True}
    ev = email_events.build_event(msg, _classification(), None)
    assert ev["graph_message_id"] == "ext-1"
    assert ev["has_attachments"] is True


def test_build_event_has_attachments_defaults_false(monkeypatch):
    monkeypatch.delenv("REDIRECTOR_BASE_URL", raising=False)
    ev = email_events.build_event(
        _msg(), _classification(), None
    )  # _msg has no has_attachments key
    assert ev["has_attachments"] is False and ev["graph_message_id"] == "ext-1"
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/test_email_events.py -q`
Expected: FAIL — `KeyError: 'graph_message_id'`.

- [ ] **Step 3: Implement**

In `services/email_events.py::build_event`, after `"seed_links": extras.get("seed_links"),` add:

```python
        # Schedule fetches .ics attachments itself (owns all calendar logic):
        # immutable Graph id + a cheap gate so it only calls Graph when needed.
        "graph_message_id": msg["external_id"],
        "has_attachments": bool(msg.get("has_attachments", False)),
```

Add to the module docstring: "`graph_message_id` + `has_attachments` exist for the schedule repo (github.com/bdrolet/schedule), which owns all calendar logic and reads `.ics` attachments via Graph itself."

- [ ] **Step 4: Run tests + lint**

Run: `.venv/bin/pytest tests/test_email_events.py -q && .venv/bin/ruff check . && .venv/bin/ruff format --check .`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/email_events.py tests/test_email_events.py
git commit -m "feat(email_events): graph_message_id + has_attachments on email_classified"
```

---

### Task 2b: Prune old `msal-token-cache` versions on write + one-off backlog cleanup

**Files:**
- Modify: `clients/azure/graph_email_client.py` (`_save_cache_to_secret_manager`)
- Create: `scripts/prune_msal_cache_versions.py`
- Test: `tests/test_graph_msal_cache.py`

**Interfaces:**
- Produces:
  ```python
  GraphEmailClient._prune_cache_versions(client, parent: str, keep: int) -> int   # module-level helper `prune_secret_versions(client, parent, keep)`; destroys all but newest `keep` ENABLED versions; never raises
  ```
  Env: `MSAL_CACHE_KEEP_VERSIONS` (default `3`).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_graph_msal_cache.py`:

```python
from types import SimpleNamespace

from clients.azure import graph_email_client as gec


class _V:
    def __init__(self, n, state="ENABLED"):
        self.name = f"projects/p/secrets/msal-token-cache/versions/{n}"
        self.state = SimpleNamespace(name=state)


class _Client:
    def __init__(self, versions):
        self.versions, self.destroyed = versions, []

    def list_secret_versions(self, request):
        return list(self.versions)  # Secret Manager returns newest first

    def destroy_secret_version(self, request):
        self.destroyed.append(request["name"])


def test_prune_keeps_newest_enabled_and_destroys_rest():
    c = _Client([_V(9), _V(8), _V(7, "DESTROYED"), _V(6), _V(5), _V(4)])
    assert gec.prune_secret_versions(c, "projects/p/secrets/msal-token-cache", keep=3) == 2
    assert c.destroyed == [
        "projects/p/secrets/msal-token-cache/versions/5",
        "projects/p/secrets/msal-token-cache/versions/4",
    ]


def test_prune_never_raises():
    class _Boom:
        def list_secret_versions(self, request):
            raise RuntimeError("perm")

    assert gec.prune_secret_versions(_Boom(), "projects/p/secrets/x", keep=3) == 0
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/test_graph_msal_cache.py -q`
Expected: FAIL — `AttributeError: module ... has no attribute 'prune_secret_versions'`. (If importing `graph_email_client` pulls in modules unavailable in CI, add the same `sys.modules` stubs the neighbouring tests use.)

- [ ] **Step 3: Implement**

In `clients/azure/graph_email_client.py`, add a module-level helper (near the top, after `logger`):

```python
def prune_secret_versions(client, parent: str, keep: int) -> int:
    """Destroy every ENABLED version of `parent` except the newest `keep`.
    Secret Manager bills per enabled version and every silent MSAL refresh
    adds one across all writer services (inbox's CFs + the schedule repo).
    Best-effort — never raises; readers always fetch `latest`."""
    try:
        versions = list(client.list_secret_versions(request={"parent": parent}))
        enabled = [v for v in versions if getattr(v.state, "name", str(v.state)) == "ENABLED"]
        destroyed = 0
        for v in enabled[keep:]:  # newest first
            client.destroy_secret_version(request={"name": v.name})
            destroyed += 1
        if destroyed:
            logger.info("Pruned %d old MSAL cache versions (kept %d)", destroyed, keep)
        return destroyed
    except Exception:
        logger.warning("MSAL cache version prune failed (non-fatal)", exc_info=True)
        return 0
```

and in `_save_cache_to_secret_manager`, after `client.add_secret_version(...)`:

```python
prune_secret_versions(client, parent, keep=int(os.getenv("MSAL_CACHE_KEEP_VERSIONS", "3")))
```

Create `scripts/prune_msal_cache_versions.py`:

```python
#!/usr/bin/env python3
"""One-off: destroy old msal-token-cache versions (there were 727 on 2026-08-17).

  GCP_PROJECT_ID=bens-project-462804 .venv/bin/python scripts/prune_msal_cache_versions.py [--keep 3] [--dry-run]

Requires secretmanager.versions.destroy on the secret (owner / secretVersionManager).
Ongoing hygiene is prune-on-write in graph_email_client + schedule's clients/graph.py.
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from google.cloud import secretmanager

from clients.azure.graph_email_client import prune_secret_versions


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", type=int, default=3)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--secret", default=os.getenv("MSAL_SECRET_NAME", "msal-token-cache"))
    args = ap.parse_args()
    parent = f"projects/{os.environ['GCP_PROJECT_ID']}/secrets/{args.secret}"
    client = secretmanager.SecretManagerServiceClient()
    enabled = [
        v
        for v in client.list_secret_versions(request={"parent": parent})
        if v.state.name == "ENABLED"
    ]
    print(f"{len(enabled)} enabled versions; keeping {args.keep}")
    if args.dry_run:
        return
    print("destroyed", prune_secret_versions(client, parent, keep=args.keep))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests + lint; run the cleanup once**

Run: `.venv/bin/pytest tests/test_graph_msal_cache.py -q && .venv/bin/ruff check .`
Expected: PASS.

Then, once (needs `gcloud auth application-default login` as an owner):

```bash
GCP_PROJECT_ID=bens-project-462804 .venv/bin/python scripts/prune_msal_cache_versions.py --dry-run
GCP_PROJECT_ID=bens-project-462804 .venv/bin/python scripts/prune_msal_cache_versions.py
gcloud secrets versions list msal-token-cache --project=bens-project-462804 --filter="state=enabled" --format="value(name)" | wc -l   # → 3
```

- [ ] **Step 5: Commit**

```bash
git add clients/azure/graph_email_client.py scripts/prune_msal_cache_versions.py tests/test_graph_msal_cache.py
git commit -m "chore(msal): prune old token-cache secret versions on write; one-off backlog cleanup"
```

---

### Task 3: Phase A docs + PR

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: CLAUDE.md**

In the "**Calendar-service extraction (shipped)**" paragraph, append: "Since schedule v1.1 the event also carries `graph_message_id` (immutable Graph id) and `has_attachments`, so schedule can read `.ics` attachments via Graph itself; inbox's own invite detection and RSVP path are being retired (see `docs/superpowers/plans/2026-08-17-email-events-invite-payload.md`, Phase B)."

- [ ] **Step 2: Verify + commit + PR**

```bash
.venv/bin/pytest tests/ -q && .venv/bin/ruff check . && .venv/bin/ruff format --check . \
  && .venv/bin/mypy clients/ services/ handlers/ models/ repo/ main.py
git add CLAUDE.md
git commit -m "docs: graph fields on email_classified for schedule v1.1"
```

Invoke `/pr-open`. Title: `feat: graph_message_id + has_attachments on email_classified (schedule v1.1)`. Body: additive; tasks unaffected; MSAL cache version pruning (727 → 3, prune-on-write); consumed by schedule PR `<link>`; Phase B (removal) follows once schedule v1.1 is verified.

**⛔ Gate:** stop here until schedule v1.1 is deployed and its rollout checklist steps 1–6 pass.

---

## Phase B — remove inbox's calendar code (after schedule v1.1 is live)

### Task 4: Branch

- [ ] **Step 1**

```bash
cd /Users/ben/src/inbox
git checkout main && git pull --ff-only
git checkout -b feat/remove-calendar-code
.venv/bin/pytest tests/ -q
```

Expected: all tests pass (Phase A merged).

---

### Task 5: Action handlers stop detecting invites; drop `invite_extras`, `prepare`, `CalendarInvite`

**Files:**
- Modify: `handlers/actions/review.py`, `handlers/actions/respond.py`, `handlers/actions/urgent.py`
- Delete: `handlers/actions/_shared.py`, `services/calendar_invite.py`, `repo/calendar_invites.py`
- Modify: `services/email_events.py` (remove `invite_extras`, `urllib.parse`/`CalendarInvite` imports if now unused; docstring)
- Modify: `models/types.py` (remove `CalendarInvite`)
- Test: `tests/test_email_events.py`

**Interfaces:**
- Produces: `review.handle` returns `{}`; `respond.handle` returns `{"draft_link": ...}` or `{}`; `urgent.handle` returns `{}` (after the ntfy push). `build_event` still emits `seed_key_points`/`seed_links` from `extras` (both now `None`) — the keys stay in the schema.

- [ ] **Step 1: Update tests first**

In `tests/test_email_events.py`: delete `_invite()`, `test_invite_extras_builds_points_and_rsvp_links`, `test_invite_extras_none_invite`, `test_review_returns_invite_seeds`, and any `monkeypatch.setattr(<handler>, "prepare", ...)` lines (the handlers no longer have `prepare`); remove the `CalendarInvite` import. Add:

```python
def test_review_returns_no_extras():
    assert review.handle(_classification(), _msg()) == {}


def test_urgent_returns_no_extras_even_when_push_fails(monkeypatch):
    monkeypatch.setattr(urgent.ntfy, "notify", lambda **kw: 1 / 0)
    assert urgent.handle(_classification(Category.URGENT), _msg()) == {}


def test_build_event_seed_fields_are_none_without_extras(monkeypatch):
    monkeypatch.delenv("REDIRECTOR_BASE_URL", raising=False)
    ev = email_events.build_event(_msg(), _classification(), {})
    assert ev["seed_key_points"] is None and ev["seed_links"] is None
```

Keep `test_respond_returns_draft_link` but remove its `prepare` patch line.

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/test_email_events.py -q`
Expected: FAIL — `review.handle` still returns `{"seed_key_points": None, "seed_links": None}` ≠ `{}` (and `AttributeError` for missing `prepare` patches once removed).

- [ ] **Step 3: Implement**

`handlers/actions/review.py`:

```python
from models.message import Message
from models.types import Classification


def handle(classification: Classification, msg: Message) -> dict:
    """Review needs no inbox-side enrichment; tasks/schedule act on the event."""
    return {}
```

`handlers/actions/respond.py` — remove the `prepare` import/call and `invite_extras`; start with `extras: dict = {}` then the existing draft block.

`handlers/actions/urgent.py` — remove `prepare`/`invite_extras`; keep the ntfy try/except; `return {}`.

Delete `handlers/actions/_shared.py`, `services/calendar_invite.py`, `repo/calendar_invites.py`.

`services/email_events.py` — delete `invite_extras` and now-unused imports (`urllib.parse`, `CalendarInvite`); rewrite the docstring paragraph: "Calendar invites are not inbox's concern: the schedule repo owns all calendar logic and reads `.ics` attachments via Graph using `graph_message_id`/`has_attachments` below. `seed_key_points`/`seed_links` remain generic hooks for handler extras."

`models/types.py` — delete the `CalendarInvite` dataclass (and `datetime` import if unused).

- [ ] **Step 4: Run tests + lint + types**

Run: `.venv/bin/pytest tests/ -q && .venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy clients/ services/ handlers/ models/ repo/ main.py`
Expected: PASS (`tests/test_calendar_response.py` will now fail to import — that is Task 6; run it after Task 6 or delete it in this step and note it in the commit).

- [ ] **Step 5: Commit**

```bash
git add -A handlers/actions services/email_events.py models/types.py tests/test_email_events.py
git rm -q services/calendar_invite.py repo/calendar_invites.py handlers/actions/_shared.py
git commit -m "refactor: drop invite detection and RSVP links — schedule owns calendar logic"
```

---

### Task 6: Remove the Graph RSVP path (`/calendar`, `inbox-calendar`, `calendar_action`)

**Files:**
- Delete: `services/calendar_response.py`, `tests/test_calendar_response.py`
- Modify: `functions/webhook/main.py` (remove `/calendar` branch, `_calendar_topic`, and the tuple slot in `_publisher_client`; update module docstring)
- Modify: `main.py` (remove `calendar_action` entry point + docstring line)
- Modify: `terraform/pubsub.tf` (remove `google_pubsub_topic.inbox_calendar`), `terraform/iam.tf` (remove `webhook_cf_calendar_publisher`), `terraform/cloud_functions.tf` (remove `google_cloudfunctions2_function.calendar_action` and its IAM/outputs if any)
- Modify: `repo/schema.sql` (remove the `calendar_invites` DDL; add a comment `-- calendar_invites moved to the schedule service (schedule owns calendar logic). Drop from live installs once confirmed idle: DROP TABLE IF EXISTS calendar_invites;`)
- Modify: `requirements.txt`, `requirements-dev.txt` (remove `icalendar`)
- Test: `tests/test_main.py` (if it references `calendar_action`) — remove; webhook function tests, if any, that hit `/calendar` — remove.

**Interfaces:**
- `_publisher_client()` in `functions/webhook/main.py` now returns `(publisher, messages_topic, labels_topic)`; update its callers in the same file.

- [ ] **Step 1: Delete code and tests**

```bash
git rm -q services/calendar_response.py tests/test_calendar_response.py
```

`main.py`: delete the `calendar_action` function and its docstring line; drop now-unused imports.

`functions/webhook/main.py`: delete the `if request.path == "/calendar":` block; remove `_calendar_topic` global and its topic_path line; change `_publisher_client()` to return a 3-tuple and fix its unpacking site(s); update the top-of-file docstring to drop the `/calendar` line.

- [ ] **Step 2: Terraform**

Delete `resource "google_pubsub_topic" "inbox_calendar"` (pubsub.tf), `resource "google_pubsub_topic_iam_member" "webhook_cf_calendar_publisher"` (iam.tf), and the whole `resource "google_cloudfunctions2_function" "calendar_action"` block plus any IAM member or output referencing it (cloud_functions.tf; `grep -n calendar terraform/*.tf` must return nothing).

- [ ] **Step 3: Schema + deps**

`repo/schema.sql`: replace the `calendar_invites` CREATE TABLE with the comment above. `requirements.txt` / `requirements-dev.txt`: remove the `icalendar` line. Then `.venv/bin/pip uninstall -y icalendar` (optional) and `grep -rn "icalendar\|calendar_invites\|calendar_response\|inbox-calendar\|calendar_action" --exclude-dir=.venv --exclude-dir=.git --exclude-dir=docs .` — must be empty (docs may keep history).

- [ ] **Step 4: Verify**

```bash
.venv/bin/pytest tests/ -q && .venv/bin/ruff check . && .venv/bin/ruff format --check . \
  && .venv/bin/mypy clients/ services/ handlers/ models/ repo/ main.py \
  && (cd terraform && terraform init -backend=false && terraform validate)
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "refactor: remove Graph RSVP path (/calendar, inbox-calendar, calendar_action) — schedule owns it"
```

---

### Task 7: Docs + PR (Phase B)

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: CLAUDE.md**

Replace the "**Calendar-service extraction (shipped)**" paragraph with:

> **Calendar ownership (schedule v1.1, shipped):** inbox owns mail only — classification, tagging, and the `email_classified` feed. The separate `schedule` repo (github.com/bdrolet/schedule) owns *everything* calendar: it reads `.ics` attachments via Graph using the event's `graph_message_id`/`has_attachments`, mirrors invites into Google Calendar by iCalendar UID, and writes Ben's RSVPs back to his Exchange calendar via Graph (`/me/events/{id}/accept|decline|tentativelyAccept`) — sharing this repo's Azure app + `msal-token-cache` (schedule's SAs have accessor + versionManager on it). Inbox has no calendar code, no `calendar_invites` table, no `/calendar` webhook route and no `inbox-calendar` topic. Do not add calendar logic here again. See schedule's `docs/superpowers/specs/2026-08-17-invite-mirroring-rsvp-relay-design.md`.

Also remove any Stack-table or "Cloud Functions" list entry for `inbox-calendar-action`, and any mention of the `/calendar` route in the webhook description.

- [ ] **Step 2: Commit + PR**

```bash
git add CLAUDE.md
git commit -m "docs: inbox owns mail only; calendar logic lives in schedule"
```

Invoke `/pr-open`. Title: `refactor: remove inbox calendar code — schedule owns calendar logic`. Body: what was deleted (code, topic, CF, table DDL, dependency); the terraform destroy list (`inbox-calendar` topic, `inbox-calendar-action` CF, publisher binding — plan output must show only these destroys); manual `DROP TABLE IF EXISTS calendar_invites;` after merge; note that schedule's SAs still consume `msal-token-cache` / `client-id` / `client-secret` / `tenant-id` from this repo's Secret Manager, so those must never be destroyed here.

---

## Sequencing with the schedule repo

1. Phase A PR merges first (or in either order relative to schedule — schedule v1 ignores the keys).
2. Schedule v1.1 deploys, migrates, `/renew`, E2E verified (schedule plan rollout steps 1–6).
3. Phase B PR merges; `terraform apply` destroys the topic + CF; drop the table.
4. `docs/superpowers/plans/2026-08-17-remove-google-calendar.md` remains historical (already shipped).
