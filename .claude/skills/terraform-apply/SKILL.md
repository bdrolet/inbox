---
name: terraform-apply
description: Use when the user wants to run a terraform apply for the inbox infrastructure, deploy GCP resources, or provision Cloud SQL / Cloud Functions / IAM changes.
---

# Terraform Apply

Runs `terraform apply` in `~/src/inbox/terraform`, then posts the output as a PR comment if a PR exists for the current branch.

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

Check that `db_password` is in `terraform/terraform.tfvars` (gitignored):
```bash
grep -q "db_password" /Users/ben/src/inbox/terraform/terraform.tfvars && echo "found" || echo "missing"
```

If missing, ask the user for a password or generate one:
```bash
openssl rand -base64 24 | tr -d '/+=' | head -c 32
```
Then append to `terraform.tfvars`: `db_password = "<generated>"`

### 2. Check for an open PR and post a "starting" comment

```bash
gh pr view --json number,url,title 2>/dev/null
```

If a PR exists, post a comment **before** running apply so the PR shows activity:

```
## `terraform apply` — starting

Applying Terraform changes. This will provision/modify the following GCP resources:
<brief description from context — e.g. "Creating Cloud SQL instance, processor Cloud Function, and IAM bindings for inbox-process-cf SA.">

⏳ Will post results when complete.
```

### 3. Spawn run-apply-and-comment (fast model)

Read `agents/run-apply-and-comment.md`, then spawn it. Pass:
- `working_dir`: `/Users/ben/src/inbox/terraform`
- `pr_number`: PR number from step 2, or `null`
- `description`: brief description of what this apply provisions, inferred from context

The subagent runs the apply (verbose output — including any 5–10 min Cloud SQL wait — stays in its context), parses the result, and posts the PR comment. It returns `result` (success/failure), a summary line, and any key outputs.

If `result: failure`, surface the error to the user immediately.

## Notes

- **Cloud SQL** (`google_sql_database_instance`) takes 5–10 minutes to provision on first apply. This is normal.
- After a successful apply that creates Cloud SQL, the next step is running the schema migration:
  ```bash
  CLOUD_SQL_CONNECTION_NAME=$(terraform -chdir=/Users/ben/src/inbox/terraform output -raw cloud_sql_connection_name) \
    POSTGRES_USER=inbox POSTGRES_PASSWORD=<db_password> POSTGRES_DB=app \
    python /Users/ben/src/inbox/scripts/migrate_db.py
  ```
- The `inbox-messages-pull` Pub/Sub subscription and `inbox-worker` SA being destroyed are expected — the GKE worker is being retired.
- `terraform.tfvars` is gitignored and contains real secrets. Never log or print its contents.
