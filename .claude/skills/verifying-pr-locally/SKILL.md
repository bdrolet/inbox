---
name: verifying-pr-locally
version: 1.0.0
description: >
  Use when verifying that inbox changes actually work by running them locally —
  typically after a PR is open and before merging, but at any time on request.
  Use when asked to "test things locally", "make sure this works", "verify the
  branch", or "run an E2E check" on inbox code. Covers the API (uvicorn + real
  Graph), services, and clients; posts results to the open PR when one exists.
metadata:
  depends-on: "verify, pr-post, refreshing-msal-token, testing-inbox-pipeline"
---

# Verifying a PR Locally

Verification is runtime observation, not a test-suite rerun. Invoke the `verify` skill
first for the discipline (surface, probes, evidence); this skill supplies the
inbox-specific handles.

## 1. Build the test plan from the diff

```bash
git diff main...HEAD --stat   # or the PR's base branch
```

Map changed files to surfaces:

| Changed | Surface | Handle |
|---|---|---|
| `api/` , `services/`, `clients/` reached by the API | HTTP on local uvicorn | below |
| pipeline path (`handlers/`, `main.py`, classification services) | **REQUIRED:** use the `testing-inbox-pipeline` skill | — |
| `functions/webhook|renew`, `terraform/` | no local surface — say so; suggest `terraform-plan` / post-merge monitoring | — |

Plan = happy-path check per changed behavior **plus adversarial probes** the diff points
at (bad/duplicate params, wrong method, empty values, auth on/off). Write the plan down
before running.

## 2. Local API handle

```bash
env -u GCP_PROJECT_ID -u SEARCH_TOKEN ./.venv/bin/uvicorn api.main:app --port 8124 --log-level warning
```
(run in background; cleanup with `pkill -f "uvicorn api.main:app --port 812"`)

Gotchas that cost time if forgotten:

- **`POST /search` does not work locally** — it hardcodes `authenticate_headless()`
  (Secret Manager). Source real message IDs from the prod API instead:
  `TOKEN=$(grep 'search_token' terraform/terraform.tfvars | grep -o '"[^"]*"' | tr -d '"')`
  then `curl -s -X POST https://inbox-api-aizbgjlava-uc.a.run.app/search -H "Authorization: Bearer $TOKEN" ...`
- Read endpoints auth via silent MSAL from `~/.inbox-token-cache.json`. If auth fails →
  **REQUIRED:** use the `refreshing-msal-token` skill.
- To probe token auth, start a second server with `SEARCH_TOKEN=probe-secret` set and
  expect 401/401/200 for missing/wrong/correct bearer.
- Group test data: list the account's M365 groups (real conversations exist, e.g.
  `allcompany@drolet.cloud`) via `clients.graph.get_graph_client()` + `get_member_groups()`.
- Broken venv (dangling `python3.13` symlink): `brew install python@3.13`, recreate
  `.venv`, pip install all three requirements files.

## 3. Execute, fix, re-run

Run every planned check; capture actual response bodies / status codes as evidence. On
failure: fix the code, commit to the PR branch (explicit `git add <files>` only — the
working tree often carries unrelated dirty files), re-run the failed check plus its
neighbors, and `git push`.

## 4. Report

Compose a verdict (PASS/FAIL) + a table of checks (happy path and probes separately) with
observed results, plus notes for anything that made you pause.

If an open PR exists for the branch (`gh pr view --json state,number`): post the report as
a PR comment per the **pr-post** skill — read `~/.claude/skills/pr-post/SKILL.md` and
apply its steps manually, including the PHI/security scrub (it is user-invocable only;
the Skill tool cannot launch it). Mask personal email subjects/addresses that aren't
infrastructure. If no PR exists, report inline and say posting was skipped.

Always kill the uvicorn servers when done.
