---
name: monitoring-inbox-deploy
description: Use when a PR has just been merged to main and the user wants to watch the GitHub Actions deploy workflow, confirm it succeeded, or diagnose and fix a failure. Also use when a deploy is in progress, when checking if the latest code is live, or when a deploy failed and needs to be investigated and re-triggered.
metadata:
  depends-on:
    - fetch-inbox-logs
---

# Monitoring Inbox Deploy

Watches the GitHub Actions `deploy.yml` workflow for `bdrolet/inbox` after a merge and fixes failures.

**Repo:** `bdrolet/inbox` | **Workflow:** `deploy.yml` | **Triggers:** push to `main` when `main.py`, `clients/**`, `handlers/**`, `services/**`, `models/**`, `functions/**`, `requirements.txt`, or `terraform/**` change.

## Watch the active run

```bash
# Find the latest run ID
gh run list --workflow=deploy.yml --repo bdrolet/inbox --limit=3 --json databaseId,status,conclusion,createdAt,headBranch

# Stream output until complete
gh run watch <run-id> --repo bdrolet/inbox
```

## If the run fails

### 1. Get logs for the failed step

```bash
gh run view <run-id> --log-failed --repo bdrolet/inbox
```

### 2. Common failure modes

| Symptom in logs | Cause | Fix |
|----------------|-------|-----|
| `No value for required variable` | Missing `TF_VAR_*` GitHub Actions secret | `gh secret set TF_VAR_<NAME> --body "..." --repo bdrolet/inbox` |
| `Error acquiring the state lock` | Stale Terraform state lock | Find lock ID in error, then force-unlock via `terraform force-unlock` locally |
| `Permission denied` on a Secret Manager resource | Missing IAM `secretAccessor` binding | Add binding in `terraform/iam.tf`, commit, push to main |
| Python import error / module not found during CF build | Dependency missing from `requirements.txt` | Add dep, push fix |
| `object of type 'NoneType' has no len()` in pg8000 | No-param query passed `None` to pg8000 — known fixed in `clients/db.py` | Verify fix is present |

### 3. Re-trigger after pushing a fix

The workflow auto-triggers on push to main if relevant paths changed. If you need to trigger it manually without a code push:

```bash
gh workflow run deploy.yml --repo bdrolet/inbox --ref main
```

## Verify the deploy landed

```bash
# Check function update time and revision
gcloud functions describe inbox-process \
  --region=us-central1 \
  --project=bens-project-462804 \
  --format='value(updateTime,serviceConfig.revision)'
```

Then use **fetch-inbox-logs** to confirm the function is processing without errors on the next invocation.
