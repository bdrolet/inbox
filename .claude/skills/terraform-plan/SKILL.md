---
name: terraform-plan
description: Use when the user wants to run a terraform plan for the inbox infrastructure, preview what changes would be applied, or check what GCP resources would be created, modified, or destroyed.
---

# Terraform Plan

Runs `terraform plan` in `~/src/inbox/terraform`, then posts the output as a PR comment if a PR exists for the current branch.

## Steps

### 1. Check prerequisites

```bash
# Verify GCP credentials are valid
gcloud auth application-default print-access-token &>/dev/null && echo "OK" || echo "EXPIRED"
```

If credentials are expired, stop and tell the user to run:
```bash
gcloud auth application-default login
```

### 2. Check for an open PR

```bash
gh pr view --json number -q '.number' 2>/dev/null
```

Note the PR number (or null if none).

### 3. Spawn run-plan-and-comment (fast model)

Read `agents/run-plan-and-comment.md`, then spawn it. Pass:
- `working_dir`: `/Users/ben/src/inbox/terraform`
- `pr_number`: PR number from step 2, or `null`
- `description`: brief description of what this plan does, inferred from context

The subagent runs the plan (verbose output stays in its context), parses the result, and posts the PR comment. It returns a summary line plus `has_destroys` and `has_errors` flags.

If `has_destroys: true`, flag the destroyed resources prominently to the user before they proceed to apply. If `has_errors: true`, stop and surface the error.

## Notes

- The plan is **read-only** — it never modifies GCP resources.
- Cloud SQL provisioning (`google_sql_database_instance`) takes 5–10 minutes on first apply; the plan will note it as a new resource.
- Resources being destroyed that belong to the old GKE worker (`inbox_worker` SA, `inbox_messages_pull` subscription) are expected — the worker is being retired.
