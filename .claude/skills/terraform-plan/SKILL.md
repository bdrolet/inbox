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

### 2. Run the plan

```bash
cd /Users/ben/src/inbox/terraform
terraform plan -no-color 2>&1
```

`db_password` is a required sensitive variable. It must be present in `terraform.tfvars` (gitignored). If the plan errors with "No value for required variable", tell the user to add `db_password = "..."` to `terraform/terraform.tfvars`.

### 3. Parse the output

From the plan output, extract:
- **Summary line** — e.g. `Plan: 3 to add, 0 to change, 1 to destroy.`
- **Resources to add** — list each `# <resource> will be created`
- **Resources to change** — list each `# <resource> will be updated in-place`
- **Resources to destroy** — list each `# <resource> will be destroyed` — flag these prominently with ⚠️
- **Errors** — any `Error:` blocks

### 4. Check for an open PR

```bash
gh pr view --json number,url,title 2>/dev/null
```

If a PR exists, post a comment. If not, just print the summary to the user.

### 5. Post the PR comment

Use `gh pr comment <number>` with a body structured as:

````
## `terraform plan`

**Result: <summary line>**

<brief description of what this plan does — 1-2 sentences synthesizing the intent>

### Resources to add (<count>)
- `<resource_type>.<name>` — <one-line description of what it is>

### Resources to change (<count>)
- `<resource_type>.<name>` — <what's changing>

### ⚠️ Resources to destroy (<count>)
- `<resource_type>.<name>` — <what it is and why it's being removed>

### Errors
<any errors, or omit this section if none>

<details>
<summary>Full plan output</summary>

```
<full plan output>
```

</details>
````

## Notes

- The plan is **read-only** — it never modifies GCP resources.
- Cloud SQL provisioning (`google_sql_database_instance`) takes 5–10 minutes on first apply; the plan will note it as a new resource.
- Resources being destroyed that belong to the old GKE worker (`inbox_worker` SA, `inbox_messages_pull` subscription) are expected — the worker is being retired.
