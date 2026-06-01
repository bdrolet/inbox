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

### 3. Run the apply

```bash
cd /Users/ben/src/inbox/terraform
terraform apply -auto-approve -no-color 2>&1
```

This runs non-interactively. Cloud SQL instance creation can take 5–10 minutes.

### 4. Parse the output

From the apply output, extract:
- **Result** — `Apply complete! Resources: N added, N changed, N destroyed.` or error
- **Resources created** — each `<resource>: Creation complete`
- **Resources modified** — each `<resource>: Modifications complete`
- **Resources destroyed** — each `<resource>: Destruction complete`
- **Key outputs** — any `Outputs:` block (e.g. `cloud_sql_connection_name`, `webhook_url`)
- **Errors** — any `Error:` blocks with the affected resource

### 5. Post the result PR comment

Use `gh pr comment <number>` with a body structured as:

````
## `terraform apply` — complete ✅  (or ❌ if failed)

**<Apply complete! Resources: N added, N changed, N destroyed.>**

<1-2 sentence summary of what was provisioned and what it enables>

### Resources created
- `<resource>` — <what it is>

### Resources modified
- `<resource>` — <what changed>

### Resources destroyed
- `<resource>` — <what was removed and why>

### Outputs
```
<outputs block if present>
```

### Next steps
<context-appropriate next steps — e.g. "Run scripts/migrate_db.py to apply the schema" or "Register the Graph subscription if expired">

<details>
<summary>Full apply output</summary>

```
<full output>
```

</details>
````

If the apply **failed**, lead with ❌, show the error clearly at the top, and suggest a fix.

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
