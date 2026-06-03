---
name: deploy-inbox
description: Use when the user wants to deploy, release, or push code changes to the inbox-process Cloud Function, rebuild the processor after changing main.py or any file under clients/, repo/, services/, handlers/, or models/.
metadata:
  depends-on:
    - terraform-apply
---

# Deploying the Inbox Processor

Deploys the `inbox-process` Cloud Function by running `terraform apply`. Terraform re-zips the repo root, uploads to GCS, and triggers a Cloud Function redeploy whenever the source changes.

**REQUIRED:** Use the **terraform-apply** skill to run the apply. It handles credentials, PR comments, and output parsing.

## What gets deployed

- **`inbox-process`** — Pub/Sub-triggered processor CF (source: repo root, excluding `.venv`, `.git`, `.claude`, `terraform/`, `docs/`)
- Any other resources with pending Terraform changes (Cloud SQL, IAM, etc.) will also be applied

## Context to give terraform-apply

Tell terraform-apply that this deploy updates the `inbox-process` Cloud Function source. Mention any specific Phase or feature being deployed so the PR comment is accurate (e.g., "Phase 2: bge embeddings").

## After a successful deploy

Read `agents/run-health-checks.md`, then spawn it (fast model). Pass:
- `project`: `bens-project-462804`
- `region`: `us-central1`
- `check_embeddings`: `true` for Phase 2+ deploys, `false` otherwise

The subagent runs all four checks in parallel (function version, processor logs, renew health, webhook activity) and returns a structured health summary. Include the full summary in the PR comment below.

If the subagent can't run (e.g. gcloud not authenticated), run the checks inline sequentially as a fallback.

## Post a PR comment with results

After the checks above, post a single comment on the open PR summarizing the full deploy:

```bash
gh pr comment <number> --body "$(cat <<'EOF'
## Deploy: inbox-process ✅ (or ❌)

**Updated:** <updateTime from gcloud describe>

### Terraform result
<paste the apply summary line from terraform-apply, e.g. "Apply complete! Resources: 0 added, 1 changed, 0 destroyed.">

### Function logs (post-deploy)
\`\`\`
<last 30 lines from gcloud functions logs read>
\`\`\`

### Notes
<any observations — errors, cold start, embedding count, etc.>
EOF
)"
```

If the deploy failed, lead with ❌ and include the error from the apply or logs.

## Cold start note

After deploying a change that adds `sentence-transformers` (Phase 2+), the first invocation will take ~60s due to model loading. This is expected — subsequent warm invocations reuse the module-level `_model` singleton.
