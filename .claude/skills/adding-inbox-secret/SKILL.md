---
name: adding-inbox-secret
description: Use when adding a new secret, API key, or credential to the inbox project — wiring it into .env, Terraform Secret Manager, the Cloud Function env vars, GitHub Actions secrets, and the CI deploy workflow. Also use when asked to "add a secret", "wire in a token", or "add credentials" for the inbox stack.
metadata:
  depends-on: terraform-plan, terraform-apply
---

## What needs to change (6 places)

For a secret named `my-secret` (kebab-case in Terraform, `MY_SECRET` as env var):

### 1. `.env` (local dev)
```
MY_SECRET=<value>
```

### 2. `terraform/variables.tf`
```hcl
variable "my_secret" {
  description = "<what it's for>"
  type        = string
  sensitive   = true
  default     = ""
}
```

### 3. `terraform/secrets.tf` — add to `locals.secrets` map
```hcl
"my-secret" = var.my_secret
```
The existing `for_each` resource creates the Secret Manager secret and grants access automatically.

### 4. `terraform/cloud_functions.tf` — add to `inbox-process` `service_config`
```hcl
secret_environment_variables {
  key        = "MY_SECRET"
  project_id = var.project_id
  secret     = google_secret_manager_secret.secrets["my-secret"].secret_id
  version    = "latest"
}
```
Add to the `inbox-label` or `inbox-webhook` CFs too if they need it.

### 5. `terraform/terraform.tfvars.example` — add placeholder
```
my_secret = "..."  # <where to get it>
```

### 5b. `terraform/terraform.tfvars` — add real value (gitignored)
```
my_secret = "<actual value>"
```

### 6. `.github/workflows/deploy.yml` — add to the `env:` block of the Terraform apply step
```yaml
TF_VAR_my_secret: ${{ secrets.TF_VAR_MY_SECRET }}
```

## Order of operations

1. Edit the 6 files above (`.env`, `variables.tf`, `secrets.tf`, `cloud_functions.tf`, `tfvars.example`, `deploy.yml`)
2. Add the real value to `terraform/terraform.tfvars` (gitignored)
3. **REQUIRED:** Use the **terraform-plan** skill — review the plan, confirm the new secret resource appears
4. Ask the user to approve before applying
5. **REQUIRED:** Use the **terraform-apply** skill
6. Add the GitHub Actions secret:
   ```bash
   gh secret set TF_VAR_MY_SECRET --body "<value>" --repo bdrolet/inbox
   ```
   Verify with: `gh secret list --repo bdrolet/inbox | grep MY_SECRET`

## IAM note

The `locals.secrets` `for_each` automatically creates:
- `google_secret_manager_secret_iam_member.accessor` for `job_sa`

If the secret needs to be readable by `webhook_cf` or `process_cf` SAs, check `terraform/iam.tf` — those SAs may need explicit accessor bindings added there (as was done for the Grafana OTLP secrets).
