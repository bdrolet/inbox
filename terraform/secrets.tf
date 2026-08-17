locals {
  secrets = {
    "client-id"             = var.client_id
    "client-secret"         = var.client_secret
    "tenant-id"             = var.tenant_id
    "anthropic-api-key"     = var.anthropic_api_key
    "msal-token-cache"      = var.msal_token_cache
    "inbox-db-password"     = var.db_password
    "hubspot-token"         = var.hubspot_token
    "hf-token"              = var.hf_token
    "search-token"          = var.search_token
    "graph-subscription-id" = var.graph_subscription_id
  }

  # Secrets whose live value is updated at runtime (by the renew/process CFs) and
  # must not be overwritten by CI / Terraform after their initial seed.
  self_managed_secrets   = ["msal-token-cache", "graph-subscription-id"]
  auto_versioned_secrets = { for k, v in local.secrets : k => v if !contains(local.self_managed_secrets, k) }
}

resource "google_secret_manager_secret" "secrets" {
  for_each  = local.secrets
  secret_id = each.key

  replication {
    auto {}
  }

  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret_version" "secrets" {
  for_each    = local.auto_versioned_secrets
  secret      = google_secret_manager_secret.secrets[each.key].id
  secret_data = each.value
}

# msal-token-cache deliberately has NO Terraform-managed version resource.
#
# `local.self_managed_secrets` above already declares this secret runtime-owned and excludes it
# from `auto_versioned_secrets`; a standalone version resource was the leftover contradiction.
# Every writer (inbox's CFs, inbox-api, the schedule/tasks repos) adds a version on each silent
# MSAL refresh and prunes all but the newest MSAL_CACHE_KEEP_VERSIONS enabled versions. A
# Terraform-managed version would eventually be destroyed by that prune, drop out of state as
# DESTROYED, and be re-created from the CI `TF_VAR_MSAL_TOKEN_CACHE` seed on the next apply —
# publishing a stale cache as `latest` and breaking headless Graph auth everywhere.
# (`lifecycle.ignore_changes` does not help: it suppresses diffs on an existing resource, not a
# create.) So the *secret* stays Terraform-managed; its *versions* are runtime-owned. The first
# version must be seeded manually — the repo's `refreshing-msal-token` skill does that.

# Separate resource so lifecycle.ignore_changes prevents CI/Terraform from
# overwriting the live subscription ID the renew CF writes on self-heal.
resource "google_secret_manager_secret_version" "graph_subscription_id" {
  secret      = google_secret_manager_secret.secrets["graph-subscription-id"].id
  secret_data = var.graph_subscription_id

  lifecycle {
    ignore_changes = [secret_data]
  }
}

# ntfy-token and ntfy-password were created outside Terraform — reference as data sources
data "google_secret_manager_secret" "ntfy_token" {
  secret_id = "ntfy-token"
  project   = var.project_id
}

# Shared secrets owned by the platform state (~/src/infra) — referenced read-only.
# Do NOT convert to resources: two states owning the same secret_id fails with
# "already exists". (asana-api-key is also platform-owned but inbox no longer
# reads it, so it is not referenced here.)
data "google_secret_manager_secret" "shared" {
  for_each = toset([
    "grafana-otlp-endpoint",
    "grafana-otlp-token",
    "webhook-label-token",
  ])
  secret_id = each.key
  project   = var.project_id
}

