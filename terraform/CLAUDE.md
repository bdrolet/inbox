# Terraform

GCP infrastructure for the inbox project (Cloud Functions, Pub/Sub, Cloud SQL, Scheduler, Secrets, IAM).

## Workflow

After making any change to a Terraform file (`*.tf`, `*.tfvars`) in this directory, run the `/terraform-plan` skill to preview the change before applying. Use `/terraform-apply` to apply once the plan looks correct.

These skills handle credential checks, run the command, and post results as a PR comment automatically — don't hand-roll `terraform plan`/`terraform apply`.
